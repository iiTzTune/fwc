import asyncio
import json
from pathlib import Path

import pytest
from fwc.plugins.host import Manifest, deliver
from fwc.plugins.sandbox import invoke

BASE = {
    "name": "sample",
    "api_version": 1,
    "permissions": ["widget.write"],
    "interval_seconds": 1,
    "max_restarts": 1,
    "budget": {"seconds": 2, "cpu_seconds": 1, "memory_mib": 48},
}


def package(path, code, **changes):
    (path / "manifest.json").write_text(json.dumps(BASE | changes))
    (path / "main.py").write_text(code)
    return Manifest.load(path)


@pytest.mark.parametrize(
    "fields",
    [
        {"permissions": ["unknown"]},
        {"api_version": 2},
        {"api_version": True},
        {"budget": {"seconds": 999, "cpu_seconds": 2, "memory_mib": 20}},
        {"name": "../other"},
        {"unexpected": 2},
        {"interval_seconds": 0},
    ],
)
def test_manifest_rejects_invalid_fields(tmp_path, fields):
    with pytest.raises(ValueError):
        package(tmp_path, "", **fields)


def test_isolated_sdk_and_resource_limits(tmp_path):
    secret = tmp_path / "bus.sock"
    secret.write_text("local")
    pkg = package(
        tmp_path,
        f"""
import os, socket, resource
from fwc_sdk import read_event, widget
assert not os.path.exists({str(secret)!r})
assert not os.path.exists("/work/bus.sock")
assert not os.environ.get("HOME")
assert resource.getrlimit(resource.RLIMIT_AS)[1] == 48*1024*1024
try:
    socket.socket()
except PermissionError:
    pass
else:
    raise AssertionError("socket created")
try:
    os.fork()
except PermissionError:
    pass
else:
    raise AssertionError("child created")
widget(read_event()["status"]["committed"].__str__())
""",
    )
    assert asyncio.run(invoke(pkg, {"status": {"committed": 7}})) == {
        "action": "widget",
        "text": "7",
    }


def test_runtime_timeout(tmp_path):
    pkg = package(
        tmp_path,
        "import time; time.sleep(3)",
        budget={"seconds": 1, "cpu_seconds": 1, "memory_mib": 48},
    )
    with pytest.raises(ValueError, match="time"):
        asyncio.run(invoke(pkg, {}))


def test_export_is_bounded_and_scoped(tmp_path):
    pkg = package(tmp_path, "", permissions=["export.write"])
    asyncio.run(
        deliver(pkg, {"action": "export", "records": [{"ssid": "home"}]}, tmp_path)
    )
    assert json.loads((tmp_path / "exports/sample.json").read_text()) == [
        {"ssid": "home"}
    ]
    with pytest.raises(ValueError):
        asyncio.run(deliver(pkg, {"action": "display", "text": "test"}, tmp_path))
    with pytest.raises(ValueError):
        asyncio.run(
            deliver(
                pkg, {"action": "export", "records": [], "path": "/tmp/x"}, tmp_path
            )
        )


def test_shipped_examples(tmp_path):
    source = Path(__file__).resolve().parents[1] / "examples"
    for path, action in [
        ("activity-widget", "widget"),
        ("file-export", "export"),
        ("status-display", "display"),
    ]:
        pkg = Manifest.load(source / path)
        reply = asyncio.run(
            invoke(pkg, {"status": {"committed": 1, "services": []}, "networks": []})
        )
        assert reply["action"] == action
