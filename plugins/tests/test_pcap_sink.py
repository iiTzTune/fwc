import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from fwc.capture.contract import PermissionDenied
from fwc.capture.storage import Ring
from fwc.plugins import host, pcap_sink
from fwc.plugins.sandbox import invoke

PACKAGE = Path(__file__).resolve().parents[1] / "examples/pcap-sink"
NAME = "activity-00000000000000000001.pcap"


@pytest.fixture
def captures(tmp_path):
    source = tmp_path / "captures"
    (tmp_path / "capture.json").write_text(json.dumps({"directory": str(source)}))
    ring = Ring(source, 128, 3)
    for i in range(3):
        ring.append(bytes([i]) * 60, time.time_ns())
    ring.close()
    return source


def test_snapshot_survives_rotation_and_expires(tmp_path, captures, monkeypatch):
    settings = {"window_seconds": 5}
    (tmp_path / "plugin-config.json").write_text(
        json.dumps({"plugins": {"pcap-sink": {"settings": settings}}})
    )
    original = (captures / NAME).read_bytes()
    pcap_sink.sync(tmp_path, settings)
    (captures / NAME).unlink()
    assert pcap_sink.read_file(tmp_path, NAME) == original
    assert len(pcap_sink.list_files(tmp_path)) == 3
    future = time.time_ns() + 6_000_000_000
    monkeypatch.setattr(time, "time_ns", lambda: future)
    assert pcap_sink.list_files(tmp_path) == []
    with pytest.raises(FileNotFoundError):
        pcap_sink.read_file(tmp_path, NAME)
    pcap_sink.sync(tmp_path, settings)
    assert not list((tmp_path / "retained-pcap").iterdir())


def test_partial_records_and_links_excluded(tmp_path, captures):
    first = captures / NAME
    data = first.read_bytes()
    first.write_bytes(data[:-2])
    second = sorted(captures.glob("*.pcap"))[1]
    second.unlink()
    second.symlink_to(tmp_path / "capture.json")
    pcap_sink.sync(tmp_path, {})
    assert len(pcap_sink.list_files(tmp_path)) == 1
    with pytest.raises(FileNotFoundError):
        pcap_sink.read_file(tmp_path, "../capture.json")
    (tmp_path / "retained-pcap" / NAME).symlink_to(tmp_path / "capture.json")
    with pytest.raises(FileNotFoundError):
        pcap_sink.read_file(tmp_path, NAME)


def test_budget_keeps_newest_and_pauses_at_watermark(tmp_path, captures, monkeypatch):
    for i, path in enumerate(sorted(captures.iterdir())):
        stamp = time.time_ns() - (5 - i) * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))
    monkeypatch.setattr(pcap_sink, "MAX_FILES", 2)
    monkeypatch.setattr(pcap_sink, "available", lambda fd, amount: False)
    pcap_sink.sync(tmp_path, {})
    assert not pcap_sink.list_files(tmp_path)
    monkeypatch.setattr(pcap_sink, "available", lambda fd, amount: True)
    pcap_sink.sync(tmp_path, {})
    names = [f["name"] for f in pcap_sink.list_files(tmp_path)]
    assert names == sorted([p.name for p in captures.iterdir()], reverse=True)[:2]


def test_denial_before_invocation_or_download(tmp_path, monkeypatch):
    pkg = host.Manifest.load(PACKAGE)
    assert asyncio.run(invoke(pkg, {})) == {"action": "pcap_sink"}
    (tmp_path / "plugin-config.json").write_text(
        '{"plugins":{"pcap-sink":{"permissions":[]}}}'
    )

    async def report(directory, name, state, detail, error=None):
        assert (name, state, error) == (
            "pcap-sink",
            "unavailable",
            {"code": "permission_denied", "permission": "export.write"},
        )
        raise asyncio.CancelledError

    monkeypatch.setattr(host, "report", report)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(host.serve(pkg, tmp_path, 1, asyncio.Semaphore(1)))
    for fn, args in [(pcap_sink.list_files, ()), (pcap_sink.read_file, (NAME,))]:
        with pytest.raises(PermissionDenied, match="export.write"):
            fn(tmp_path, *args)
    assert not (tmp_path / "retained-pcap").exists()
