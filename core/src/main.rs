mod bus;
#[cfg(test)]
#[path = "tests.rs"]
mod core;
mod drivers;
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
    let mut displayed = None;
    let mut next_display_attempt = time::Instant::now();
    let mut config = loop {
        let result = fs::read_to_string(&args[1])
            .context("reading configuration")
            .and_then(|s| Config::parse(&s));
        let result = match result {
            Ok(config) => config.drivers.probe().await.map(|()| config),
            Err(error) => Err(error),
        };
        match result {
            Ok(c) => break c,
            Err(e) => {
                let message = format!("Configuration needs attention: {e:#}");
                if last_error != message {
                    log.write("config_error", json!({"message":message}))?;
                    fs::write(&status, &message)?;
                    eprintln!("{message}");
                    next_display_attempt = time::Instant::now();
                    last_error = message.clone();
                }
                if displayed.as_ref() != Some(&message)
                    && time::Instant::now() >= next_display_attempt
                {
                    let registry = fs::read_to_string(&args[1])
                        .ok()
                        .and_then(|text| drivers::Registry::diagnostic_display(&text));
                    if let Some(registry) = registry {
                        match registry.show_status(&message).await {
                            Ok(()) => displayed = Some(message),
                            Err(error) => {
                                log.write("display_error", json!({"message":format!("{error:#}")}))?
                            }
                        }
                    }
                    next_display_attempt = time::Instant::now() + Duration::from_secs(30);
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
    let monitor_display = config.drivers.has_display();
    let bus = Bus::new(store, std::mem::take(&mut config.drivers));
    if monitor_display {
        tokio::task::spawn_local(drivers::status::watch(
            bus.borrow().drivers(),
            dir.join("capture-status.txt"),
            displayed,
        ));
    }
    let mut supervisor = Supervisor::new(config)?;
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
    supervisor.stop().await?;
    fs::remove_file(path)?;
    log.write("stopped", json!({}))
}
