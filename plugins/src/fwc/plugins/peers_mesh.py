"""FWC peer information elements and bounded local peer summaries."""

import re

DISCRIMINATOR = b"\x02FWC"
WIRE_VERSION = 1
FIELDS = {1: "peer", 2: "timestamp", 3: "name", 4: "channel", 5: "network_count"}
WIDTHS = {"timestamp": 8, "channel": 1, "network_count": 2}
IDENTITY = re.compile(r"[A-Za-z0-9_.-]{1,40}")


def field_bytes(field, value):
    if field == "peer":
        if not isinstance(value, str) or not IDENTITY.fullmatch(value):
            raise ValueError("invalid peer identity")
        return value.encode("ascii")
    if field == "name":
        if not isinstance(value, str) or not value.isprintable():
            raise ValueError("invalid peer name")
        data = value.encode("utf-8")
        if not 1 <= len(data) <= 32:
            raise ValueError("peer name must occupy 1 through 32 bytes")
        return data
    width = WIDTHS[field]
    if type(value) is not int or not 0 <= value < (1 << (8 * width)):
        raise ValueError(f"invalid {field}")
    if field == "channel" and not 1 <= value <= 233:
        raise ValueError("invalid channel")
    return value.to_bytes(width, "big")


def encode(record):
    """Encode one complete information element in canonical version 1 order."""
    if (
        type(record.get("wire_version")) is not int
        or record["wire_version"] != WIRE_VERSION
    ):
        raise ValueError("expected wire version 1")
    if not {"peer", "timestamp"} <= record.keys():
        raise ValueError("expected peer identity and timestamp")
    payload = bytearray(DISCRIMINATOR)
    payload.append(WIRE_VERSION)
    for tag, field in FIELDS.items():
        if field in record:
            data = field_bytes(field, record[field])
            payload.extend((tag, len(data)))
            payload.extend(data)
    return bytes((221, len(payload))) + payload


def decode(element):
    """Read the known fields of a complete FWC peer information element."""
    if (
        not 7 <= len(element) <= 257
        or element[0] != 221
        or element[1] != len(element) - 2
        or element[2:6] != DISCRIMINATOR
        or element[6] == 0
    ):
        raise ValueError("invalid peer element header")
    record = {"wire_version": element[6]}
    offset = 7
    while offset < len(element):
        if offset + 2 > len(element):
            raise ValueError("incomplete peer field header")
        tag, length = element[offset : offset + 2]
        offset += 2
        data = element[offset : offset + length]
        if len(data) != length:
            raise ValueError("incomplete peer field value")
        offset += length
        field = FIELDS.get(tag)
        if field is None:
            continue
        if field in record:
            raise ValueError(f"duplicate peer field {field}")
        if field in WIDTHS:
            if length != WIDTHS[field]:
                raise ValueError(f"invalid length for {field}")
            value = int.from_bytes(data, "big")
        else:
            value = data.decode("utf-8")
        field_bytes(field, value)
        record[field] = value
    if not {"peer", "timestamp"} <= record.keys():
        raise ValueError("expected peer identity and timestamp")
    return record


def peer_records(peers, elements):
    """Merge the latest reported names and wire observations into a local export."""
    entries = {}
    for entry in peers[:32]:
        if not isinstance(entry, dict):
            continue
        try:
            field_bytes("peer", entry["peer"])
            field_bytes("name", entry["name"])
        except (KeyError, ValueError):
            continue
        seq = entry.get("seq")
        if type(seq) is int and 0 <= seq <= 2**63 - 1:
            entries.setdefault(
                entry["peer"], {k: entry[k] for k in ("peer", "name", "seq")}
            )

    latest = {}
    for text in elements[:32]:
        if not isinstance(text, str) or len(text) > 514:
            continue
        try:
            record = decode(bytes.fromhex(text))
        except ValueError:
            continue
        peer = record["peer"]
        if peer not in latest or record["timestamp"] > latest[peer]["timestamp"]:
            latest[peer] = record

    for peer, observation in latest.items():
        merged = entries.get(peer, {}) | observation
        merged["wire"] = encode(merged | {"wire_version": WIRE_VERSION}).hex()
        entries[peer] = merged

    return sorted(
        entries.values(),
        key=lambda r: (-r.get("timestamp", -1), -r.get("seq", -1), r["peer"]),
    )[:8]


def recent_elements(directory):
    import sqlite3
    from contextlib import closing

    with closing(
        sqlite3.connect(
            (directory / "state.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        return [
            row[0]
            for row in db.execute(
                "SELECT json_extract(data,'$.element') FROM events WHERE topic='peer.element' ORDER BY seq DESC LIMIT 32"
            )
        ]
