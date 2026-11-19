# Hardware drivers

Select installed drivers in the core JSON startup configuration. All four roles
are optional. The core opens the bus after validating the selected packages and
their options. A configuration diagnostic appears in the core log and
`status.txt` while the core waits for a correction. Restart the core to apply
a change to a running device.

```json
{
  "drivers": {
    "python": "/opt/fwc/.venv/bin/python",
    "display": {"name": "dummy", "options": {"output": "/tmp/device-face.png"}},
    "power": {"name": "dummy", "options": {"millivolts": 3800}},
    "indicator": {"name": "dummy"},
    "input": {"name": "dummy", "options": {"pressed": false}}
  }
}
```

Choose the absolute path to the installed workspace interpreter. Omitting
`python` uses `python3` from the core process's PATH. Each configured role
requires a driver name and accepts an optional options object. See the physical
[display and ADC drivers](HARDWARE.md) for their configuration.

## Core operations

Send a JSON line to the [core Unix socket](../docs/CORE.md) with this envelope.

```json
{"op":"driver","request":{"action":"read_power"}}
```

| Action | Additional request fields | Successful reply data |
| --- | --- | --- |
| `read_power` | None | `{"millivolts":3800}` |
| `read_input` | None | `{"pressed":false}` |
| `set_indicator` | Boolean `enabled` | `null` |
| `render` | Positive integer `width`, `height` and hexadecimal string `pixels` | `null` |

A successful reply is `{"ok":true,"data":...}`. A failure returns `ok:false`
and a readable `message`. The core runs one hardware operation at a time.
Concurrent requests receive a busy reply. A driver has 30 seconds to finish.
Watchdog feeds, service polling and other bus clients continue during the call.

The monochrome frame starts at the top left. Each row contains
`ceil(width/8)` bytes, most significant bit first, with one for white.
Unused low bits in a final partial byte are padding. The bus limit is 16 KiB
including the whole JSON request and its newline. A native 122 × 250 frame
uses 8,000 hexadecimal characters.

## Driver package interface

A distribution advertises a named entry point in `fwc.drivers.display`,
`fwc.drivers.power`, `fwc.drivers.indicator` or `fwc.drivers.input`.
The core selects a unique entry point by role and configured name. The callable
receives one options dictionary and returns a driver instance. Validate options
in the factory and defer hardware I/O to the operation. Factories run during
startup checks and in a fresh helper process on each operation.

When the capture worker writes a diagnostic to `capture-status.txt` in the
state directory, the core renders its text through the selected display. It
checks every two seconds, refreshes on change, and shows “Ready” when the
worker clears the file. A failed refresh retries after 30 seconds. The
optional driver setting `status_size` selects the diagnostic frame width and
height, defaulting to `[122,250]`. During startup the core can render a
configuration error when the display selection is readable independently of
the invalid setting. Without a readable display selection, consult
`status.txt` and the structured log.

| Role | Python instance method | Expected result |
| --- | --- | --- |
| Display | `render(width, height, pixels)` | Rendered frame, passed as packed bytes |
| Power | `read_mv()` | Integer from 0 through 4294967295 |
| Indicator | `set(enabled)` | Apply the boolean LED state |
| Input | `read()` | Boolean button state |

The helper `python -m fwc.drivers.bridge` reads a single JSON object from
standard input through EOF. Fields are `role`, `spec` (the configured name and
options), `probe` (true for validation) and `args` (action fields or null).
Display args may carry a diagnostic `text` string in place of `pixels`.
Standard output carries one JSON object with `value` or an `error` string.
The helper directs driver messages to standard error. A driver should release
hardware resources before returning. The core terminates a timed-out helper.

The headless display writes a PNG if `output` is configured. Headless power
defaults to 3700 mV, input defaults to released, and the indicator accepts
state changes. Run the configuration swap check from the workspace root with
`cargo build && uv run python core/tests/driver_swap.py`.
