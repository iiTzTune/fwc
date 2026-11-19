import asyncio
import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from conftest import publish, request
from fwc.capture.control import Commands, Control
from fwc.capture.worker import Publisher, Settings, run
from fwc.plugins import host, peers_mesh
from fwc.plugins.sandbox import invoke

EXAMPLE = Path(__file__).resolve().parents[2] / "plugins/examples/peers-mesh"


def test_received_element_merges_with_http_peer(device):
    port, state, _ = device
    folder = state / "plugins/peers-mesh"
    shutil.copytree(EXAMPLE, folder)
    element = bytes.fromhex("dd15024657430101046e6f646502080000000000000001")
    ap = bytes.fromhex("020000000001")
    packet = (
        bytes.fromhex("000008000000000080000000")
        + bytes(6)
        + ap
        + ap
        + bytes(14)
        + b"\x00\x04home"
        + element
    )

    class Radio:
        finished = False

        @contextmanager
        def listen(self, channel):
            yield self

        def settimeout(self, timeout):
            pass

        def recv(self, size):
            self.finished = True
            return packet

    radio = Radio()
    settings = Settings("phy0", "fwcmon0", str(state / "records"))
    publisher = Publisher(state / "bus.sock")
    try:
        run(
            settings,
            radio,
            publisher,
            lambda: radio.finished,
            control=Control(state, settings, Commands(state / "bus.sock")),
        )
    finally:
        publisher.close()
    publish(state, "peer.seen", {"peer": "node", "name": "Companion"}, "known-peer")
    package = host.Manifest.load(folder)
    data = host.snapshot(package, port)
    data["peer_elements"] = peers_mesh.recent_elements(state)
    assert data["peer_elements"] == [element.hex()]
    result = asyncio.run(invoke(package, data))
    asyncio.run(host.deliver(package, result, state))
    (record,) = json.loads((state / "exports/peers-mesh.json").read_text())
    assert (record["name"], record["peer"], record["timestamp"]) == (
        "Companion",
        "node",
        1,
    )
    assert set(request(port, "GET", "/api/peers")[1]["items"][0]) == {
        "seq",
        "peer",
        "name",
    }
