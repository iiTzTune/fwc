# Bring-up evidence ledger

Keep each item open until an operator records the board revision, software commit,
command output and observed result. Run the board checks on a designated test
device with the corresponding drivers selected in the core configuration.

## Open — Waveshare 2.13in V4 refresh

Install the vendor Python library and SPI/GPIO bindings in the driver interpreter,
enable SPI and connect the panel. The reference x86 board needs a compatible
GPIO/SPI implementation of the vendor transport. On a provisioned board, send
alternating black and white horizontal bands. Observe a full refresh and count
the bands on the physical panel.

```sh
python3 -c 'from fwc.waveshare import Display; Display({}).render(122,250,b"".join(bytes([255 if (y//25)%2 else 0])*16 for y in range(250)))'
```

## Open — PiSugar S GPIO3 external-power edge reaches the HAL

On the Zero 2 W, select the `pisugar-s` power driver, set the PiSugar S
auto-start switch ON and leave I²C disabled on GPIO3. Connect and disconnect
the charger while the battery continues supplying the Pi. Check that the
powered USB hub does not supply the Pi through its upstream cable.

```sh
curl -fsS http://127.0.0.1:8080/api/power
```

Expected on charger insertion: `capability=coarse`, `external_power=true`,
`charge_state=unknown`. On removal expect `false` and `discharging`.
Both samples must have `millivolts=null`. Check both transitions in Settings.

## Open — PiSugar S hardware cutoff under load

Use a disposable test card and the manufacturer's supported battery and
normal operating load. Record pack voltage and Pi supply voltage with an
independent meter/logger through the hardware cutoff. Run the following on
the board and record the serial console on a separately powered computer.

```sh
while :; do date -u; curl -fsS http://127.0.0.1:8080/api/power; echo; sleep 1; done
```

