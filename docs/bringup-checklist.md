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

## Open — Battery voltage

Connect the battery sense divider to an IIO ADC. Substitute its actual device
directory, channel and measured divider ratio. Compare the reading in mV with
a meter across the battery and record both readings and the ADC tolerance.

```sh
python3 -c 'from fwc.iio import Power; print(Power({"device":"/sys/bus/iio/devices/iio:device0","channel":0,"divider":2}).read_mv())'
```

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
