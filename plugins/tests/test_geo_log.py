import asyncio
import json
import os
import pty
from pathlib import Path

import pytest
from fwc.plugins import geo_log, host
from fwc.plugins.sandbox import invoke

PACKAGE = Path(__file__).resolve().parents[1] / "examples/geo-log"
NOW = 764426119_000_000_000
BODY = "GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W"


def sentence(body):
    value = 0
    for byte in body.encode():
        value ^= byte
    return f"${body}*{value:02X}\r\n".encode()


def test_serial_position_and_historical_export(tmp_path, monkeypatch):
    master, slave = pty.openpty()
    try:
        os.write(master, b"incomplete\r\n" + sentence(BODY))
        monkeypatch.setattr(
            geo_log, "serial_path", lambda device: Path(os.ttyname(slave))
        )
        monkeypatch.setattr(geo_log.time, "time_ns", lambda: NOW)
        settings = {"device": "/dev/ttyUSB0"}
        inputs = geo_log.snapshot(tmp_path, settings)
    finally:
        os.close(master)
        os.close(slave)
    fix = inputs["fix"]
    assert fix == {"latitude": 48.1173, "longitude": 11.516667, "time_ns": NOW}
    network = {"seq": 6, "network": "02:00:00:00:00:01", "ssid": "home", "time_ns": NOW}
    package = host.Manifest.load(PACKAGE)
    result = asyncio.run(
        invoke(
            package,
            dict(
                inputs,
                networks=[network, dict(network, seq=7, time_ns=NOW - 11_000_000_000)],
                settings=settings,
            ),
        )
    )
    assert result["records"] == [
        dict(network, latitude=48.1173, longitude=11.516667, fix_time_ns=NOW)
    ]
    asyncio.run(host.deliver(package, result, tmp_path))
    previous = geo_log.snapshot(tmp_path, {})
    assert previous["fix"] is None and previous["points"] == result["records"]
    repeat = asyncio.run(
        invoke(
            package,
            dict(inputs, networks=[network], points=previous["points"], settings={}),
        )
    )
    assert repeat == result
    no_fix = asyncio.run(
        invoke(package, dict(previous, networks=[network], settings={}))
    )
    assert no_fix == result


@pytest.mark.parametrize(
    "raw,now",
    [
        (sentence(BODY)[:-4] + b"FF\r\n", NOW),
        (sentence(BODY.replace(",A,", ",V,")), NOW),
        (sentence(BODY.replace("4807.038", "9061.0")), NOW),
        (sentence(BODY), NOW + 11_000_000_000),
        (sentence(BODY), NOW - 2_000_000_000),
    ],
)
def test_invalid_or_stale_readings(raw, now):
    assert geo_log.parse_fix(raw, now, 10) is None


@pytest.mark.parametrize(
    "device", ["/etc/passwd", "/dev/serial/by-id/../ttyUSB0", "/dev/pts/0"]
)
def test_device_path_scope(device):
    with pytest.raises(ValueError):
        geo_log.serial_path(device)


def test_unplugged_puck(tmp_path):
    assert geo_log.snapshot(tmp_path, {"device": "/dev/ttyUSB99999"})["fix"] is None


def test_revocation_preserves_history_and_skips_plugin(tmp_path, monkeypatch):
    (tmp_path / "plugin-config.json").write_text(
        json.dumps({"plugins": {"geo-log": {"permissions": ["export.write"]}}})
    )
    (tmp_path / "exports").mkdir()
    path = tmp_path / "exports/geo-log.json"
    path.write_text('[{"seq": 8}]')

    async def report(directory, name, state, detail, error):
        assert state == "unavailable"
        assert error == {"code": "permission_denied", "permission": "networks.read"}
        raise asyncio.CancelledError

    monkeypatch.setattr(host, "report", report)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            host.serve(host.Manifest.load(PACKAGE), tmp_path, 1, asyncio.Semaphore(1))
        )
    assert path.read_text() == '[{"seq": 8}]'
