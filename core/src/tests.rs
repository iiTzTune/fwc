use super::*;
use crate::bus::{Frame, Queue, Request};
use crate::supervisor::{Budget, Config};
use serde_json::json;
use std::time::{Duration, Instant};

#[test]
fn restart_window_expires_without_spinning() {
    let now = Instant::now();
    let mut budget = Budget::default();
    assert!(budget.allow(now, 1, Duration::from_secs(5)));
    assert!(!budget.allow(now, 1, Duration::from_secs(5)));
    assert!(budget.allow(now + Duration::from_secs(5), 1, Duration::from_secs(5)));
}

#[test]
fn settings_reject_duplicate_names_and_unknown_fields() {
    let service = json!({"name":"worker","command":["true"],"restarts":1,"window_secs":2});
    assert!(Config::parse(&json!({"services":[service,service]}).to_string()).is_err());
    assert!(Config::parse(r#"{"typo":1}"#).is_err());
}

#[test]
fn protocol_rejects_unknown_operations_and_empty_keys() {
    assert!(Request::parse(br#"{"op":"other"}"#).is_err());
    assert!(Request::parse(br#"{"op":"publish","topic":"a","id":"","data":1}"#).is_err());
}

#[test]
fn event_evicts_metric_without_reordering_events() {
    let mut q = Queue::new(2);
    assert!(q.push(Frame::new(json!(1), false)));
    assert!(q.push(Frame::new(json!(2), true)));
    assert!(q.push(Frame::new(json!(3), false)));
    assert!(q.push(Frame::new(json!(4), true)));
    assert_eq!(q.dropped, 2);
    assert!(!q.push(Frame::new(json!(5), false)));
    assert_eq!(q.items.pop_front().unwrap().value, json!(1));
    assert_eq!(q.items.pop_front().unwrap().value, json!(3));
}

#[test]
fn committed_records_survive_reopen_and_retries() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let store = Store::open(dir.path())?;
    assert!(Store::open(dir.path()).is_err());
    let (seq, fresh) = store.record("one", "observations", &json!({"n":1}))?;
    assert!(fresh);
    drop(store);
    let store = Store::open(dir.path())?;
    assert_eq!(
        store.record("one", "observations", &json!({"n":1}))?,
        (seq, false)
    );
    assert!(store.record("one", "observations", &json!(2)).is_err());
    Ok(())
}

#[tokio::test]
async fn watchdog_receives_feeds_and_orderly_close() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let device = dir.path().join("test-device");
    std::fs::File::create(&device)?;
    let mut supervisor =
        supervisor::Supervisor::new(Config::parse(&json!({"watchdog":device}).to_string())?)?;
    let mut log = Log::open(dir.path(), 32)?;
    supervisor.tick(&mut log)?;
    supervisor.tick(&mut log)?;
    supervisor.stop().await?;
    assert_eq!(std::fs::read(device)?, b"\0\0V");
    Ok(())
}

#[test]
fn log_rotates_whole_json_records() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let mut log = Log::open(dir.path(), 32)?;
    for n in 0..3 {
        log.write("sample", json!(n))?;
    }
    for (name, n) in [("core.jsonl.1", 1), ("core.jsonl", 2)] {
        let entry: serde_json::Value =
            serde_json::from_slice(&std::fs::read(dir.path().join(name))?)?;
        assert_eq!(entry["data"], n);
    }
    Ok(())
}

#[tokio::test]
async fn orderly_shutdown_allows_cleanup_and_reaps_workers() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let ready = dir.path().join("ready");
    let stopped = dir.path().join("stopped");
    let stubborn_ready = dir.path().join("stubborn-ready");
    let device = dir.path().join("test-device");
    std::fs::File::create(&device)?;
    let script = r#"trap 'echo stopped > "$2"; exit 0' TERM
        echo ready > "$1"
        while :; do sleep 0.02; done"#;
    let mut supervisor = Supervisor::new(Config::parse(&json!({
        "watchdog":device,
        "services":[
            {"name":"collector","command":["sh","-c",script,"collector",ready,stopped],"window_secs":5,"restarts":1},
            {"name":"stubborn","command":["sh","-c","trap '' TERM; echo ready > \"$1\"; exec sleep 60","stubborn",stubborn_ready],"window_secs":5,"restarts":1}
        ]
    }).to_string())?)?;
    let mut log = Log::open(dir.path(), 1024)?;
    supervisor.tick(&mut log)?;
    let deadline = Instant::now() + Duration::from_secs(2);
    while !ready.exists() || !stubborn_ready.exists() {
        anyhow::ensure!(Instant::now() < deadline, "worker did not start");
        std::thread::sleep(Duration::from_millis(5));
    }
    tokio::time::timeout(Duration::from_secs(5), supervisor.stop()).await??;
    let feeds = std::fs::read(device)?;
    assert!(feeds.len() > 2 && feeds.ends_with(b"V"));
    assert_eq!(std::fs::read_to_string(stopped)?.trim(), "stopped");
    assert_eq!(supervisor.snapshot()[0]["pid"], serde_json::Value::Null);
    Ok(())
}
