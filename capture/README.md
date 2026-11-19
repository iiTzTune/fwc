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

The following configuration shows the defaults. The worker chooses the first USB
radio advertising monitor mode in ascending kernel phy number order.
Set `observation_interface` to the source interface name (for example, `wlx001122334455`)
to choose a particular adapter. The separate `interface` key names the monitor
link. An existing installation can keep its explicit `phy`, including a software
radio in a test guest. If both selectors are given they must identify the same radio.
SDIO radios and devices bound to brcmfmac are reserved for connectivity even
with an explicit selector. When the observation radio is absent, the worker
waits and checks again every two seconds within the same process.

Relative directories resolve from the
service working directory. Invalid configuration writes a structured diagnostic
to standard error and a readable capture-status.txt beside the bus socket. The
worker checks the file again every two seconds.

```json
{
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

## Zero 2 W observation radio

The TP-Link Archer T2U Nano (RTL8811AU, expected USB ID `2357:011e`) plugs
into the externally powered Waveshare hub. The onboard CYW43430 remains the
connectivity interface. The running kernel needs `CONFIG_RTW88_8821AU` and
the adapter firmware. The USB ID and actual binding on this unit are open
hardware checks. The upstream device table and module build rule describe
the expected combination. [Kernel device table](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/realtek/rtw88/rtw8821au.c)
· [Module build rule](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/realtek/rtw88/Makefile)

With the observation service stopped, insert the adapter and record the result.
Replace `$USB_IF` with its source interface from `iw dev`. Confirm the USB
bus, IDs and driver on that specific interface before changing its mode.

```sh
lsusb -d 2357:011e
modprobe rtw88_8821au
lsmod | grep rtw88
iw dev
dev=$(readlink -f "/sys/class/net/$USB_IF/device")
readlink -f "$dev/subsystem"
cat "$dev/../idVendor" "$dev/../idProduct"
readlink -f "$dev/driver/module"
phy=$(basename "$(readlink -f "/sys/class/net/$USB_IF/phy80211")")
iw phy "$phy" info
```

Expect a TP-Link entry, a successful module load, the `rtw88_8821au` module
and its dependencies for a modular build, subsystem `usb`, IDs `2357` and
`011e`, and a module link ending in `/rtw88_8821au`. A built-in driver can
be checked through the USB driver link and the running kernel configuration.
Expect `* monitor` in the supported or software interface modes.

On the confirmed USB interface, use an owner's access point on channel 1 with
visible beacons. Run as root with the source excluded from network management.
The counter below reads the receive count across a ten-second observation.

```sh
ip link set dev "$USB_IF" down
iw dev "$USB_IF" set monitor none
ip link set dev "$USB_IF" up
iw dev "$USB_IF" set channel 1
iw dev "$USB_IF" info
rx="/sys/class/net/$USB_IF/statistics/rx_packets"
start=$(cat "$rx")
sleep 10
echo "observed frames: $(( $(cat "$rx") - start ))"
ip link set dev "$USB_IF" down
iw dev "$USB_IF" set type managed
```

Expect `type monitor`, channel 1 and a positive frame count. Start a fresh
FWC observation session with channel 1 in its plan. Record the sequence
before starting the worker, then check for a newly committed network record.

```sh
seq=$(sqlite3 /var/lib/fwc/state.sqlite 'SELECT COALESCE(MAX(seq),0) FROM events')
# Start the configured capture service, then allow at least one channel cycle.
iw dev fwcmon0 info
sqlite3 /var/lib/fwc/state.sqlite "SELECT seq,json_extract(data,'$.ssid'),json_extract(data,'$.channel') FROM events WHERE seq > $seq AND topic='network.seen' ORDER BY seq"
```

Expect a monitor on the same phy and a new row containing the owner's SSID
and channel. Keep the USB binding, frame count and activity log row together
as on-desk evidence.

Use the rtl8821au DKMS fallback only if the physical ID is confirmed and
the deployed kernel must be retained but lacks an in-tree driver for that
ID, or its bound upstream driver reproducibly cannot provide the monitor
interface and receive known beacons. First verify firmware availability,
USB power, the selected channel and the reference access point. For a
fallback build use matching kernel headers and a module that advertises
the exact USB alias. Load only one driver for the adapter and repeat the
same interface, mode, count and activity-log checks. Record the running
kernel, DKMS package revision and actual bound module. A quiet channel or
a different USB ID needs its own diagnosis.