Expected: discharging samples until the board supply drops and output stops.
Correlate the supply drop with the measured pack voltage. The coarse driver
has no voltage threshold or software shutdown. The hardware cutoff is the
protection to verify. [PiSugar S specifications](https://docs.pisugar.com/docs/product-wiki/battery/pisugar-s-series)

## Open — Power loss loses at most one state-store sync interval

Record serial output externally while the test device publishes numbered
probes. Use a fresh run identifier on every trial. On a disposable card,
interrupt board power at a recorded time during capture, including a trial
during file rotation. Keep both the PiSugar and the powered hub in mind
when identifying the supply to interrupt.

```sh
python3 -u - <<'PYTHON'
import json, socket, time, uuid
run = uuid.uuid4().hex
with socket.socket(socket.AF_UNIX) as sock:
    sock.connect("/var/lib/fwc/bus.sock")
    with sock.makefile("rb") as stream:
        i = 0
        while True:
            stamp = time.monotonic_ns()
            msg = {"op":"publish", "id":f"{run}-{i}", "topic":"power.probe",
                   "data":{"run":run,"i":i,"time_ns":stamp}}
            print("submit", run, i, stamp)
            sock.sendall(json.dumps(msg).encode() + b"\n")
            assert json.loads(stream.readline())["ok"]
            print("ack", run, i, stamp)
            i += 1
            time.sleep(0.2)
PYTHON
```

After reboot substitute the printed run ID and compare the sequence and
timestamps with the external log.

```sh
python3 -m sqlite3 /var/lib/fwc/state.sqlite "PRAGMA integrity_check;"
python3 -m sqlite3 /var/lib/fwc/state.sqlite "SELECT data FROM events WHERE topic='power.probe' AND json_extract(data,'$.run')='$RUN_ID' ORDER BY seq;"
```

Expected: `ok`, contiguous recovered probes, all acknowledged probes present
and at most two seconds of submitted activity missing from the tail. Record
the measured gap for each trial. The two-second requirement covers the state
store; measure pcap tail recovery separately.

## Open — M1 watchdog reset

Configure the core with the board watchdog device. Record the boot ID and
hardware timeout, confirm uptime advances for longer than that timeout, then
suspend the sole core process. Observe an unattended reset. After reboot,
record the new boot ID and the watchdog reset indication from the board.

```sh
cat /proc/sys/kernel/random/boot_id /sys/class/watchdog/watchdog0/timeout
sleep "$(( $(cat /sys/class/watchdog/watchdog0/timeout) + 2 ))"
pgrep -ax fwc-core
kill -STOP "$(pgrep -x fwc-core)"
# After the board restarts
cat /proc/sys/kernel/random/boot_id /sys/class/watchdog/watchdog0/bootstatus
```

## Verified — Hosted GitHub Actions

GitHub Actions passed all six jobs for commit `16bf6be`, covering Rust,
Python 3.11–3.13, the web and image assets, and the UEFI rollback check.
[Hosted run 44525835238](https://github.com/iiTzTune/fwc/actions/runs/44525835238)

```sh
run_id=$(gh run list --workflow ci.yml --commit "$(git rev-parse HEAD)" --json databaseId --jq '.[0].databaseId')
gh run watch "$run_id" --exit-status
gh run view "$run_id" --json conclusion,headSha,url
```

## Open — Board slot handoff and repeated watchdog resets

On the designated A/B board, start from a healthy A slot, install a known
working B update using its released slot artifact and digest, and reboot.
After the health check confirms B, verify the previous slot is A. Perform the
M1 watchdog check above twice on consecutive B boots. Check the reset indication
after each, and the automatic return to A. Record the boot IDs and selection
output. Also confirm with the board watchdog documentation or serial trace that
its timer covers the firmware-to-kernel handoff; QEMU exercises slot selection.

```sh
fwc-update install "$SLOT_ARTIFACT_URL" "$SLOT_SHA256"
reboot
# After each boot
cat /proc/cmdline /sys/class/watchdog/watchdog0/bootstatus
grep -aE '^(confirmed|previous|pending|resets)=' /boot/efi/EFI/FWC/grubenv
```

## Open — M7 reference board boot and dedicated USB radio

Flash a checked image as described in [bring-up](bringup.md), boot it on the UP
Board from removable UEFI media, and record board revision, firmware version,
slot, radio driver, first-boot identity and API response. Select its dedicated
USB phy in capture.json and enable the capture service. Reboot and confirm the
identity persists and the observation worker publishes the owner's network.

```sh
cat /sys/devices/virtual/dmi/id/board_name /sys/class/dmi/id/bios_version
cat /proc/cmdline /var/lib/fwc/device-id
iw dev
curl -fsS http://127.0.0.1:8080/api/status
curl -fsS 'http://127.0.0.1:8080/api/networks?limit=10'
```

## Open — Physical button input

Connect the board button or GPIO key to a Linux input driver and select the
corresponding event device. Press and release the button and record the key
code and both transitions. The packaged headless input role can test UI
logic; an installed board input adapter is needed for HAL reads of that button.

```sh
cat /proc/bus/input/devices
evtest "$BUTTON_EVENT_DEVICE"
```

## Verified — Hosted image identity

Two independent GitHub Actions runs built commit `16bf6be` from clean
checkouts, booted their disk images twice in QEMU and uploaded the binaries.
Both SHA-256 manifests matched each other and the clean local build.
[Push run](https://github.com/iiTzTune/fwc/actions/runs/44525834805) ·
[Manual dispatch](https://github.com/iiTzTune/fwc/actions/runs/44525868298)

| Artifact | SHA-256 |
| --- | --- |
| fwc-x86_64.img | `2f73da265e4b3ae5effaba68f2de599f2cb7ae3f76a2951f9cf21ba06926e763` |
| fwc-slot.ext4 | `8413efe952e4de63adc9cd91d69d65cad8ffcbb2aeea14bcc05213c97d045854` |

### Repeat the hosted comparison

Run the image workflow twice for one commit, waiting for each run to finish
before starting the next. Compare the two uploaded checksum manifests and
record the two run URLs and matching hashes. Set BRANCH to the branch
containing the candidate commit. Use fresh directories for both downloads.

```sh
gh workflow run image.yml --ref "$BRANCH"
gh run list --workflow image.yml --commit "$(git rev-parse HEAD)" --json databaseId,url,conclusion
gh run watch "$FIRST_RUN" --exit-status
gh workflow run image.yml --ref "$BRANCH"
gh run list --workflow image.yml --commit "$(git rev-parse HEAD)" --json databaseId,url,conclusion
gh run watch "$SECOND_RUN" --exit-status
gh run download "$FIRST_RUN" --name image-digests --dir /tmp/fwc-first-digests
gh run download "$SECOND_RUN" --name image-digests --dir /tmp/fwc-second-digests
diff -u /tmp/fwc-first-digests/SHA256SUMS /tmp/fwc-second-digests/SHA256SUMS
```

## Open — Receive-channel selection on the board radio

Install the reference hop plugin on the board and configure it for channels 1
and 6. Enable its `radio.tx` permission. With capture running, compare the
kernel's current channel with the worker's most recent confirmed channel over
several transitions. Record the board, adapter, interface and driver.

```sh
iw dev fwcmon0 info
python3 -m sqlite3 /var/lib/fwc/state.sqlite "SELECT data FROM events WHERE topic='radio/state' ORDER BY seq DESC LIMIT 6"
curl -fsS http://127.0.0.1:8080/api/status
```

Revoke the grant in the device settings. Repeat the status query and confirm
the capture service's start count is unchanged. Check the latest plugin status
for the named permission and confirm the radio resumes the device channel plan.

```sh
python3 -m sqlite3 /var/lib/fwc/state.sqlite "SELECT data FROM events WHERE topic='plugin.status' AND json_extract(data,'$.name')='fwc-plugin-hop' ORDER BY seq DESC LIMIT 1"
iw dev fwcmon0 info
```

## Open — USB receiver and observation map

Connect the USB GPS receiver, select its serial device and baud rate in the
geo-log settings, and grant `networks.read` and `export.write`. At a known
outdoor location, check for a dated fix. Observe a network and compare the
coordinates in the Records page map with the known location.

```sh
ls -l /dev/serial/by-id/
python3 -c 'from pathlib import Path; from fwc.plugins.geo_log import snapshot; print(snapshot(Path("/var/lib/fwc"), {"device":"/dev/ttyACM0","baud":9600})["fix"])'
curl -fsS http://127.0.0.1:8080/api/map
```

Substitute the receiver's actual device and baud rate.

## Open — Epoch and score on the companion display

Install stats-faces and grant `networks.read` and `display.write`. Select the
Waveshare display driver. During an observation session, compare the face,
epoch and score on the physical panel with the latest plugin detail.

```sh
curl -fsS http://127.0.0.1:8080/api/plugins
```

## Open — Nearby peer element on the board radio

Install peers-mesh with its declared grants. At a location where an owner-run
access point already advertises one of the [FWC fixture elements](peer-wire.md),
select its receive channel. Compare the captured element and the local
decoded identity and timestamp with the fixture.

```sh
iw dev fwcmon0 info
python3 -m sqlite3 /var/lib/fwc/state.sqlite "SELECT json_extract(data,'$.element') FROM events WHERE topic='peer.element' ORDER BY seq DESC LIMIT 5"
cat /var/lib/fwc/exports/peers-mesh.json
```

## Open — Adaptive dwell on the board adapter

Install smart-sweep, grant both radio permissions it declares and configure
a channel plan spanning a quiet channel and one with observed associations.
Compare the per-channel dwell in the most recent worker states with the
configured minimum, step and maximum. Revoke `radio.tx` while leaving
`radio.config` enabled. Confirm the device dwell resumes on the next
boundary, the capture start count stays fixed and `tx_enabled` stays false.

```sh
python3 -m sqlite3 /var/lib/fwc/state.sqlite "SELECT data FROM events WHERE topic='radio/state' ORDER BY seq DESC LIMIT 12"
curl -fsS http://127.0.0.1:8080/api/status
```
