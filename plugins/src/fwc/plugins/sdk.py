"""Version one observation snapshot and result helpers, also installed as fwc_sdk."""

import json
import sys
from typing import TypedDict


class Network(TypedDict):
    network: str
    ssid: str
    owned: bool
    channel: int
    time_ns: int
    seq: int


class Status(TypedDict):
    committed: int
    clients: int


class RadioState(TypedDict, total=False):
    channel: int
    mode: str
    tx_enabled: bool
    dwell_ms: int


class Snapshot(TypedDict, total=False):
    settings: dict
    radio: RadioState
    last_radio: RadioState
    status: Status
    networks: list[Network]


def read_event() -> Snapshot:
    return json.load(sys.stdin)


def widget(text: str) -> None:
    print(json.dumps({"action": "widget", "text": text}))


def display(text: str) -> None:
    print(json.dumps({"action": "display", "text": text}))


def export(records: list[Network]) -> None:
    print(json.dumps({"action": "export", "records": records}))


def set_channel(channel: int) -> None:
    print(
        json.dumps(
            {"action": "radio", "command": {"op": "set_channel", "channel": channel}}
        )
    )


def configure_radio(**changes) -> None:
    print(
        json.dumps(
            {"action": "radio", "command": {"op": "configure", "changes": changes}}
        )
    )
