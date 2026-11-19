import asyncio
import json
from pathlib import Path

import pytest
from fwc.plugins import host
from fwc.plugins.peers_mesh import decode, encode, peer_records

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "plugins/examples/peers-mesh"


def vectors():
    doc = (ROOT / "docs/peer-wire.md").read_text()
    return json.loads(doc.split("```json\n")[1].split("\n```")[0])


@pytest.mark.parametrize("vector", vectors())
def test_documented_vectors(vector):
    data = bytes.fromhex(vector["hex"])
    assert decode(data) == vector["record"]
    assert encode(vector["record"]) == data


def test_additive_version_reordered_fields_and_unknown_tlv():
    data = bytearray.fromhex(vectors()[0]["hex"])
    data[6] = 2
    fields = data[7:]
    data[7:] = fields[6:] + b"\x80\x03xyz" + fields[:6]
    data[1] = len(data) - 2
    assert decode(data) == vectors()[0]["record"] | {"wire_version": 2}
    with pytest.raises(ValueError):
        encode(decode(data))


@pytest.mark.parametrize(
    "change",
    [
        lambda b: bytes([221, 255]) + b[2:],
        lambda b: b[:6] + bytes([0]) + b[7:],
        lambda b: b[:2] + bytes(4) + b[6:],
        lambda b: b[:1] + bytes([b[1] + 1]) + b[2:] + b"\x99",
        lambda b: b[:1] + bytes([b[1] + 3]) + b[2:] + b"\x80\xffX",
        lambda b: b[:1] + bytes([b[1] + 3]) + b[2:] + b"\x01\x01A",
        lambda b: b[:1] + bytes([b[1] - 10]) + b[2:-10],
    ],
)
def test_invalid_elements(change):
    with pytest.raises(ValueError):
        decode(change(bytes.fromhex(vectors()[0]["hex"])))


def test_round_trip_limits_and_unicode():
    record = {
        "wire_version": 1,
        "peer": "Field_1.a-b",
        "timestamp": 2**64 - 1,
        "name": "草地",
        "channel": 233,
        "network_count": 65535,
    }
    assert decode(encode(record)) == record


def test_summary_joins_local_peers_and_recovers_after_bad_elements():
    original = vectors()[0]
    recent = original["record"] | {"timestamp": 4}
    peers = [
        {"peer": "node", "name": "North", "seq": 12},
        {"peer": "other", "name": "Field", "seq": 2},
    ]
    records = peer_records(peers, ["aa", encode(recent).hex(), original["hex"], None])
    assert records[0] == recent | {
        "name": "North",
        "seq": 12,
        "wire": encode(recent | {"name": "North"}).hex(),
    }
    assert records[1] == peers[1]
    assert peer_records([], []) == []


@pytest.mark.parametrize("missing", ["status.read", "export.write"])
def test_revocation_before_plugin_start(tmp_path, monkeypatch, missing):
    package = host.Manifest.load(PACKAGE)
    (tmp_path / "plugin-config.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "peers-mesh": {
                        "permissions": sorted(package.permissions - {missing})
                    }
                }
            }
        )
    )

    async def report(directory, name, state, detail, error):
        assert name == "peers-mesh" and state == "unavailable"
        assert error == {"code": "permission_denied", "permission": missing}
        raise asyncio.CancelledError

    monkeypatch.setattr(host, "report", report)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(host.serve(package, tmp_path, 1, asyncio.Semaphore(1)))
    assert not (tmp_path / "exports").exists()
