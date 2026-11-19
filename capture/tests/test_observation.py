import struct

import pytest
from fwc.capture.observation import Observer, decode

AP = bytes.fromhex("020000000001")
CLIENT = bytes.fromhex("020000000002")


def packet(kind=0x80, ssid=b"home", signal=-49, extra=b""):
    radio = struct.pack("<BBHIb", 0, 0, 9, 1 << 5, signal)
    header = bytes([kind, 0, 0, 0]) + CLIENT + AP + AP + b"\x00\x00"
    body = {
        0x80: bytes(12) + bytes([0, len(ssid)]) + ssid,
        0x10: bytes(6),
        0x30: bytes(6),
        0xA0: bytes(2),
    }.get(kind, b"")
    return radio + header + body + extra


def test_metadata_only_and_signal():
    beacon = decode(packet(extra=b"\xdd\x06extras"))
    assert beacon.ssid == "home"
    assert beacon.dbm == -49
    assert b"extras" not in beacon.record
    for kind in (0x08, 0x88, 0xB0, 0x40, 0xC0):
        assert decode(packet(kind)) is None


def test_malformed_and_partial_frames():
    frame = packet()
    for end in range(len(frame)):
        assert decode(frame[:end]) is None
    bad = bytearray(packet(0x10))
    bad[9 + 24 + 2] = 1
    assert decode(bad) is None


def test_event_order_and_policy():
    observer = Observer(["home"], ["ignore"])
    assert not observer.observe(decode(packet(0x10)), 1, 9)
    assert not observer.observe(decode(packet(ssid=b"ignore")), 1, 9)
    messages = observer.observe(decode(packet()), 1, 9)
    assert messages[0][0] == "network.seen"
    assert messages[0][1]["owned"] is True
    assert messages[1][0] == "signal.stats"
    assert observer.observe(decode(packet()), 1, 9) == []
    for kind, state in ((0x10, "joined"), (0xA0, "left")):
        msg = observer.observe(decode(packet(kind)), 1, 9)[0]
        assert msg[0] == "association.event"
        assert msg[1]["state"] == state


@pytest.mark.parametrize(
    "field,value",
    [
        ("channels", []),
        ("channels", [True]),
        ("dwell_ms", 0),
        ("file_count", -1),
        ("max_bytes", 20),
        ("phy", "../phy0"),
        ("allow_ssids", "abc"),
        ("typo", 0),
    ],
)
def test_config_validation(field, value):
    from fwc.capture.worker import Settings

    with pytest.raises(ValueError):
        Settings.parse({"phy": "phy0", "interface": "fwcmon0", field: value})


def test_association_without_a_known_or_allowed_beacon():
    observer = Observer(["home"], ["denied"])
    assert not observer.observe(decode(packet(ssid=b"denied")), 1, 1)
    assert not observer.observe(decode(packet(0x10)), 1, 2)
    observer.observe(decode(packet(ssid=b"home")), 1, 3)
    assert observer.observe(decode(packet(0x10)), 1, 4)
    assert not observer.observe(decode(packet(ssid=b"other")), 1, 5)
    assert not observer.observe(decode(packet(0x10)), 1, 6)


def test_alignment_across_extended_radiotap_fields():
    # Extended bitmap then padding before TSFT. Signal follows flags and channel.
    radio = struct.pack("<BBHII", 0, 0, 31, (1 << 31) | 0x2B, 0)
    radio += bytes(4 + 8) + b"\x00\x00" + bytes(4) + b"\xcf"
    assert decode(radio + packet()[9:]).dbm == -49
    broken = bytearray(packet())
    broken[2:4] = b"\xff\xff"
    assert decode(broken) is None
