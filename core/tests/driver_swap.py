"""Exercise the installed registry through a running core and its local bus."""

import json
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from fwc.drivers.bridge import status_frame
from PIL import Image


def until(check):
    deadline = time.monotonic() + 10
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the core")
        time.sleep(0.02)


@contextmanager
def daemon(root, config):
    with subprocess.Popen(["target/debug/fwc-core", root, config]) as process:
        try:
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                raise


def call(root, action, **args):
    until((root / "bus.sock").is_socket)

    def attempt():
        nonlocal data
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(5)
            sock.connect(str(root / "bus.sock"))
            request = {"op": "driver", "request": {"action": action, **args}}
            sock.sendall(json.dumps(request).encode() + b"\n")
            reply = json.loads(sock.makefile("rb").readline())
            if reply.get("message") == "driver busy":
                return False
            assert reply["ok"], reply
            data = reply["data"]
            return True

    data = None
    until(attempt)
    return data


def wait_for_frame(root, message):
    expected = status_frame(122, 250, message)

    def matches():
        with Image.open(root / "frame.png") as frame:
            return frame.size == (122, 250) and frame.tobytes() == expected

    until(matches)


def exercise(root):
    config = root / "config.json"
    settings = {
        "python": sys.executable,
        "display": {"name": "dummy", "options": {"output": str(root / "frame.png")}},
        "indicator": {"name": "dummy"},
        "input": {"name": "dummy", "options": {"pressed": True}},
        "power": {"name": "dummy", "options": {"millivolts": 3800}},
    }

    def save():
        config.write_text(json.dumps({"drivers": settings}))

    save()
    with daemon(root, config):
        assert call(root, "read_power") == {"millivolts": 3800}
        assert call(root, "read_input") == {"pressed": True}
        assert call(root, "set_indicator", enabled=True) is None
        assert call(root, "render", width=8, height=1, pixels="a5") is None
        assert (root / "frame.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

        capture_status = root / "capture-status.txt"
        capture_status.write_text("Capture settings need attention: dwell_ms")
        wait_for_frame(root, capture_status.read_text())
        capture_status.unlink()
        wait_for_frame(root, "Ready")

    (root / "in_voltage0_raw").write_text("1500\n")
    (root / "in_voltage_scale").write_text("1.2\n")
    settings["power"] = {"name": "missing"}
    save()
    with daemon(root, config):
        until(lambda: "is missing" in (root / "status.txt").read_text())
        wait_for_frame(root, (root / "status.txt").read_text())
        settings["power"] = {
            "name": "iio-voltage",
            "options": {"device": str(root), "channel": 0, "divider": 2.0},
        }
        save()
        assert call(root, "read_power") == {"millivolts": 3600}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        exercise(Path(tmp))
    print(
        "Core selected dummy and IIO packages by configuration; all four HAL roles responded."
    )
