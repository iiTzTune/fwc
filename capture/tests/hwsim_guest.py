"""Exercise the production worker and core inside a disposable Linux radio guest."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path


def subscribe(stack, address, topic):
    sock = stack.enter_context(socket.socket(socket.AF_UNIX))
    sock.settimeout(8)
    sock.connect(str(address))
    stream = stack.enter_context(sock.makefile("rwb", buffering=0))
    stream.write(json.dumps({"op": "subscribe", "topic": topic}).encode() + b"\n")
    assert json.loads(stream.readline())["ok"]
    return stream


def receive(streams, topic, start, dwell):
    message = json.loads(streams[topic].readline())
    elapsed = time.monotonic() - start
    data = message["data"]
    assert message["topic"] == topic and data["owned"]
    assert data["channel"] == 1 and data["ssid"] == "fwc-virtual"
    assert elapsed < dwell, (topic, elapsed, dwell)
    print(f"{topic} received after {elapsed:.3f} s", flush=True)
    return data


def until(condition, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if condition():
            return
        if process.poll() is not None:
            raise RuntimeError(f"{process.args[0]} stopped with {process.returncode}")
        time.sleep(0.05)
    raise TimeoutError(condition)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def ring(directory):
    files = {}
    for path in directory.glob("*.pcap"):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue  # Rotation can remove the oldest file during enumeration.
        files[path.name] = (stat.st_size, stat.st_mtime_ns)
    return files


def exercise(directory, core_binary):
    dwell = 2
    capture = directory / "pcap"
    capture.mkdir()
    state = directory / "state"
    bus = state / "bus.sock"
    core_config = directory / "core.json"
    core_config.write_text("{}")
    config = directory / "worker.json"
    config.write_text(
        json.dumps(
            {
                "phy": "phy1",
                "interface": "fwcmon0",
                "directory": str(capture),
                "channels": [1, 6],
                "dwell_ms": dwell * 1000,
                "file_count": 3,
                "max_bytes": 350,
                "allow_ssids": ["fwc-virtual"],
            }
        )
    )
    with ExitStack() as stack:
        subprocess.run(
            ["mount", "-t", "tmpfs", "-o", "size=1m", "none", capture], check=True
        )
        stack.callback(subprocess.run, ["umount", capture], check=True)
        core = subprocess.Popen([core_binary, state, core_config])
        stack.callback(stop, core)
        until(bus.exists, core)
        streams = {
            key: subscribe(stack, bus, key)
            for key in ("network.seen", "association.event", "signal.stats")
        }
        subprocess.run(["ip", "link", "set", "wlan2", "up"], check=True)
        start = time.monotonic()
        worker = subprocess.Popen([sys.executable, "-m", "fwc.capture", config, bus])
        stack.callback(stop, worker)
        receive(streams, "network.seen", start, dwell)
        signal = receive(streams, "signal.stats", start, dwell)
        assert signal["samples"] >= 1 and signal["min_dbm"] <= signal["max_dbm"]
        start = time.monotonic()
        subprocess.run(
            ["iw", "dev", "wlan2", "connect", "fwc-virtual", "2412"], check=True
        )
        stack.callback(subprocess.run, ["iw", "dev", "wlan2", "disconnect"])
        assert receive(streams, "association.event", start, dwell)["state"] == "joined"
        until(lambda: len(ring(capture)) == 3, worker)
        until(
            lambda: (
                "channel 6 "
                in subprocess.check_output(["iw", "dev", "fwcmon0", "info"], text=True)
            ),
            worker,
        )
        fill = capture / "fill"
        usage = shutil.disk_usage(capture)
        with fill.open("wb") as f:
            os.posix_fallocate(f.fileno(), 0, int(0.92 * usage.total) - usage.used)
        time.sleep(0.4)
        before = ring(capture)
        time.sleep(2 * dwell + 0.5)
        assert ring(capture) == before
        assert 0 < len(before) <= 3 and all(size <= 350 for size, _ in before.values())
        fill.unlink()
        until(lambda: ring(capture) != before, worker)
        print("Rotation, watermark pause and resume passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("core", help="path to the fwc-core executable")
    args = parser.parse_args()
    for phy in ("phy0", "phy1", "phy2"):
        driver = Path("/sys/class/ieee80211", phy, "device/driver")
        if driver.resolve().name != "mac80211_hwsim":
            raise SystemExit(f"{phy} must be a simulated radio")
    if Path("/sys/class/net/wlan2/phy80211").resolve().name != "phy2":
        raise SystemExit("wlan2 must belong to the third simulated radio")
    with tempfile.TemporaryDirectory() as tmp:
        exercise(Path(tmp), args.core)
