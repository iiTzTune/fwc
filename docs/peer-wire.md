# Nearby peer records

The `peers-mesh` plugin reads nearby peer names from the existing HTTP peer
list. Those entries come from `peer.seen` events. It also reads FWC
information elements received during an observation session. It merges by
peer identity and writes a local summary in `exports/peers-mesh.json`.
An entry seen only in the HTTP list keeps its name and sequence. A wire
observation supplies its own timestamp and fields. The newest timestamp
wins for each identity, with the first received element winning a tie.
The plugin considers up to 32 recent entries from each source and exports
up to eight peers, most recent wire observation first.

## Element layout

An element starts with the single byte `dd`, followed by its unsigned
one-byte payload length. The length must match the complete remaining
payload, whose maximum is 255 bytes. The payload begins with the four
bytes `02 46 57 43`, a locally defined identifier for this FWC format.
The next byte is `peer.wire_version`. The current writer uses version 1.
It appends type-length-value (TLV) fields. Type and length are each one
unsigned byte. Length counts value bytes. Integers use network byte order.
A record may be used without a display name.

| Type | Field | Value bytes | Meaning |
| --- | --- | --- | --- |
| 01 | `peer` | 1–40 | Required stable, case-sensitive ASCII identity using letters, digits, dots, hyphens or underscores. |
| 02 | `timestamp` | 8 | Required unsigned UTC Unix time in seconds when the observations were summarized. |
| 03 | `name` | 1–32 | Optional printable UTF-8 display name. |
| 04 | `channel` | 1 | Optional observed channel, 1–233. |
| 05 | `network_count` | 2 | Optional number of network records, 0–65535. |

Writers emit known types once in ascending order. Readers accept any order,
skip unknown types by length, and reject repeated known types, missing
required fields, invalid values and incomplete lengths. Version zero is
reserved. Later nonzero versions in this family must keep the envelope,
required fields, and existing field meanings. A version 2 writer can add
optional TLVs and a version 1 reader will still return the known fields.
An incompatible format would require a different element discriminator.

The export's `wire` field is a canonical version 1 encoding of the known
fields for that peer. It can include the display name from the HTTP peer
list when the wire observation omitted a name. An entry with only an HTTP
sequence has no wire encoding because its observation time is unknown.
The plugin writes encodings locally. The capture worker only receives.
Support for the earlier handheld companion format can be added in a
separate adapter plugin when a reference fixture becomes available.

## Executable vectors

The hex strings are complete elements, including both header bytes. Each
object pairs the wire bytes with the record returned by the decoder.

```json
[
  {
    "hex": "dd15024657430101046e6f646502080000000000000001",
    "record": {"wire_version": 1, "peer": "node", "timestamp": 1}
  },
  {
    "hex": "dd220246574301010270320208000000006553f10003064d6561646f7704010605020011",
    "record": {"wire_version": 1, "peer": "p2", "timestamp": 1700000000, "name": "Meadow", "channel": 6, "network_count": 17}
  }
]
```

With peers-mesh installed and its grants enabled, the worker reads complete
FWC elements following the SSID in an accepted beacon. It supplies at most
32 distinct samples per dwell through the local `peer.element` topic.
The host also reads the existing peer list. The plugin exports merged
records to `exports/peers-mesh.json`.
