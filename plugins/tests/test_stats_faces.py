import asyncio
import json
import runpy
import sys
from pathlib import Path

import pytest
from fwc.drivers.bridge import dispatch
from fwc.plugins import host, sdk
from fwc.plugins.sandbox import invoke
from PIL import Image

PACKAGE = Path(__file__).resolve().parents[1] / "examples/stats-faces"
SECOND = 1_000_000_000


def observation(channel, stamp):
    return {"channel": channel, "time_ns": stamp}


def test_epochs_count_only_current_observations(monkeypatch):
    monkeypatch.setitem(sys.modules, "fwc_sdk", sdk)
    face = runpy.run_path(str(PACKAGE / "main.py"))["face"]
    networks = [
        observation(1, 299 * SECOND),
        observation(1, 300 * SECOND),
        observation(1, 325 * SECOND),
        observation(6, 341 * SECOND),
        observation(11, 361 * SECOND),
    ]
    settings = {"epoch_seconds": 60}
    assert face(networks, settings, 350 * SECOND) == (
        "(^_^) Busy | 00:05:00 UTC | 3 networks, 2 channels | Score 4"
    )
    assert face(networks, settings, 360 * SECOND) == (
        "(-_-) Resting | 00:06:00 UTC | 0 networks, 0 channels | Score 0"
    )
    assert "Curious" in face(networks, settings, 361 * SECOND)


@pytest.mark.parametrize(
    "settings", [{"epoch_seconds": 0}, {"epoch_seconds": True}, {"other": 1}]
)
def test_settings_are_bounded(settings):
    with pytest.raises(ValueError, match="epoch_seconds"):
        asyncio.run(
            invoke(host.Manifest.load(PACKAGE), {"networks": [], "settings": settings})
        )


def test_snapshot_reaches_dummy_display(tmp_path, monkeypatch):
    package = host.Manifest.load(PACKAGE)
    result = asyncio.run(invoke(package, {"networks": [], "settings": {}}))
    assert result["action"] == "display" and "Resting" in result["text"]
    output = tmp_path / "face.png"

    async def bus(directory, message):
        operation = message["request"].copy()
        assert message["op"] == "driver" and operation.pop("action") == "render"
        dispatch(
            {
                "role": "display",
                "spec": {"name": "dummy", "options": {"output": str(output)}},
                "probe": False,
                "args": operation,
            }
        )

    monkeypatch.setattr(host, "bus", bus)
    asyncio.run(host.deliver(package, result, tmp_path))
    with Image.open(output) as image:
        assert image.size == (122, 250)
        assert image.convert("L").getextrema() == (0, 255)


@pytest.mark.parametrize("revoked", ["display.write", "networks.read"])
def test_missing_grant_stops_before_inputs_or_render(tmp_path, monkeypatch, revoked):
    package = host.Manifest.load(PACKAGE)
    rights = sorted(package.permissions - {revoked})
    config = {"plugins": {"stats-faces": {"permissions": rights}}}
    (tmp_path / "plugin-config.json").write_text(json.dumps(config))

    async def report(directory, name, state, detail, error):
        assert name == "stats-faces" and state == "unavailable"
        assert error == {"code": "permission_denied", "permission": revoked}
        raise asyncio.CancelledError

    monkeypatch.setattr(host, "report", report)
    monkeypatch.setattr(
        host, "snapshot", lambda *args: pytest.fail("read while disabled")
    )
    monkeypatch.setattr(
        host, "show", lambda *args: pytest.fail("rendered while disabled")
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(host.serve(package, tmp_path, 1, asyncio.Semaphore(1)))
