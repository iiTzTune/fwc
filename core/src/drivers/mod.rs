pub mod status;
#[cfg(test)]
mod tests;

use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::{path::PathBuf, process::Stdio, time::Duration};
use tokio::{io::AsyncWriteExt, process::Command, time::timeout};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    name: String,
    #[serde(default)]
    options: Map<String, Value>,
}

#[derive(Default, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Registry {
    python: Option<PathBuf>,
    status_size: Option<[u16; 2]>,
    display: Option<Selection>,
    power: Option<Selection>,
    indicator: Option<Selection>,
    input: Option<Selection>,
}

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Operation {
    Render {
        width: u16,
        height: u16,
        pixels: String,
    },
    ReadPower,
    PowerStatus,
    SetIndicator {
        enabled: bool,
    },
    ReadInput,
}

pub trait Display {
    async fn render(&self, width: u16, height: u16, pixels: &str) -> Result<()>;
}
pub trait Power {
    async fn read_mv(&self) -> Result<u32>;
}
pub trait Indicator {
    async fn set(&self, enabled: bool) -> Result<()>;
}
pub trait Input {
    async fn read(&self) -> Result<bool>;
}

fn check_frame(width: u16, height: u16, pixels: &str) -> Result<()> {
    ensure!(
        width > 0 && height > 0,
        "display dimensions must be positive"
    );
    let bytes = usize::from(width).div_ceil(8) * usize::from(height);
    ensure!(
        pixels.len() == bytes * 2 && pixels.bytes().all(|c| c.is_ascii_hexdigit()),
        "pixels must contain one hexadecimal byte per eight pixels on each row"
    );
    Ok(())
}

impl Registry {
    fn roles(&self) -> [(&'static str, Option<&Selection>); 4] {
        [
            ("display", self.display.as_ref()),
            ("power", self.power.as_ref()),
            ("indicator", self.indicator.as_ref()),
            ("input", self.input.as_ref()),
        ]
    }

    pub fn validate(&self) -> Result<()> {
        if let Some(path) = &self.python {
            ensure!(
                path.is_absolute(),
                "drivers.python must be an absolute interpreter path"
            );
        }
        if let Some([width, height]) = self.status_size {
            ensure!(
                (64..=1024).contains(&width) && (32..=1024).contains(&height),
                "status_size must be 64–1024 pixels wide and 32–1024 pixels high"
            );
        }
        for (role, selection) in self.roles() {
            if let Some(spec) = selection {
                ensure!(!spec.name.trim().is_empty(), "{role} driver name is empty");
            }
        }
        Ok(())
    }

    pub fn has_display(&self) -> bool {
        self.display.is_some()
    }

    pub fn diagnostic_display(input: &str) -> Option<Self> {
        let settings: Value = serde_json::from_str(input).ok()?;
        let drivers = settings.get("drivers")?;
        let registry: Registry = serde_json::from_value(json!({
            "python": drivers.get("python"),
            "status_size": drivers.get("status_size"),
            "display": drivers.get("display")?,
        }))
        .ok()?;
        registry.validate().ok()?;
        Some(registry)
    }

    pub async fn show_status(&self, message: &str) -> Result<()> {
        let [width, height] = self.status_size.unwrap_or([122, 250]);
        self.call(
            "display",
            self.display.as_ref(),
            false,
            json!({"width":width, "height":height, "text":message}),
        )
        .await?;
        Ok(())
    }

    pub async fn probe(&self) -> Result<()> {
        for (role, selection) in self.roles() {
            if let Some(spec) = selection {
                self.call(role, Some(spec), true, Value::Null).await?;
            }
        }
        Ok(())
    }

    async fn call(
        &self,
        role: &str,
        selection: Option<&Selection>,
        probe: bool,
        args: Value,
    ) -> Result<Value> {
        let spec = selection.with_context(|| format!("no {role} driver configured"))?;
        let request =
            serde_json::to_vec(&json!({"role":role,"spec":spec,"probe":probe,"args":args}))?;
        let python = self
            .python
            .as_deref()
            .unwrap_or_else(|| std::path::Path::new("python3"));
        let operation = async {
            let mut command = Command::new(python);
            crate::supervisor::terminate_with_core(command.as_std_mut());
            let mut child = command
                .args(["-m", "fwc.drivers.bridge"])
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .kill_on_drop(true)
                .spawn()
                .with_context(|| {
                    format!("could not start driver interpreter {}", python.display())
                })?;
            let mut stdin = child.stdin.take().context("missing driver input pipe")?;
            stdin.write_all(&request).await?;
            drop(stdin);
            let output = child.wait_with_output().await?;
            ensure!(
                output.status.success(),
                "driver bridge exited with {}",
                output.status
            );
            let reply: Value =
                serde_json::from_slice(&output.stdout).context("invalid driver response")?;
            if let Some(error) = reply.get("error").and_then(Value::as_str) {
                bail!("{error}");
            }
            reply.get("value").cloned().context("missing driver result")
        };
        timeout(Duration::from_secs(30), operation)
            .await
            .context("driver timed out")?
            .with_context(|| format!("{role} driver {}", spec.name))
    }

    pub async fn execute(&self, op: Operation) -> Result<Value> {
        match op {
            Operation::Render {
                width,
                height,
                pixels,
            } => {
                self.render(width, height, &pixels).await?;
                Ok(Value::Null)
            }
            Operation::PowerStatus => {
                if self.power.is_none() {
                    Ok(
                        json!({"capability":"none","external_power":null,"charge_state":"unknown","millivolts":null}),
                    )
                } else {
                    self.call(
                        "power",
                        self.power.as_ref(),
                        false,
                        json!({"action":"power_status"}),
                    )
                    .await
                }
            }
            Operation::ReadPower => Ok(json!({"millivolts":self.read_mv().await?})),
            Operation::SetIndicator { enabled } => {
                self.set(enabled).await?;
                Ok(Value::Null)
            }
            Operation::ReadInput => Ok(json!({"pressed":self.read().await?})),
        }
    }
}

impl Display for Registry {
    async fn render(&self, width: u16, height: u16, pixels: &str) -> Result<()> {
        check_frame(width, height, pixels)?;
        self.call(
            "display",
            self.display.as_ref(),
            false,
            json!({"width":width,"height":height,"pixels":pixels}),
        )
        .await?;
        Ok(())
    }
}
impl Power for Registry {
    async fn read_mv(&self) -> Result<u32> {
        let value = self
            .call("power", self.power.as_ref(), false, Value::Null)
            .await?;
        serde_json::from_value(value).context("expected voltage in millivolts")
    }
}
impl Indicator for Registry {
    async fn set(&self, enabled: bool) -> Result<()> {
        self.call(
            "indicator",
            self.indicator.as_ref(),
            false,
            json!({"enabled":enabled}),
        )
        .await?;
        Ok(())
    }
}
impl Input for Registry {
    async fn read(&self) -> Result<bool> {
        let value = self
            .call("input", self.input.as_ref(), false, Value::Null)
            .await?;
        serde_json::from_value(value).context("expected boolean button state")
    }
}
