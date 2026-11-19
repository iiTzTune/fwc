use crate::state::Log;
use anyhow::{Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::{HashSet, VecDeque},
    fs::File,
    io::Write,
    os::unix::process::CommandExt,
    path::PathBuf,
    process::{Child, Command},
    time::{Duration, Instant},
};

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    #[serde(default)]
    services: Vec<Service>,
    pub watchdog: Option<PathBuf>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Service {
    name: String,
    command: Vec<String>,
    restarts: u32,
    window_secs: u32,
}

impl Config {
    pub fn parse(input: &str) -> Result<Self> {
        let c: Self = serde_json::from_str(input)?;
        let mut names = HashSet::new();
        for s in &c.services {
            ensure!(
                !s.name.trim().is_empty() && names.insert(&s.name),
                "service names must be distinct and nonempty"
            );
            ensure!(
                s.command.first().is_some_and(|v| !v.trim().is_empty())
                    && s.command.iter().all(|v| !v.contains('\0')),
                "{} needs a valid command",
                s.name
            );
            ensure!(
                s.restarts > 0 && s.window_secs > 0,
                "{} needs a positive restart budget and window",
                s.name
            );
        }
        ensure!(
            c.watchdog.as_ref().is_none_or(|p| p.is_absolute()),
            "watchdog path must be absolute"
        );
        Ok(c)
    }
}

#[derive(Default)]
pub struct Budget(VecDeque<Instant>);
impl Budget {
    pub fn allow(&mut self, now: Instant, max: u32, window: Duration) -> bool {
        while self
            .0
            .front()
            .is_some_and(|t| now.duration_since(*t) >= window)
        {
            self.0.pop_front();
        }
        if self.0.len() >= max as usize {
            return false;
        }
        self.0.push_back(now);
        true
    }
}
struct Worker {
    spec: Service,
    child: Option<Child>,
    budget: Budget,
    starts: u64,
}
pub struct Supervisor {
    workers: Vec<Worker>,
    watchdog: Option<File>,
}

impl Supervisor {
    pub fn new(config: Config) -> Result<Self> {
        let watchdog = config
            .watchdog
            .map(|p| File::options().write(true).open(p))
            .transpose()?;
        Ok(Self {
            workers: config
                .services
                .into_iter()
                .map(|spec| Worker {
                    spec,
                    child: None,
                    budget: Budget::default(),
                    starts: 0,
                })
                .collect(),
            watchdog,
        })
    }
    pub fn tick(&mut self, log: &mut Log) -> Result<()> {
        for w in &mut self.workers {
            if let Some(child) = &mut w.child {
                let Some(status) = child.try_wait()? else {
                    continue;
                };
                log.write(
                    "service_exit",
                    json!({"name":w.spec.name,"status":status.to_string()}),
                )?;
                w.child = None;
            }
            if w.starts > 0
                && !w.budget.allow(
                    Instant::now(),
                    w.spec.restarts,
                    Duration::from_secs(w.spec.window_secs.into()),
                )
            {
                continue;
            }
            w.starts += 1;
            let mut cmd = Command::new(&w.spec.command[0]);
            cmd.args(&w.spec.command[1..]);
            let parent = std::process::id();
            // Only async-signal-safe system calls run between fork and exec.
            unsafe {
                cmd.pre_exec(move || {
                    if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    if libc::getppid() as u32 != parent {
                        libc::_exit(1)
                    }
                    Ok(())
                });
            }
            match cmd.spawn() {
                Ok(child) => {
                    w.child = Some(child);
                    log.write("service_start", json!({"name":w.spec.name}))?;
                }
                Err(e) => log.write(
                    "service_error",
                    json!({"name":w.spec.name,"message":e.to_string()}),
                )?,
            }
        }
        if let Some(device) = &mut self.watchdog {
            device.write_all(&[0])?;
        }
        Ok(())
    }
    pub fn snapshot(&self) -> Value {
        Value::Array(self.workers.iter().map(|w|json!({"name":w.spec.name,"starts":w.starts,"pid":w.child.as_ref().map(Child::id)})).collect())
    }
    pub fn stop(&mut self) -> Result<()> {
        for w in &mut self.workers {
            if let Some(mut c) = w.child.take() {
                c.kill()?;
                c.wait()?;
            }
        }
        if let Some(mut device) = self.watchdog.take() {
            device.write_all(b"V")?;
        }
        Ok(())
    }
}
