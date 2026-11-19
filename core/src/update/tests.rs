use super::*;

#[test]
fn trial_confirms_only_after_health() -> Result<()> {
    let mut state = State::initial();
    state.arm(Slot::A)?;
    assert_eq!(state.pending, Some(Slot::B));
    assert!(state.arm(Slot::A).is_err());
    state.pending = None; // Boot loader consumes the trial before loading its kernel.
    assert_eq!(state.confirmed, Slot::A);
    state.confirm(Slot::B)?;
    assert_eq!(state.previous, Some(Slot::A));
    assert_eq!(state.confirmed, Slot::B);
    Ok(())
}

#[test]
fn second_watchdog_reset_restores_previous_once_per_boot() -> Result<()> {
    let mut state = State::initial();
    state.confirm(Slot::B)?;
    assert!(!state.assess(Slot::B, "boot-1", true)?);
    assert!(!state.assess(Slot::B, "boot-1", true)?);
    assert!(state.assess(Slot::B, "boot-2", true)?);
    assert_eq!(state.confirmed, Slot::A);
    assert_eq!(state.previous, None);
    Ok(())
}

#[test]
fn environment_roundtrips_and_rejects_bad_slots() -> Result<()> {
    let state = State::initial();
    let bytes = state.encode()?;
    assert_eq!(bytes.len(), 1024);
    assert_eq!(State::parse(&bytes)?, state);
    let mut bad = bytes;
    let i = bad.windows(11).position(|v| v == b"confirmed=A").unwrap();
    bad[i + 10] = b'X';
    assert!(State::parse(&bad).is_err());
    Ok(())
}

#[test]
fn checksum_failure_preserves_running_slot_and_selection() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let root = temp.path();
    let layout = Layout::offline(root)?;
    let state = State::initial();
    layout.save(&state)?;
    std::fs::write(root.join("A.img"), b"active")?;
    std::fs::write(root.join("B.img"), vec![0; 128])?;
    let payload = root.join("new.img");
    std::fs::write(&payload, b"hello")?;
    let url = format!("file://{}", payload.display());
    assert!(layout.install(Slot::A, &url, &"0".repeat(64)).is_err());
    assert_eq!(std::fs::read(root.join("A.img"))?, b"active");
    assert_eq!(layout.load()?, state);
    layout.install(
        Slot::A,
        &url,
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    )?;
    assert_eq!(layout.load()?.pending, Some(Slot::B));
    Ok(())
}

#[test]
fn health_requires_a_live_pid_for_each_registered_service() -> Result<()> {
    use std::os::unix::net::UnixListener;
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("bus");
    let listener = UnixListener::bind(&path)?;
    let server = thread::spawn(move || -> std::io::Result<()> {
        for response in [
            r#"{"ok":true,"services":[{"pid":null}]}"#,
            r#"{"ok":true,"services":[{"pid":15}]}"#,
        ] {
            let (mut socket, _) = listener.accept()?;
            let mut request = String::new();
            BufReader::new(&socket).read_line(&mut request)?;
            assert_eq!(request, "{\"op\":\"snapshot\"}\n");
            writeln!(socket, "{response}")?;
        }
        Ok(())
    });
    assert!(!healthy(&path)?);
    assert!(healthy(&path)?);
    server.join().unwrap()?;
    Ok(())
}
