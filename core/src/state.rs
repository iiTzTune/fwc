use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, params};
use serde_json::{Value, json};
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub struct Store {
    db: Connection,
    _lease: File,
}

impl Store {
    pub fn open(dir: &Path) -> Result<Self> {
        let lease = File::options()
            .create(true)
            .truncate(false)
            .write(true)
            .open(dir.join("writer.lock"))?;
        lease
            .try_lock()
            .context("another core owns this directory")?;
        let mut db = Connection::open(dir.join("state.sqlite"))?;
        let mode: String = db.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
        ensure!(mode == "wal", "WAL mode is required");
        db.execute_batch("PRAGMA synchronous=FULL;")?;
        let tx = db.transaction()?;
        let version: i64 = tx.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        anyhow::ensure!(
            (0..=2).contains(&version),
            "unsupported state schema {version}"
        );
        if version == 0 {
            tx.execute_batch(include_str!("../migrations/001.sql"))?;
        }
        if version < 2 {
            tx.execute_batch(include_str!("../migrations/002.sql"))?;
        }
        tx.commit()?;
        Ok(Self { db, _lease: lease })
    }

    pub fn record(&self, id: &str, topic: &str, data: &Value) -> Result<(i64, bool)> {
        let encoded = serde_json::to_string(data)?;
        let added = self.db.execute(
            "INSERT INTO events(id,topic,data) VALUES (?1,?2,?3) ON CONFLICT(id) DO NOTHING",
            params![id, topic, encoded],
        )?;
        if added == 1 {
            return Ok((self.db.last_insert_rowid(), true));
        }
        let seq = self
            .db
            .query_row(
                "SELECT seq FROM events WHERE id=?1 AND topic=?2 AND data=?3",
                params![id, topic, encoded],
                |r| r.get(0),
            )
            .context("event id already has different content")?;
        Ok((seq, false))
    }
}

pub struct Log {
    path: PathBuf,
    file: File,
    size: u64,
    limit: u64,
}
impl Log {
    pub fn open(dir: &Path, limit: u64) -> Result<Self> {
        let path = dir.join("core.jsonl");
        let file = File::options().append(true).create(true).open(&path)?;
        let size = file.metadata()?.len();
        Ok(Self {
            path,
            file,
            size,
            limit,
        })
    }
    pub fn write(&mut self, kind: &str, data: Value) -> Result<()> {
        let mut bytes = serde_json::to_vec(
            &json!({"time":SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),"kind":kind,"data":data}),
        )?;
        bytes.push(b'\n');
        if self.size > 0 && self.size + bytes.len() as u64 > self.limit {
            fs::rename(&self.path, self.path.with_extension("jsonl.1"))?;
            self.file = File::options().create(true).append(true).open(&self.path)?;
            self.size = 0;
        }
        self.file.write_all(&bytes)?;
        self.size += bytes.len() as u64;
        Ok(())
    }
}
