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


class Snapshot(TypedDict, total=False):
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
