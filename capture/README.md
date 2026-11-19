# Capture worker

Run the worker on Linux with a dedicated monitor-capable radio, iproute2 and
iw. Use a service account with the interface-management and raw-socket
permissions for that radio. The example supervisor service entry below assumes
the FWC workspace environment is installed at /opt/fwc/.venv.

```json
{
  "name": "capture",
  "command": ["/opt/fwc/.venv/bin/python", "-m", "fwc.capture", "/etc/fwc/capture.json", "/var/lib/fwc/bus.sock"],
  "restarts": 3,
  "window_secs": 60
}
```

A complete capture configuration follows. Only phy and interface are required.
The remaining values shown are defaults. Relative directories resolve from the
service working directory. Invalid configuration writes a structured diagnostic
to standard error and a readable capture-status.txt beside the bus socket. The
worker checks the file again every two seconds.

```json
{
  "phy": "phy0",
  "interface": "fwcmon0",
  "directory": "captures",
  "channels": [1, 6, 11],
  "dwell_ms": 1000,
  "max_bytes": 4194304,
  "file_count": 8,
  "allow_ssids": [],
  "deny_ssids": []
}
```

The worker creates the named monitor interface or reuses it when its type and
radio match. It raises the link, cycles channels in order and listens with a
fresh filtered socket for each dwell. A radio's interfaces can share a channel
setting, so dedicate the selected physical radio to the worker. Choose channels
supported by that radio and its region. The worker labels interfaces it creates.
Normal exit removes its labeled
interface, including one recovered after an abrupt stop. A separately
provisioned monitor interface remains available on exit. Dwell time is 10–60000 milliseconds.
Tuning and bus acknowledgments
can add time between channels.

An empty allow list observes all UTF-8 network names. A populated list selects
matching names and marks them as owned for the companion display. The deny list
takes precedence. Names are case-sensitive and can use up to 32 UTF-8 bytes.
Association events require a preceding accepted beacon for that network.
Up to 1024 recently observed networks stay in memory per session.

## Files and limits

The capture directory has one writer, enforced by capture.lock. Standard
little-endian pcap files use link type 105 (802.11) and microsecond timestamps.
They contain the observed management header and fixed fields plus the SSID for
beacons. An accepted association or reassociation response records a join,
and a disassociation records a departure. Each record contains just the
selected fields. A kernel socket filter selects the supported frame types.

Each file can hold 128 bytes to 2 GiB, including the pcap header. The count can
range from 1 to 1024 files. The oldest file is removed before a new file would
exceed that count. On restart, the worker enforces the current limits,
repairs an incomplete final record, and starts a new file. It checks volume
use before every append and pauses at 90%
of space available to the service account. It checks again every 250 ms while
paused. Other consumers freeing space allow automatic resumption.

## Bus interface

Messages use the [core protocol](../docs/CORE.md). All payloads include the
observed network's colon-separated BSSID as network, its SSID as ssid, the
boolean owned flag, the tuned channel number and the Unix timestamp time_ns.

| Topic | Bus operation | Additional fields and timing |
| --- | --- | --- |
| network.seen | publish | First accepted beacon in the cache, or changed SSID |
| association.event | publish | station is a colon-separated address, state is joined or left |
| signal.stats | metric | samples, min_dbm and max_dbm for the network on the current channel |

The first available signal sample is published immediately. A cumulative
summary follows at dwell end when additional samples arrive. Signal reports
require the radio to provide signal strength. Durable events wait for a commit
acknowledgment. On a connection failure the worker retries the pending event
with its original ID. Metrics can be dropped while the bus is unavailable.

## Verification

From the repository root, run `uv run pytest capture/` and
`capture/tests/virtual-radio.sh`. The script builds the real core and runs
a software radio over a Unix datagram pair. It exercises the Linux kernel
filter, channel changes, pcap writer and real publish/subscribe connections,
and checks event arrival within one dwell. Storage tests drive an accounted
test volume to the watermark and verify bounded growth and resumption.

For the Linux monitor interface and real filesystem watermark check, follow
the [QEMU guest procedure](HWSIM.md).
