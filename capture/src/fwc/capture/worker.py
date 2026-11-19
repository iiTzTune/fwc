"""Worker settings, acknowledged local bus publishing, and channel scheduling."""

import json
import re
import socket
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, fields
from itertools import cycle
from pathlib import Path

from .observation import Observer, decode, peer_element
from .storage import Ring


@dataclass(frozen=True)
class Settings:
    phy: str
    interface: str
    directory: str = "captures"
    channels: tuple = (1, 6, 11)
    dwell_ms: int = 1000
    max_bytes: int = 4 * 1024 * 1024
    file_count: int = 8
    allow_ssids: tuple = ()
    deny_ssids: tuple = ()

    @classmethod
    def parse(cls, obj):
        if not isinstance(obj, dict) or set(obj) - {f.name for f in fields(cls)}:
            raise ValueError("unknown capture settings or invalid object")
        if not {"interface", "phy"} <= obj.keys():
            raise ValueError("interface and phy are required")
        config = cls(**obj)
        for name, pattern in (
            ("interface", r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}"),
            ("phy", r"phy[0-9]+"),
        ):
            value = getattr(config, name)
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError(f"invalid {name}")
        if (
            not isinstance(config.directory, str)
            or not config.directory
            or "\0" in config.directory
        ):
            raise ValueError("directory must be a path")
        for name, lo, hi in (
            ("dwell_ms", 10, 60000),
            ("max_bytes", 128, 2**31),
            ("file_count", 1, 1024),
        ):
            value = getattr(config, name)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{name} must be between {lo} and {hi}")
        if (
            not isinstance(config.channels, (list, tuple))
            or not config.channels
            or not all(type(c) is int and 1 <= c <= 233 for c in config.channels)
        ):
            raise ValueError("channels must be a nonempty list of channel numbers")
        for names in (config.allow_ssids, config.deny_ssids):
            if not isinstance(names, (list, tuple)) or not all(
                isinstance(name, str) and len(name.encode()) <= 32 for name in names
            ):
                raise ValueError(
                    "SSID lists must contain names of at most 32 UTF-8 bytes"
                )
        return config


class Publisher:
    def __init__(self, path):
        self.path = path
        self.sock = self.stream = None

    def close(self):
        if self.stream:
            self.stream.close()
        if self.sock:
            self.sock.close()
        self.stream = self.sock = None

    def publish(self, topic, data):
        metric = topic == "signal.stats"
        request = {
            "op": "metric" if metric else "publish",
            "topic": topic,
            "data": data,
        }
        if not metric:
            request["id"] = uuid.uuid4().hex
        encoded = json.dumps(request).encode() + b"\n"
        while True:
            try:
                if self.sock is None:
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(3)
                    self.sock.connect(str(self.path))
                    self.stream = self.sock.makefile("rb")
                self.sock.sendall(encoded)
                raw = self.stream.readline(16385)
                if not raw.endswith(b"\n"):
                    raise OSError("incomplete bus reply")
                reply = json.loads(raw)
                if not reply["ok"]:
                    raise ValueError(f"bus rejected {topic}: {reply.get('message')}")
                return
            except OSError:
                self.close()
                if metric:
                    return
                time.sleep(0.25)


def run(settings, radio, publisher, stopping=lambda: False, usage=None, control=None):
    observer = Observer(settings.allow_ssids, settings.deny_ssids)
    ring_args = () if usage is None else (usage,)
    ring = Ring(
        Path(settings.directory), settings.max_bytes, settings.file_count, *ring_args
    )
    try:
        plan = settings.channels
        scanner = cycle(plan)
        while True:
            channel = next(scanner)
            if stopping():
                return
            if not ring.space(128):
                ring.close()
                time.sleep(0.25)
                continue
            dwell = settings.dwell_ms
            applied = None
            if control:
                applied = control.prepare(publisher)
                changes = control.overrides.get("radio.config", (None, {}))[1]
                current_plan = changes.get("channels", settings.channels)
                if plan != current_plan:
                    plan = current_plan
                    scanner = cycle(plan)
                    channel = next(scanner)
                channel = control.overrides.get("radio.tx", (None, channel))[1]
                dwell = changes.get("dwell_by_channel", {}).get(
                    str(channel), changes.get("dwell_ms", dwell)
                )
            with ExitStack() as stack:
                try:
                    sock = stack.enter_context(radio.listen(channel))
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    if not control or not control.overrides:
                        raise
                    control.tune_failed(publisher, applied, error)
                    continue
                deadline = time.monotonic() + dwell / 1000
                if control:
                    max_bytes = changes.get("max_bytes", settings.max_bytes)
                    count = changes.get("file_count", settings.file_count)
                    if (max_bytes, count) != (ring.max_bytes, ring.count):
                        ring.close()
                        ring = Ring(
                            Path(settings.directory), max_bytes, count, *ring_args
                        )
                    control.announce(publisher, channel, dwell, applied)
                read_peers = control is not None and control.reads_peers()
                peer_samples = set()
                while not stopping() and (remaining := deadline - time.monotonic()) > 0:
                    sock.settimeout(remaining)
                    try:
                        packet = sock.recv(4096)
                        item = decode(packet)
                    except TimeoutError:
                        break
                    if item is None:
                        continue
                    observed = time.time_ns()
                    accepted = observer.accepts(item)
                    if accepted and not ring.append(item.record, observed):
                        time.sleep(0.25)
                        break
                    if accepted and read_peers and len(peer_samples) < 32:
                        element = peer_element(packet, item)
                        if element and element not in peer_samples:
                            peer_samples.add(element)
                            publisher.publish("peer.element", {"element": element})
                    for topic, payload in observer.observe(item, channel, observed):
                        publisher.publish(topic, payload)
                for topic, payload in observer.finish_dwell():
                    publisher.publish(topic, payload)
    finally:
        ring.close()


def read_settings(path, state_directory):
    diagnostic = state_directory / "capture-status.txt"
    previous = ""
    while True:
        try:
            result = Settings.parse(json.loads(path.read_text()))
            diagnostic.unlink(missing_ok=True)
            return result
        except (OSError, ValueError) as error:
            message = f"Capture settings need attention: {error}"
            if message != previous:
                print(
                    json.dumps({"kind": "capture_config_error", "message": message}),
                    file=sys.stderr,
                    flush=True,
                )
                diagnostic.write_text(message + "\n")
                previous = message
            time.sleep(2)
