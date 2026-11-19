"""Decode the small set of wireless management records used by the companion."""

import struct
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    kind: int
    network: str
    ssid: str | None
    station: str
    dbm: int | None
    record: bytes


def radio_fields(packet):
    if len(packet) < 8 or packet[0] != 0:
        return None
    size, present = struct.unpack_from("<HI", packet, 2)
    if not 8 <= size <= len(packet):
        return None
    offset = 8
    word = present
    while word & 0x80000000:
        if offset + 4 > size:
            return None
        word = struct.unpack_from("<I", packet, offset)[0]
        offset += 4
    dbm = None
    # Natural alignment is relative to the beginning of the radiotap header.
    for bit, (align, width) in enumerate(
        ((8, 8), (1, 1), (1, 1), (2, 4), (2, 2), (1, 1))
    ):
        if present & (1 << bit):
            offset = (offset + align - 1) & -align
            if offset + width > size:
                return None
            if bit == 1 and packet[offset] & 0x40:  # bad checksum
                return None
            if bit == 5:
                dbm = struct.unpack_from("b", packet, offset)[0]
            offset += width
    return size, dbm


def decode(packet):
    radio = radio_fields(packet)
    if radio is None:
        return None
    offset, dbm = radio
    frame = packet[offset:]
    if (
        len(frame) < 24 or frame[1] & 0xC7
    ):  # nonstandard header, fragment, or protected body
        return None
    kind = frame[0]
    body_sizes = {0x80: 12, 0x10: 6, 0x30: 6, 0xA0: 2}
    if kind not in body_sizes:
        return None
    end = 24 + body_sizes[kind]
    if len(frame) < end:
        return None
    network = frame[16:22]
    if kind in (0x80, 0x10, 0x30) and frame[10:16] != network:
        return None
    station = frame[4:10] if frame[10:16] == network else frame[10:16]
    ssid = None
    if kind == 0x80:
        # Retain just the mandatory SSID element following the fixed beacon fields.
        if len(frame) < end + 2 or frame[end] != 0:
            return None
        length = frame[end + 1]
        end += 2 + length
        if length > 32 or len(frame) < end:
            return None
        try:
            ssid = frame[end - length : end].decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif kind in (0x10, 0x30) and frame[26:28] != b"\x00\x00":
        return None
    return Observation(kind, network.hex(":"), ssid, station.hex(":"), dbm, frame[:end])


class Observer:
    """Keep a bounded network cache; collect signal counts and ranges per dwell."""

    def __init__(self, allow, deny):
        self.allow, self.deny = set(allow), set(deny)
        self.networks = OrderedDict()
        self.stats = {}

    def accepts(self, item):
        ssid = item.ssid if item.ssid is not None else self.networks.get(item.network)
        return (
            ssid is not None
            and ssid not in self.deny
            and (not self.allow or ssid in self.allow)
        )

    def observe(self, item, channel, timestamp_ns):
        if not self.accepts(item):
            if item.ssid is not None:
                self.networks.pop(item.network, None)
                self.stats.pop(item.network, None)
            return []
        network = item.network
        name = item.ssid if item.ssid is not None else self.networks[network]
        common = {
            "network": network,
            "ssid": name,
            "owned": name in self.allow,
            "channel": channel,
            "time_ns": timestamp_ns,
        }
        messages = []
        if item.kind == 0x80:
            if self.networks.get(network) != name:
                messages.append(("network.seen", common.copy()))
            self.networks[network] = name
            self.networks.move_to_end(network)
            if len(self.networks) > 1024:
                oldest, _ = self.networks.popitem(last=False)
                self.stats.pop(oldest, None)
        else:
            state = "left" if item.kind == 0xA0 else "joined"
            messages.append(
                ("association.event", dict(common, station=item.station, state=state))
            )
        if item.dbm is not None:
            entry = self.stats.get(network)
            if entry is None:
                entry = dict(common, samples=1, min_dbm=item.dbm, max_dbm=item.dbm)
                self.stats[network] = entry
                messages.append(("signal.stats", entry.copy()))
            else:
                entry.update(
                    samples=entry["samples"] + 1,
                    min_dbm=min(entry["min_dbm"], item.dbm),
                    max_dbm=max(entry["max_dbm"], item.dbm),
                    time_ns=timestamp_ns,
                )
        return messages

    def finish_dwell(self):
        summaries = [
            ("signal.stats", entry)
            for entry in self.stats.values()
            if entry["samples"] > 1
        ]
        self.stats.clear()
        return summaries


def peer_element(packet, observation):
    """Find the local companion element following the accepted beacon's SSID."""
    if observation.kind != 0x80:
        return None
    offset = int.from_bytes(packet[2:4], "little") + len(observation.record)
    while offset + 2 <= len(packet):
        kind, length = packet[offset : offset + 2]
        end = offset + 2 + length
        if end > len(packet):
            break
        if (
            kind == 221
            and length >= 5
            and packet[offset + 2 : offset + 6] == b"\x02FWC"
        ):
            return packet[offset:end].hex()
        offset = end
    return None
