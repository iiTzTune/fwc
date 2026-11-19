"""Device settings and receive-only radio commands shared with the plugin host."""

import json
from pathlib import Path

LEGACY = frozenset(
    {"status.read", "networks.read", "widget.write", "display.write", "export.write"}
)
RADIO = frozenset({"radio.tx", "radio.config", "radio.raw"})
PERMISSIONS = LEGACY | RADIO | {"network.out"}
CONFIG_KEYS = {"channels", "dwell_ms", "dwell_by_channel", "file_count", "max_bytes"}


class PermissionDenied(ValueError):
    def __init__(self, permission):
        super().__init__(f"permission_denied: {permission}")
        self.payload = {"code": "permission_denied", "permission": permission}


def plugin_config(directory, name):
    path = Path(directory) / "plugin-config.json"
    try:
        with path.open("rb") as stream:
            encoded = stream.read(65537)
        if len(encoded) > 65536:
            raise ValueError("plugin configuration exceeds its size limit")
        data = json.loads(encoded)
    except FileNotFoundError:
        data = {"plugins": {}}
    if (
        not isinstance(data, dict)
        or data.keys() != {"plugins"}
        or not isinstance(data["plugins"], dict)
    ):
        raise ValueError("invalid plugin configuration")
    entry = data["plugins"].get(name, {})
    if not isinstance(entry, dict) or entry.keys() - {"settings", "permissions"}:
        raise ValueError("invalid plugin settings")
    rights = entry.get("permissions", list(LEGACY))
    settings = entry.get("settings", {})
    if not isinstance(rights, list) or not all(
        isinstance(p, str) and p in PERMISSIONS for p in rights
    ):
        raise ValueError("invalid plugin permissions")
    if not isinstance(settings, dict):
        raise TypeError("invalid plugin settings")
    return set(rights) - {"radio.raw"}, settings


def require(granted, requested):
    for name in sorted(set(requested) - granted):
        raise PermissionDenied(name)


def radio_grants(permission):
    return {"radio.tx", permission}


def check_dwell_map(values):
    if not isinstance(values, dict) or len(values) > 233:
        raise ValueError("expected a channel dwell map")
    for key, dwell in values.items():
        if not (
            isinstance(key, str)
            and key.isascii()
            and key.isdigit()
            and len(key) <= 3
            and 1 <= int(key) <= 233
            and str(int(key)) == key
        ):
            raise ValueError("expected a channel number from 1 through 233")
        if type(dwell) is not int or not 10 <= dwell <= 60000:
            raise ValueError("channel dwell must be between 10 and 60000 milliseconds")


def command_permission(command):
    if not isinstance(command, dict):
        raise TypeError("expected radio command")
    if command.get("op") == "set_channel" and command.keys() == {"op", "channel"}:
        channel = command["channel"]
        if type(channel) is int and 1 <= channel <= 233:
            return "radio.tx"
        raise ValueError("invalid channel")
    if command.get("op") == "configure" and command.keys() == {"op", "changes"}:
        changes = command["changes"]
        if isinstance(changes, dict) and changes and changes.keys() <= CONFIG_KEYS:
            if "dwell_by_channel" in changes:
                check_dwell_map(changes["dwell_by_channel"])
            return "radio.config"
    raise ValueError("unsupported radio command")
