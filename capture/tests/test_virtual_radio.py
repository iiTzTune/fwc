"""Observe a software radio through the production socket filter and core bus."""

import json
import socket
import sqlite3
import subprocess
import threading
import time
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

import pytest
from fwc.capture.radio import select_management
from fwc.capture.worker import Publisher, Settings, run
from test_observation import packet


class VirtualRadio:
    def __init__(self):
        self.tuned = []
        self.stop = threading.Event()
        self.started = None

    @contextmanager
    def listen(self, channel):
        self.tuned.append(channel)
        source, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        with source, receiver:
            select_management(receiver)
            self.started = time.monotonic()
            if channel == 1:
                for kind in (0x08, 0xB0, 0x80, 0x10, 0x80):
                    source.send(packet(kind))
            else:
                self.stop.set()
            yield receiver


def test_kernel_filter():
    source, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    with source, receiver:
        select_management(receiver)
        for kind in (0x08, 0x88, 0xB0, 0x40, 0x80):
            source.send(packet(kind))
        receiver.settimeout(0.5)
        assert receiver.recv(4096) == packet(0x80)


def test_virtual_radio_bus(tmp_path):
    binary = Path(__file__).resolve().parents[2] / "target/debug/fwc-core"
    if not binary.exists():
        pytest.skip("build fwc-core to run the bus integration")
    config = tmp_path / "core.json"
    config.write_text("{}")
    path = tmp_path / "bus.sock"
    radio = VirtualRadio()
    settings = Settings(
        "phy0",
        "fwcmon0",
        str(tmp_path / "records"),
        channels=(1, 6),
        dwell_ms=500,
        max_bytes=128,
    )
    with ExitStack() as stack:
        core = subprocess.Popen([binary, tmp_path, config])
        stack.callback(core.wait, 5)
        stack.callback(core.terminate)
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        topics = ["network.seen", "signal.stats", "association.event"]
        readers = []
        for topic in topics:
            sock = stack.enter_context(socket.socket(socket.AF_UNIX))
            sock.settimeout(settings.dwell_ms / 1000)
            sock.connect(str(path))
            stream = stack.enter_context(sock.makefile("rwb", buffering=0))
            stream.write(
                json.dumps({"op": "subscribe", "topic": topic}).encode() + b"\n"
            )
            assert json.loads(stream.readline())["ok"]
            readers.append(stream)
        publisher = Publisher(path)
        stack.callback(publisher.close)
        result = []

        def work():
            run(settings, radio, publisher, radio.stop.is_set)
            result.append(True)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        stack.callback(thread.join, 2)
        stack.callback(radio.stop.set)
        common = {"network", "ssid", "channel", "owned", "time_ns"}
        for topic, reader in zip(topics, readers, strict=True):
            event = json.loads(reader.readline())
            elapsed = time.monotonic() - radio.started
            assert elapsed < settings.dwell_ms / 1000
            assert event["topic"] == topic
            data = event["data"]
            assert data["network"] == "02:00:00:00:00:01"
            assert data["ssid"] == "home" and data["channel"] == 1
            assert type(data["time_ns"]) is int and data["owned"] is False
            if topic == "signal.stats":
                assert event["kind"] == "metric" and data["min_dbm"] == -49
                assert data.keys() == common | {"min_dbm", "max_dbm", "samples"}
            elif topic == "association.event":
                assert event["kind"] == "event" and data["state"] == "joined"
                assert data.keys() == common | {"state", "station"}
            else:
                assert data.keys() == common and event["kind"] == "event"
        thread.join(2)
        assert result and radio.tuned == [1, 6]
        with closing(sqlite3.connect(tmp_path / "state.sqlite")) as db:
            indices = db.execute(
                "SELECT json_extract(data,'$.index') FROM events WHERE topic='capture.rotated' ORDER BY seq"
            ).fetchall()
        assert indices == [(1,), (2,)]
        assert len(list((tmp_path / "records").glob("activity-*.pcap"))) == 2
