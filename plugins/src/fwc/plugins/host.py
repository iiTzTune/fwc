"""Load local observation plugins and mediate version one inputs and results."""

import asyncio
import http.client
import json
import os
import re
import signal
import sqlite3
import stat
import sys
import tempfile
import uuid
from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path

from fwc.capture.contract import (
    LEGACY,
    RADIO,
    PermissionDenied,
    command_permission,
    plugin_config,
    require,
)

from . import radio
from .sandbox import invoke

PERMISSIONS = LEGACY | RADIO
NAME = re.compile(r"[a-z][a-z0-9-]{0,39}")


def bounded(path, limit):
    with os.fdopen(
        os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
    ) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"{path.name} must be a regular file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"{path.name} exceeds its size limit")
        return data


def number(obj, key, low, high):
    value = obj[key]
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{key} must be an integer from {low} through {high}")
    return value


@dataclass(frozen=True)
class Manifest:
    name: str
    permissions: frozenset[str]
    seconds: int
    cpu_seconds: int
    memory_mib: int
    interval_seconds: int
    max_restarts: int
    script: bytes

    @classmethod
    def load(cls, folder):
        spec = json.loads(bounded(folder / "manifest.json", 8192))
        keys = {
            "name",
            "api_version",
            "permissions",
            "budget",
            "interval_seconds",
            "max_restarts",
        }
        if type(spec) is not dict or spec.keys() != keys:
            raise ValueError("invalid manifest fields")
        if not isinstance(spec["name"], str) or not NAME.fullmatch(spec["name"]):
            raise ValueError("invalid plugin name")
        if type(spec["api_version"]) is not int or spec["api_version"] != 1:
            raise ValueError(
                f"unsupported plugin API version {spec['api_version']!r}, expected 1"
            )
        rights = spec["permissions"]
        if not isinstance(rights, list) or not all(
            isinstance(p, str) and p in PERMISSIONS for p in rights
        ):
            raise ValueError("unknown plugin permission")
        budget = spec["budget"]
        if not isinstance(budget, dict) or budget.keys() != {
            "seconds",
            "cpu_seconds",
            "memory_mib",
        }:
            raise ValueError("invalid resource budget")
        return cls(
            spec["name"],
            frozenset(rights),
            number(budget, "seconds", 1, 60),
            number(budget, "cpu_seconds", 1, 10),
            number(budget, "memory_mib", 32, 96),
            number(spec, "interval_seconds", 1, 3600),
            number(spec, "max_restarts", 0, 10),
            bounded(folder / "main.py", 65536),
        )


def snapshot(package, port):
    inputs = {}
    endpoints = [
        ("status.read", "status", "/api/status"),
        ("networks.read", "networks", "/api/networks?limit=16"),
    ]
    for permission, key, path in endpoints:
        if permission in package.permissions:
            with closing(
                http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            ) as client:
                client.request("GET", path)
                response = client.getresponse()
                if response.status != 200:
                    raise OSError(f"companion API returned {response.status}")
                data = json.loads(response.read(65537))
                if key == "status":
                    inputs[key] = {k: data[k] for k in ("committed", "clients")}
                else:
                    inputs[key] = data["items"]
    return inputs


async def bus(directory, message):
    reader, writer = await asyncio.open_unix_connection(
        str(directory / "bus.sock"), limit=16384
    )
    try:
        writer.write(json.dumps(message).encode() + b"\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        if not response["ok"]:
            raise ValueError(response.get("message", "core request failed"))
    finally:
        writer.close()
        await writer.wait_closed()


async def report(directory, name, state, detail, error=None):
    record = {"name": name, "state": state, "detail": detail[:200]}
    if error:
        record["error"] = error
    print(json.dumps(record), file=sys.stderr, flush=True)
    message = {
        "op": "publish",
        "topic": "plugin.status",
        "data": record,
        "id": uuid.uuid4().hex,
    }
    while True:
        try:
            await asyncio.wait_for(bus(directory, message), 3)
            return
        except (OSError, TimeoutError, ValueError):
            await asyncio.sleep(1)


async def show(directory, text):
    from fwc.drivers.bridge import status_frame

    pixels = status_frame(122, 250, text)
    request = {"action": "render", "width": 122, "height": 250, "pixels": pixels.hex()}
    await asyncio.wait_for(bus(directory, {"op": "driver", "request": request}), 31)


async def diagnostic(directory, name, detail):
    await report(directory, name, "unavailable", detail)
    # A headless device keeps the same diagnostic on the dashboard.
    with suppress(OSError, ValueError, TimeoutError):
        await show(directory, f"{name}: {detail[:160]}")


async def deliver(package, result, directory):
    if not isinstance(result, dict) or not isinstance(result.get("action"), str):
        raise TypeError("expected a plugin result object")
    action = result["action"]
    granted, settings = plugin_config(directory, package.name)
    require(granted, package.permissions)
    if action == "pcap_sink" and package.name == "pcap-sink":
        from .pcap_sink import sync

        require(package.permissions, ["export.write"])
        if set(result) != {"action"}:
            raise ValueError("unexpected capture archive settings")
        return await asyncio.to_thread(sync, directory, settings)
    if action == "radio":
        if result.keys() != {"action", "command"}:
            raise ValueError("invalid radio result fields")
        permission = command_permission(result["command"])
        require(package.permissions, [permission])
        state = await radio.request(directory, package.name, result["command"], bus)
        return f"Channel {state['channel']}, dwell {state['dwell_ms']} ms"
    require(package.permissions, [action + ".write"])
    if action in ("display", "widget"):
        if (
            set(result) != {"action", "text"}
            or not isinstance(result["text"], str)
            or not 1 <= len(result["text"]) <= 160
        ):
            raise ValueError("expected a short display string")
        if action == "display":
            await show(directory, result["text"])
        return result["text"]
    if (
        action != "export"
        or set(result) != {"action", "records"}
        or not isinstance(result["records"], list)
    ):
        raise ValueError("expected an export record list")
    payload = json.dumps(result["records"], allow_nan=False).encode()
    if len(payload) > 8192:
        raise ValueError("export exceeds size limit")
    dest = directory / "exports"
    dest.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest, delete=False) as stream:
        temp = Path(stream.name)
    try:
        temp.write_bytes(payload + b"\n")
        temp.replace(dest / (package.name + ".json"))
    finally:
        temp.unlink(missing_ok=True)
    return "Export updated"


