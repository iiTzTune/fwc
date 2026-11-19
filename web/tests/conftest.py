import http.client
import json
import socket
import subprocess
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web"))


def free_port():
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(port, method, route, body=None, headers=None):
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=4)) as client:
        client.request(
            method,
            route,
            json.dumps(body) if body is not None else None,
            {"Content-Type": "application/json", **(headers or {})},
        )
        response = client.getresponse()
        raw = response.read()
        if response.getheader("Content-Type", "").startswith("application/json"):
            raw = json.loads(raw)
        return response.status, raw


def publish(state, topic, data, key):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(3)
        sock.connect(str(state / "bus.sock"))
        sock.sendall(
            json.dumps(
                {"op": "publish", "topic": topic, "data": data, "id": key}
            ).encode()
            + b"\n"
        )
        assert json.loads(sock.makefile("rb").readline())["ok"]


@pytest.fixture(scope="session")
def core_binary():
    subprocess.run(
        ["cargo", "build", "--locked", "-p", "fwc-core"], cwd=ROOT, check=True
    )
    return ROOT / "target/debug/fwc-core"


@contextmanager
def running_device(tmp_path, core_binary):
    port = free_port()
    short = tempfile.TemporaryDirectory(prefix="fwc-web-", dir="/tmp")
    state = Path(short.name)
    config = tmp_path / "capture.json"
    config.write_text(
        json.dumps(
            {
                "phy": "phy0",
                "interface": "fwcmon0",
                "directory": "/var/lib/fwc/captures",
            }
        )
    )
    core_config = tmp_path / "core.json"
    core_config.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "web",
                        "command": [
                            sys.executable,
                            str(ROOT / "web/server.py"),
                            str(state),
                            str(config),
                            "--port",
                            str(port),
                        ],
                        "restarts": 2,
                        "window_secs": 60,
                    }
                ]
            }
        )
    )
    with subprocess.Popen([core_binary, state, core_config]) as core:
        try:

            def ready(_):
                try:
                    return request(port, "GET", "/api/status")[0] == 200
                except OSError:
                    return False

            try:
                WebDriverWait(None, 45).until(ready)
            except TimeoutException as exc:
                raise TimeoutError(
                    f"Device startup timed out; title=<no page> URL=http://127.0.0.1:{port}"
                ) from exc
            yield port, state, config
        finally:
            core.terminate()
            core.wait(5)
            short.cleanup()


@pytest.fixture
def device(tmp_path, core_binary):
    with running_device(tmp_path, core_binary) as started:
        yield started
