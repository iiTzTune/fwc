//! Update selection shared with the UEFI loader in image/boot/grub.cfg.
#[cfg(test)]
mod tests;
use anyhow::{Context, Result, bail, ensure};
use serde_json::Value;
use std::{
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    os::unix::{
        fs::{FileTypeExt, OpenOptionsExt},
        net::UnixStream,
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

#[derive(Debug, PartialEq, Eq, Copy, Clone)]
enum Slot {
    A,
    B,
}
impl Slot {
    fn parse(value: &str) -> Result<Self> {
        match value {
            "A" => Ok(Self::A),
            "B" => Ok(Self::B),
            _ => bail!("unknown slot {value:?}"),
        }
    }
    fn name(self) -> &'static str {
        match self {
            Self::A => "A",
            Self::B => "B",
        }
    }
    fn other(self) -> Self {
        match self {
            Self::A => Self::B,
            Self::B => Self::A,
        }
    }
}
#[derive(Debug, PartialEq, Eq, Clone)]
struct State {
    confirmed: Slot,
    previous: Option<Slot>,
    pending: Option<Slot>,
    resets: u8,
    boot_id: String,
}
const HEADER: &str = "# GRUB Environment Block\n";
impl State {
    fn initial() -> Self {
        Self {
            confirmed: Slot::A,
            previous: None,
            pending: None,
            resets: 0,
            boot_id: String::new(),
        }
    }
    fn parse(bytes: &[u8]) -> Result<Self> {
        ensure!(bytes.len() == 1024, "boot selection must be 1024 bytes");
        let text = std::str::from_utf8(bytes)?
            .strip_prefix(HEADER)
            .context("invalid boot selection header")?;
        let mut fields = std::collections::HashMap::new();
        for line in text
            .lines()
            .filter(|s| !s.starts_with('#') && !s.is_empty())
        {
            let (k, v) = line.split_once('=').context("invalid selection field")?;
            ensure!(fields.insert(k, v).is_none(), "duplicate selection field");
        }
        let confirmed = Slot::parse(fields.get("confirmed").context("missing confirmed slot")?)?;
        let slot = |key| {
            fields
                .get(key)
                .filter(|s| !s.is_empty())
                .map(|v| Slot::parse(v))
                .transpose()
        };
        let state = Self {
            confirmed,
            previous: slot("previous")?,
            pending: slot("pending")?,
            resets: fields.get("resets").unwrap_or(&"0").parse()?,
            boot_id: fields.get("boot_id").unwrap_or(&"").to_string(),
        };
        ensure!(
            state.previous != Some(confirmed) && state.pending != Some(confirmed),
            "overlapping boot selections"
        );
        Ok(state)
    }
    fn encode(&self) -> Result<Vec<u8>> {
        ensure!(
            self.boot_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-'),
            "invalid boot id"
        );
        let mut bytes = format!(
            "{HEADER}confirmed={}\nprevious={}\npending={}\nresets={}\nboot_id={}\n",
            self.confirmed.name(),
            self.previous.map(Slot::name).unwrap_or(""),
            self.pending.map(Slot::name).unwrap_or(""),
            self.resets,
            self.boot_id
        )
        .into_bytes();
        ensure!(bytes.len() < 1024, "boot selection too large");
        bytes.resize(1024, b'#');
        Ok(bytes)
    }
    fn arm(&mut self, running: Slot) -> Result<()> {
        ensure!(
            self.confirmed == running && self.pending.is_none(),
            "an update is already pending or running"
        );
        self.pending = Some(running.other());
        Ok(())
    }
    fn confirm(&mut self, running: Slot) -> Result<()> {
        ensure!(
            self.pending.is_none(),
            "trial has not been consumed by the loader"
        );
        if self.confirmed != running {
            self.previous = Some(self.confirmed);
            self.confirmed = running;
            self.resets = 0;
        }
        Ok(())
    }
    fn assess(&mut self, running: Slot, boot_id: &str, watchdog: bool) -> Result<bool> {
        ensure!(
            self.confirmed == running,
            "running slot differs from confirmed slot"
        );
        if self.boot_id != boot_id {
            self.boot_id = boot_id.to_string();
            self.resets = if watchdog {
                self.resets.saturating_add(1)
            } else {
                0
            };
        }
        if self.resets >= 2
            && let Some(previous) = self.previous.take()
        {
            self.confirmed = previous;
            self.pending = None;
            self.resets = 0;
            return Ok(true);
        }
        Ok(false)
    }
}

struct Layout {
    env: PathBuf,
    slots: [PathBuf; 2],
    offline: bool,
}
impl Layout {
    fn device() -> Self {
        Self {
            env: "/boot/efi/EFI/FWC/grubenv".into(),
            slots: [
                "/dev/disk/by-partlabel/FWC-A".into(),
                "/dev/disk/by-partlabel/FWC-B".into(),
            ],
            offline: false,
        }
    }
    fn offline(root: &Path) -> Result<Self> {
        ensure!(root.is_dir(), "expected an offline image directory");
        Ok(Self {
            env: root.join("grubenv"),
            slots: [root.join("A.img"), root.join("B.img")],
            offline: true,
        })
    }
    fn load(&self) -> Result<State> {
        State::parse(&fs::read(&self.env).context("read boot selection")?)
    }
    fn save(&self, state: &State) -> Result<()> {
        Self::write_env(&self.env, state)
    }
    fn write_env(path: &Path, state: &State) -> Result<()> {
        let temp = path.with_extension("new");
        let bytes = state.encode()?;
        let mut f = OpenOptions::new()
            .write(true)
            .truncate(true)
            .create(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&temp)?;
        f.write_all(&bytes)?;
        f.sync_all()?;
        fs::rename(temp, path)?;
        File::open(path.parent().context("missing selection directory")?)?.sync_all()?;
        Ok(())
    }
    fn lock(&self) -> Result<File> {
        let f = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(self.env.with_extension("lock"))?;
        f.try_lock().context("another update operation is active")?;
        Ok(f)
    }
    fn install(&self, running: Slot, url: &str, digest: &str) -> Result<()> {
        ensure!(
            digest.len() == 64 && digest.bytes().all(|b| b.is_ascii_hexdigit()),
            "expected a SHA-256 digest"
        );
        ensure!(
            url.starts_with("https://") || url.starts_with("file:///"),
            "expected HTTPS or an absolute file URL"
        );
        let _lock = self.lock()?;
        let mut state = self.load()?;
        let mut next = state.clone();
        next.arm(running)?;
        let target = running.other();
        let path = &self.slots[usize::from(target == Slot::B)];
        let flags = if self.offline {
            libc::O_NOFOLLOW
        } else {
            libc::O_EXCL
        };
        let mut dest = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(flags)
            .open(path)
            .with_context(|| format!("open inactive slot {}", target.name()))?;
        let kind = dest.metadata()?.file_type();
        ensure!(
            if self.offline {
                kind.is_file()
            } else {
                kind.is_block_device()
            },
            "incorrect slot storage type"
        );
        let capacity = dest.seek(SeekFrom::End(0))?;
        dest.rewind()?;
        // Keep an independent confirmed selection while rewriting the inactive slot.
        Self::write_env(
            &self.env.with_extension("safe"),
            &State {
                confirmed: running,
                ..State::initial()
            },
        )?;
        // Overwriting the previous slot retires it before any of its bytes change.
        if state.previous.take().is_some() {
            self.save(&state)?
        }
        next.previous = None;
        let mut child = Command::new("curl")
            .args([
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--proto",
                "=https,file",
                "--proto-redir",
                "=https",
                "--connect-timeout",
                "15",
                "--speed-time",
                "30",
                "--speed-limit",
                "1",
                "--",
                url,
            ])
            .stdout(Stdio::piped())
            .spawn()
            .context("start download")?;
        let mut stream = child.stdout.take().context("missing download stream")?;
        let copied = std::io::copy(&mut (&mut stream).take(capacity), &mut dest);
        let mut extra = [0u8];
        let complete = copied.is_ok() && stream.read(&mut extra).is_ok_and(|n| n == 0);
        drop(stream);
        if !complete {
            let _ = child.kill();
        }
        let status = child.wait()?;
        let length = copied?;
        ensure!(
            complete && status.success() && length > 0,
            "incomplete, empty or oversized slot download"
        );
        dest.sync_all()?;
        dest.rewind()?;
        let mut check = Command::new("sha256sum")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()?;
        let mut input = check.stdin.take().context("missing checksum input")?;
        let hashed = std::io::copy(&mut dest.take(length), &mut input);
        drop(input);
        let result = check.wait_with_output()?;
        ensure!(
            hashed? == length && result.status.success(),
            "slot readback failed"
        );
        let output = std::str::from_utf8(&result.stdout)?;
        ensure!(
            output
                .split_whitespace()
                .next()
                .is_some_and(|s| s.eq_ignore_ascii_case(digest)),
            "slot checksum mismatch"
        );
        self.save(&next)?;
        println!(
            "Verified {length} bytes. Slot {} will run once on the next boot.",
            target.name()
        );
        Ok(())
    }
}

fn kernel_selection(cmdline: &str) -> Result<(Slot, bool)> {
    let get = |key: &str| cmdline.split_whitespace().find_map(|s| s.strip_prefix(key));
    let slot = Slot::parse(get("fwc.slot=").context("missing boot slot")?)?;
    let trial = match get("fwc.trial=") {
        Some("1") => true,
        Some("0") => false,
        _ => bail!("missing or invalid trial flag"),
    };
    Ok((slot, trial))
}

fn healthy(socket: &Path) -> Result<bool> {
    let mut stream = UnixStream::connect(socket)?;
    stream.set_read_timeout(Some(Duration::from_secs(1)))?;
    stream.set_write_timeout(Some(Duration::from_secs(1)))?;
    stream.write_all(b"{\"op\":\"snapshot\"}\n")?;
    let mut reply = String::new();
    BufReader::new(stream.take(16384)).read_line(&mut reply)?;
    let value: Value = serde_json::from_str(&reply)?;
    Ok(value["ok"] == true
        && value["services"].as_array().is_some_and(|entries| {
            entries
                .iter()
                .all(|s| s["pid"].as_u64().is_some_and(|pid| pid > 0))
        }))
}

fn boot_health(layout: &Layout, socket: &Path, running: Slot, trial: bool) -> Result<()> {
    let _lock = layout.lock()?;
    let mut state = layout.load()?;
    let boot_id = fs::read_to_string("/proc/sys/kernel/random/boot_id")?;
    let watchdog = match fs::read_to_string("/sys/class/watchdog/watchdog0/bootstatus") {
        Ok(s) => s.trim().parse::<u32>()? & 32 != 0,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
        Err(e) => return Err(e.into()),
    };
    if !trial {
        let original = state.clone();
        let rollback = state.assess(running, boot_id.trim(), watchdog)?;
        if state != original {
            layout.save(&state)?;
        }
        if rollback {
            std::process::exit(75)
        }
        return Ok(());
    }
    let end = Instant::now() + Duration::from_secs(45);
    let mut consecutive = 0;
    while Instant::now() < end {
        consecutive = if healthy(socket).unwrap_or(false) {
            consecutive + 1
        } else {
            0
        };
        if consecutive >= 4 {
            state.confirm(running)?;
            state.boot_id = boot_id.trim().to_string();
            layout.save(&state)?;
            println!("Slot {} passed the core health check.", running.name());
            return Ok(());
        }
        thread::sleep(Duration::from_secs(1));
    }
    eprintln!("Trial failed the core health check. The confirmed slot will resume.");
    std::process::exit(75)
}

pub fn run(args: Vec<String>) -> Result<()> {
    match args.as_slice() {
        [cmd, path] if cmd == "init-env" => {
            let dest = Path::new(path);
            let mut f = File::options().create_new(true).write(true).open(dest)?;
            f.write_all(&State::initial().encode()?)?;
            f.sync_all()?;
        }
        [cmd, dir, url, sha] if cmd == "stage-files" => {
            let layout = Layout::offline(Path::new(dir))?;
            layout.install(layout.load()?.confirmed, url, sha)?;
        }
        [cmd, url, sha] if cmd == "install" => {
            let (running, trial) = kernel_selection(&fs::read_to_string("/proc/cmdline")?)?;
            ensure!(!trial, "restart into a confirmed slot before updating");
            Layout::device().install(running, url, sha)?;
        }
        [cmd, socket] if cmd == "boot-health" => {
            let (running, trial) = kernel_selection(&fs::read_to_string("/proc/cmdline")?)?;
            boot_health(&Layout::device(), Path::new(socket), running, trial)?;
        }
        _ => bail!(
            "usage: fwc-update install URL SHA256 | boot-health BUS_SOCKET | init-env FILE | stage-files DIRECTORY URL SHA256"
        ),
    }
    Ok(())
}
