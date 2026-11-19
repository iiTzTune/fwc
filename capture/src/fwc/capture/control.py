"""Listen for local requests and apply approved settings at a dwell boundary."""

import json
import queue
import socket
import threading
import time
from contextlib import suppress
from dataclasses import asdict

from .contract import (
    PermissionDenied,
    command_permission,
    manifest_directory,
    plugin_config,
    radio_grants,
    require,
)


class Commands:
    def __init__(self, bus):
        self.bus = bus
        self.pending = queue.Queue(maxsize=32)
        self.connected = threading.Event()
        self.closed = threading.Event()
        self.socket = None
        self.thread = threading.Thread(target=self.listen, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.closed.set()
        sock = self.socket
        if sock:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
        self.thread.join(timeout=0.5)

    def listen(self):
        while not self.closed.is_set():
            with (
                suppress(OSError, ValueError, KeyError),
                socket.socket(socket.AF_UNIX) as sock,
            ):
                self.socket = sock
                if self.closed.is_set():
                    return
                sock.connect(str(self.bus))
                sock.sendall(b'{"op":"subscribe","topic":"radio/command"}\n')
                with sock.makefile("rb") as reader:
                    if json.loads(reader.readline(16385))["ok"]:
                        self.connected.set()
                        while not self.closed.is_set():
                            record = json.loads(reader.readline(16385))
                            while not self.closed.is_set():
                                try:
                                    self.pending.put(record, timeout=0.2)
                                    break
                                except queue.Full:
                                    continue
            self.connected.clear()
            self.closed.wait(0.25)


class Control:
    def __init__(self, directory, defaults, commands):
        self.directory = directory
        self.defaults = defaults
        self.commands = commands
        self.overrides = {}
        self.previous_state = None

    def grants(self, plugin):
        try:
            return plugin_config(self.directory, plugin)[0]
        except (TypeError, ValueError, OSError, RecursionError):
            return set()

    def reads_peers(self):
        try:
            spec = manifest_directory(self.directory) / "peers-mesh/manifest.json"
            return spec.is_file() and {"status.read", "export.write"} <= self.grants(
                "peers-mesh"
            )
        except (ValueError, OSError):
            return False

    def prepare(self, publisher):
        for permission, (plugin, _) in list(self.overrides.items()):
            if not radio_grants(permission) <= self.grants(plugin):
                del self.overrides[permission]

        # Process one request per dwell so every successful reply has a tuning result.
        try:
            event = self.commands.pending.get_nowait()
        except queue.Empty:
            return None
        request = event["data"]
        result = {"request_id": event["id"], "plugin": request.get("plugin")}
        try:
            plugin = request["plugin"]
            if not isinstance(plugin, str):
                raise TypeError("invalid plugin name")
            permission = command_permission(request["command"])
            require(self.grants(plugin), radio_grants(permission))
            if time.monotonic() >= request["expires"]:
                raise ValueError("radio request expired")
            cmd = request["command"]
            if permission == "radio.tx":
                value = cmd["channel"]
            else:
                previous = self.overrides.get(permission)
                changes = previous[1] if previous and previous[0] == plugin else {}
                value = changes | cmd["changes"]
                common = {k: v for k, v in value.items() if k != "dwell_by_channel"}
                base = {k: v for k, v in asdict(self.defaults).items() if v is not None}
                type(self.defaults).parse(base | common)
            self.overrides[permission] = (plugin, value)
            return result, permission
        except PermissionDenied as exc:
            error = exc.payload
        except (TypeError, KeyError, ValueError) as exc:
            error = {"code": "invalid_command", "detail": str(exc)}
        publisher.publish("radio/result", result | {"ok": False, "error": error})
        return None

    def announce(self, publisher, channel, dwell, applied):
        state = {
            "channel": channel,
            "mode": "monitor",
            "tx_enabled": False,
            "dwell_ms": dwell,
        }
        if self.commands.connected.is_set() and (
            applied or state != self.previous_state
        ):
            publisher.publish("radio/state", state)
            self.previous_state = state
        if applied:
            result, _ = applied
            publisher.publish("radio/result", result | {"ok": True, "state": state})

    def tune_failed(self, publisher, applied, error):
        if applied:
            result, permission = applied
            self.overrides.pop(permission, None)
            publisher.publish(
                "radio/result",
                result
                | {
                    "ok": False,
                    "error": {"code": "tune_failed", "detail": str(error)[:120]},
                },
            )
        else:
            self.overrides.clear()
