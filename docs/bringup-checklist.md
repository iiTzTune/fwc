# Bring-up evidence ledger

Keep each item open until an operator records the board revision, software commit,
command output and observed result. Run the board checks on a designated test
device with the corresponding drivers selected in the core configuration.

## Open — Waveshare 2.13in V4 refresh

Install the vendor Python library and SPI/GPIO bindings in the driver interpreter,
enable SPI and connect the panel. From a workspace checkout on the board, send
alternating black and white horizontal bands. Observe a full refresh and count
the bands on the physical panel.

```sh
uv run python -c 'from fwc.waveshare import Display; Display({}).render(122,250,b"".join(bytes([255 if (y//25)%2 else 0])*16 for y in range(250)))'
```

## Open — Battery voltage

Connect the battery sense divider to an IIO ADC. Substitute its actual device
directory, channel and measured divider ratio. Compare the reading in mV with
a meter across the battery and record both readings and the ADC tolerance.

```sh
uv run python -c 'from fwc.iio import Power; print(Power({"device":"/sys/bus/iio/devices/iio:device0","channel":0,"divider":2}).read_mv())'
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

## Open — Hosted GitHub Actions

No remote is configured. After the maintainer selects a GitHub repository and
pushes the milestone commit, run these commands in the connected checkout.
Record the workflow URL, commit and conclusion.

```sh
run_id=$(gh run list --workflow ci.yml --commit "$(git rev-parse HEAD)" --json databaseId --jq '.[0].databaseId')
gh run watch "$run_id" --exit-status
gh run view "$run_id" --json conclusion,headSha,url
```
