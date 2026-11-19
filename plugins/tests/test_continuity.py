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
    folder = manifests / "sample-scanner"
    shutil.copytree(EXAMPLES / "activity-widget", folder)
    spec = json.loads((folder / "manifest.json").read_text())
    spec.update(name=folder.name, permissions=["radio.tx"], interval_seconds=1)
    (folder / "manifest.json").write_text(json.dumps(spec))
    (folder / "main.py").write_text("from fwc_sdk import set_channel\nset_channel(6)\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    state = tmp_path / "state"
    state.mkdir()
    grants = state / "plugin-config.json"
    grants.write_text(
        json.dumps({"plugins": {"sample-scanner": {"permissions": ["radio.tx"]}}})
    )
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
            revoked = False
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    plugins = {
                        entry["name"]: entry for entry in get(port, "plugins")["items"]
                    }
                    if not revoked and "Channel 6" in plugins.get(
                        "sample-scanner", {}
                    ).get("detail", ""):
                        replacement = grants.with_suffix(".tmp")
                        replacement.write_text(json.dumps({"plugins": {}}))
                        replacement.replace(grants)
                        revoked = True
                    if (
                        revoked
                        and plugins.get("sample-scanner", {}).get("state")
                        == "unavailable"
                        and plugins.get("ten-second-example", {}).get("state")
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
            records = [(topic, json.loads(payload)) for _, topic, payload in events]
            assert any(
                topic == "radio/state" and payload["channel"] == 6
                for topic, payload in records
            )
            assert (
                next(
                    payload
                    for topic, payload in reversed(records)
                    if topic == "radio/state"
                )["channel"]
                == 1
            )
            denial = next(
                payload
                for topic, payload in reversed(records)
                if topic == "plugin.status" and payload["name"] == "sample-scanner"
            )
            assert denial["error"] == {
                "code": "permission_denied",
                "permission": "radio.tx",
            }
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
                f"Channel 6 confirmed, radio.tx revoked, channel 1 restored; two ten-second plugin failures; {len(observations)} association events; capture starts=1."
            )
        finally:
            core.terminate()
            core.wait(8)


def test_plugin_failures_preserve_capture():
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="fwc-m5-") as root:
        exercise(Path(root))


def test_radio_topics_verify_supervised_sender():
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="fwc-radio-") as directory:
        check_sender(Path(directory))


def check_sender(tmp_path):
    subprocess.run(
        ["cargo", "build", "--locked", "--bin", "fwc-core"], cwd=ROOT, check=True
    )
    config = tmp_path / "core.json"
    config.write_text("{}")
    state = tmp_path / "state"
    proc = subprocess.Popen([ROOT / "target/debug/fwc-core", state, config])
    try:
        end = time.monotonic() + 4
        address = state / "bus.sock"
        while not address.exists() and time.monotonic() < end:
            time.sleep(0.02)
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(address))
            with client.makefile("rwb", buffering=0) as stream:
                for topic in ["radio/command", "radio/state", "radio/result"]:
                    for op in ["publish", "metric"]:
                        msg = {"op": op, "topic": topic, "data": {"plugin": "sample"}}
                        if op == "publish":
                            msg["id"] = "one"
                        stream.write(json.dumps(msg).encode() + b"\n")
                        assert not json.loads(stream.readline())["ok"]
                stream.write(b'{"op":"subscribe","topic":"radio/command"}\n')
                assert not json.loads(stream.readline())["ok"]
    finally:
        proc.terminate()
        proc.wait(5)
