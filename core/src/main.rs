mod bus;
#[cfg(test)]
#[path = "tests.rs"]
mod core;
mod state;
mod supervisor;
use anyhow::{Context, Result, ensure};
use bus::Bus;
use serde_json::json;
use state::{Log, Store};
use std::{
    env, fs,
    os::unix::fs::{DirBuilderExt, FileTypeExt},
    path::Path,
    time::Duration,
};
use supervisor::{Config, Supervisor};
use tokio::{
    net::UnixListener,
    signal::unix::{SignalKind, signal},
    time,
};

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    tokio::task::LocalSet::new().run_until(run()).await
}

async fn run() -> Result<()> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    ensure!(
        args.len() == 2,
        "usage: fwc-core STATE_DIRECTORY CONFIG_JSON"
    );
    let dir = Path::new(&args[0]);
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(dir)?;
    let store = Store::open(dir)?;
    let mut log = Log::open(dir, 1024 * 1024)?;
    let status = dir.join("status.txt");
    let mut last_error = String::new();
    let config = loop {
        let result = fs::read_to_string(&args[1])
            .context("reading configuration")
            .and_then(|s| Config::parse(&s));
        match result {
            Ok(c) => break c,
            Err(e) => {
                let message = format!("Configuration needs attention: {e:#}");
                if last_error != message {
                    log.write("config_error", json!({"message":message}))?;
                    fs::write(&status, &message)?;
                    eprintln!("{message}");
                    last_error = message;
                }
                time::sleep(Duration::from_secs(2)).await;
            }
        }
    };
    let path = dir.join("bus.sock");
    if let Ok(meta) = fs::symlink_metadata(&path) {
        ensure!(meta.file_type().is_socket(), "bus path is occupied");
        fs::remove_file(&path)?;
    }
    let socket = UnixListener::bind(&path)?;
    let mut term = signal(SignalKind::terminate())?;
    let mut interrupt = signal(SignalKind::interrupt())?;
    let mut supervisor = Supervisor::new(config)?;
    let bus = Bus::new(store);
    let mut tick = time::interval(Duration::from_millis(250));
    tick.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
    log.write("ready", json!({}))?;
    fs::write(&status, "Ready\n")?;
    loop {
        tokio::select! {
            _=term.recv()=>break,
            _=interrupt.recv()=>break,
            _=tick.tick()=>{supervisor.tick(&mut log)?;bus.borrow_mut().services=supervisor.snapshot();}
            client=socket.accept()=>{let (s,_)=client?;Bus::accept(&bus,s);}
        }
    }
    supervisor.stop()?;
    fs::remove_file(path)?;
    log.write("stopped", json!({}))
}