async def serve(package, directory, port, capacity):
    failures = 0
    last = None
    previous_display = None

    async def status(state, detail, error=None):
        nonlocal last
        if (state, detail) != last:
            await report(directory, package.name, state, detail, error)
            last = (state, detail)

    while True:
        try:
            granted, settings = plugin_config(directory, package.name)
            require(granted, package.permissions)
        except PermissionDenied as exc:
            await status("unavailable", str(exc), exc.payload)
            await asyncio.sleep(1)
            continue
        except (TypeError, ValueError, OSError, RecursionError) as exc:
            await status("unavailable", str(exc))
            await asyncio.sleep(1)
            continue
        if last is None or last[0] == "unavailable":
            await status("running", "Ready")
        try:
            inputs = await asyncio.to_thread(snapshot, package, port)
            inputs["settings"] = settings
            if package.permissions & RADIO:
                inputs.update(
                    await asyncio.to_thread(
                        radio.radio_snapshot, directory, package.name
                    )
                )
        except (OSError, ValueError, http.client.HTTPException, sqlite3.Error):
            await status("waiting", "Companion API unavailable")
            await asyncio.sleep(2)
            continue
        try:
            async with capacity:
                result = await invoke(package, inputs)
            if previous_display is not None and result == previous_display:
                granted, _ = plugin_config(directory, package.name)
                require(granted, package.permissions)
                detail = previous_display["text"]
            else:
                detail = await deliver(package, result, directory)
                previous_display = result if result["action"] == "display" else None
            await status("running", detail)
            failures = 0
        except PermissionDenied as exc:
            await status("unavailable", str(exc), exc.payload)
            await asyncio.sleep(1)
            continue
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            failures += 1
            state = "unavailable" if failures > package.max_restarts else "waiting"
            await status(state, str(exc)[:160])
            if state == "unavailable":
                return
            await asyncio.sleep(min(2 ** (failures - 1), 30))
            await status("running", "Restarted")
            continue
        await asyncio.sleep(package.interval_seconds)


async def run(manifests, directory, port):
    capacity = asyncio.Semaphore(2)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for action in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(action, stop.set)
    tasks = {}
    last_error = None
    while not stop.is_set():
        try:
            folders = sorted(
                folder
                for folder in manifests.iterdir()
                if not folder.is_symlink() and folder.is_dir()
            )
            if len(folders) > 32:
                raise ValueError("plugin directory exceeds 32 installed packages")
            break
        except (OSError, ValueError) as exc:
            message = str(exc)
            if message != last_error:
                await diagnostic(directory, "plugin-host", message)
                last_error = message
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 2)
    else:
        return
    if last_error:
        await report(directory, "plugin-host", "running", "Directory ready")
    async with asyncio.TaskGroup() as group:
        for folder in folders:
            try:
                package = Manifest.load(folder)
                if package.name != folder.name:
                    raise ValueError("manifest name must match its directory")
            except (ValueError, OSError, RecursionError) as exc:
                if NAME.fullmatch(folder.name):
                    group.create_task(diagnostic(directory, folder.name, str(exc)))
                else:
                    print(
                        json.dumps({"name": folder.name, "detail": str(exc)}),
                        file=sys.stderr,
                        flush=True,
                    )
                continue
            tasks[package.name] = group.create_task(
                serve(package, directory, port, capacity)
            )
        await stop.wait()
        for name, task in tasks.items():
            if not task.done():
                task.cancel()
                group.create_task(stopped(directory, name))


async def stopped(directory, name):
    # The core also stops reading requests during its own orderly shutdown.
    with suppress(TimeoutError):
        await asyncio.wait_for(report(directory, name, "stopped", "Host stopped"), 0.5)
