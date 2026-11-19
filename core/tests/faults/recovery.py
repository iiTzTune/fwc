"""Run from the workspace root after cargo build."""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def line(message):
    return json.dumps(message).encode() + b"\n"


def event(number):
    return {"op": "publish", "topic": "activity", "id": str(number), "data": number}


def request(stream, message):
    stream.write(line(message))
    reply = json.loads(stream.readline())
    assert reply["ok"], reply
    return reply


def worker(root):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(str(root / "bus.sock"))
        stream = sock.makefile("rwb", buffering=0)
        if not (root / "partial").exists():
            for number in range(8):
                request(stream, event(number))
            sock.sendall(line(event(8))[:-1])
            (root / "partial").write_text(str(os.getpid()))
        else:
            for number in range(9, 16):
                request(stream, event(number))
            (root / "resumed").write_text(str(os.getpid()))
        signal.pause()


def until(check):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {check}")


def exercise(root):
    cfg = root / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "sample",
                        "command": [sys.executable, __file__, str(root)],
                        "restarts": 2,
                        "window_secs": 10,
                    }
                ]
            }
        )
    )
    binary = Path("target/debug/fwc-core").resolve()

    def start():
        return subprocess.Popen([binary, root, cfg])

    core = start()
    try:
        until((root / "partial").exists)
        os.kill(int((root / "partial").read_text()), signal.SIGKILL)
        until((root / "resumed").exists)
        core.kill()
        core.wait(timeout=5)
        # The restarted worker repeats a batch whose acknowledgments preceded the stop.
        (root / "resumed").unlink()
        core = start()
        until((root / "resumed").exists)
        with sqlite3.connect(f"file:{root}/state.sqlite?mode=ro", uri=True) as db:
            assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            ids = [
                int(row[0]) for row in db.execute("SELECT id FROM events ORDER BY seq")
            ]
            assert ids == [*range(8), *range(9, 16)], ids
        with socket.socket(socket.AF_UNIX) as sub, socket.socket(socket.AF_UNIX) as pub:
            for sock in [sub, pub]:
                sock.settimeout(5)
                sock.connect(str(root / "bus.sock"))
            reader = sub.makefile("rwb", buffering=0)
            writer = pub.makefile("rwb", buffering=0)
            request(reader, {"op": "subscribe", "topic": "activity"})
            ack = request(writer, event(16))
            assert json.loads(reader.readline())["seq"] == ack["seq"]
            assert request(writer, {"op": "snapshot"})["committed"] == 1
        core.terminate()
        assert core.wait(timeout=5) == 0
    finally:
        if core.poll() is None:
            core.kill()
            core.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        worker(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            exercise(Path(tmp))
        print("One partial event lost. All acknowledged events recovered exactly once.")
