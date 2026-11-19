import asyncio
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from fwc.capture.control import Commands, Control
from fwc.capture.worker import Settings, run
from fwc.plugins import host, smart_sweep
from fwc.plugins.sandbox import invoke

PACKAGE = Path(__file__).resolve().parents[1] / "examples/smart-sweep"


def density(tmp_path, monkeypatch):
    now = time.time_ns()
    ap1, ap2 = "02:00:00:00:00:01", "02:00:00:00:00:02"
    rows = [
        (1, ap1, 1, "joined", -90),
        (2, ap1, 1, "joined", -5),
        (2, ap2, 6, "joined", -3),
        (2, ap1, 1, "left", -2),
        (3, ap2, 6, "joined", -6),
        (3, ap2, 6, "joined", -7),
        (4, ap2, 6, "joined", -2),
        (4, ap2, 6, "left", -1),
        (5, ap2, 6, "joined", 90),
    ]
    with closing(sqlite3.connect(tmp_path / "state.sqlite")) as db:
        db.execute(
            "CREATE TABLE events(seq INTEGER PRIMARY KEY, topic TEXT, data TEXT)"
        )
        for index, ap, channel, state, age in rows:
            event = {
                "network": ap,
                "station": f"02:00:00:01:00:{index:02x}",
                "channel": channel,
                "state": state,
                "time_ns": now + age * 10**9,
            }
            db.execute(
                "INSERT INTO events(topic,data) VALUES('association.event',?)",
                (json.dumps(event),),
            )
        db.commit()
    monkeypatch.setattr(time, "time_ns", lambda: now)
    return smart_sweep.density_snapshot(tmp_path, {})


def test_window_moves_with_observation_time(tmp_path, monkeypatch):
    assert density(tmp_path, monkeypatch) == {"6": 2}
    now = time.time_ns()
    monkeypatch.setattr(time, "time_ns", lambda: now + 200 * 10**9)
    assert smart_sweep.density_snapshot(tmp_path, {}) == {}


@pytest.mark.parametrize("setting", [{"window_seconds": 0}, {"window_seconds": True}])
def test_invalid_window(tmp_path, setting):
    with pytest.raises(ValueError, match="window_seconds"):
        smart_sweep.density_snapshot(tmp_path, setting)


@pytest.mark.parametrize(
    "settings",
    [
        {"min_dwell_ms": True},
        {"min_dwell_ms": 1000, "max_dwell_ms": 30},
        {"step_ms": 0},
        {"unknown": 3},
    ],
)
def test_rejects_invalid_dwell(settings):
    with pytest.raises(ValueError):
        asyncio.run(
            invoke(
                host.Manifest.load(PACKAGE),
                {"settings": settings, "client_density": {}},
            )
        )


@pytest.mark.parametrize("revoked", ["radio.tx", "radio.config"])
def test_worker_applies_dwell_plan_then_recovers(tmp_path, monkeypatch, revoked):
    package = host.Manifest.load(PACKAGE)
    config = tmp_path / "plugin-config.json"

    def grant(rights):
        config.write_text(
            json.dumps({"plugins": {package.name: {"permissions": sorted(rights)}}})
        )

    grant(package.permissions)
    snapshot = {"settings": {}, "client_density": density(tmp_path, monkeypatch)}
    result = asyncio.run(invoke(package, snapshot))
    commands = Commands(tmp_path / "bus.sock")
    commands.connected.set()
    defaults = Settings(
        "phy0", "sample", str(tmp_path / "pcaps"), channels=(1, 6), dwell_ms=1000
    )
    control = Control(tmp_path, defaults, commands)
    states, errors, tuned = [], [], []

    class Publisher:
        def publish(self, topic, data):
            if topic == "radio/state":
                assert not data["tx_enabled"]
                states.append((data["channel"], data["dwell_ms"]))
            if topic == "radio/result" and not data["ok"]:
                errors.append(data["error"])

    def submit():
        commands.pending.put_nowait(
            {
                "id": str(time.monotonic_ns()),
                "data": {
                    "plugin": package.name,
                    "command": result["command"],
                    "expires": time.monotonic() + 5,
                },
            }
        )

    class Radio:
        @contextmanager
        def listen(self, channel):
            tuned.append(channel)
            yield self

        def settimeout(self, timeout):
            pass

        def recv(self, size):
            if len(tuned) == 2:
                grant(package.permissions - {revoked})
                submit()
            raise TimeoutError

    submit()
    run(defaults, Radio(), Publisher(), lambda: len(tuned) == 4, control=control)
    assert states == [(1, 250), (6, 400), (1, 1000), (6, 1000)]
    assert errors == [{"code": "permission_denied", "permission": revoked}]
