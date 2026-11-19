"""Exercise two ten-second failures alongside live capture and the companion API."""

import http.client
import itertools
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "plugins/examples"


def get(port, path):
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=2)) as client:
        client.request("GET", "/api/" + path)
        response = client.getresponse()
        assert response.status == 200
        return json.loads(response.read())


def exercise(tmp_path):
    subprocess.run(
        ["cargo", "build", "--locked", "--bin", "fwc-core"], cwd=ROOT, check=True
    )
    manifests = tmp_path / "installed"
    for name in ("activity-widget", "file-export"):
        shutil.copytree(EXAMPLES / name, manifests / name)
    for name, version in [("ten-second-example", 1), ("later-api", 9)]:
        folder = manifests / name
        shutil.copytree(EXAMPLES / "activity-widget", folder)
        spec = json.loads((folder / "manifest.json").read_text())
        spec.update(
            name=name, api_version=version, permissions=["widget.write"], max_restarts=1
        )
        spec["budget"]["seconds"] = 15
        (folder / "manifest.json").write_text(json.dumps(spec))
        (folder / "main.py").write_text(
            "import time\ntime.sleep(10)\nraise RuntimeError('scheduled stop')\n"
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    state = tmp_path / "state"
    capture_config = tmp_path / "capture.json"
    capture_config.write_text('{"phy":"virtual","interface":"sample"}')
    commands = {
        "web": [
            sys.executable,
            str(ROOT / "web/server.py"),
            str(state),
            str(capture_config),
            "--port",
            str(port),
        ],
        "capture": [
            sys.executable,
            str(Path(__file__).with_name("radio_fixture.py")),
            str(state),
        ],
        "plugins": [
            sys.executable,
            "-m",
            "fwc.plugins",
            str(manifests),
            str(state),
            "--port",
            str(port),
        ],
    }
    config = tmp_path / "core.json"
    config.write_text(
        json.dumps(
            {
                "services": [
                    {"name": name, "command": args, "restarts": 2, "window_secs": 60}
                    for name, args in commands.items()
                ]
            }
        )
    )
    with (tmp_path / "service.log").open("w+") as log:
        core = subprocess.Popen(
            [ROOT / "target/debug/fwc-core", state, config], stderr=log
        )
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    plugins = {
                        entry["name"]: entry for entry in get(port, "plugins")["items"]
                    }
                    if (
                        plugins.get("ten-second-example", {}).get("state")
                        == "unavailable"
                    ):
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                log.seek(0)
                raise AssertionError(log.read())
            assert "unsupported plugin API version 9" in plugins["later-api"]["detail"]
            assert "Networks on the first page" in plugins["activity-widget"]["detail"]
            assert (state / "exports/file-export.json").is_file()
            time.sleep(0.6)
            with closing(
                sqlite3.connect(
                    (state / "state.sqlite").as_uri() + "?mode=ro", uri=True
                )
            ) as db:
                events = db.execute(
                    "SELECT seq,topic,data FROM events ORDER BY seq"
                ).fetchall()
            lifecycle = [
                (seq, json.loads(data))
                for seq, topic, data in events
                if topic == "plugin.status"
                and json.loads(data)["name"] == "ten-second-example"
            ]
            failed = [
                seq
                for seq, data in lifecycle
                if data["state"] in {"waiting", "unavailable"}
                and "scheduled stop" in data["detail"]
            ]
            assert len(failed) == 2
            observations = [
                (seq, json.loads(data)["time_ns"])
                for seq, topic, data in events
                if topic == "association.event"
            ]
            for seq in failed:
                assert observations[0][0] < seq < observations[-1][0]
            assert len(observations) > 70
            assert (
                max((b[1] - a[1]) / 1e9 for a, b in itertools.pairwise(observations))
                < 2
            )
            capture = next(
                service
                for service in get(port, "status")["services"]
                if service["name"] == "capture"
            )
            assert capture["starts"] == 1 and capture["pid"]
            print(
                f"Two ten-second plugin failures; {len(observations)} association events; capture starts=1."
            )
        finally:
            core.terminate()
            core.wait(8)


def test_plugin_failures_preserve_capture():
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="fwc-m5-") as root:
        exercise(Path(root))
