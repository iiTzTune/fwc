# Configuration reference

On the reference image, the writable volume holds `/var/lib/fwc/core.json`,
`/var/lib/fwc/capture.json` and the `plugins/` directory. The core validates
service and driver selections at startup. Capture settings saved through the
dashboard take effect the next time the worker starts. A plugin manifest is
read at host startup. The device description is used when building an image.

This reference is generated from the [configuration schemas](settings.schema.json),
the [published API schema](../web/openapi.json) and the worker defaults.
Driver names resolve through installed package entry points. The option
sections below cover the included packages. [Driver setup](../drivers/README.md)

## Core settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `services` | list of service | `[]` | Supervised processes. Service names must be distinct. |
| `watchdog` | string or null | Optional | Linux watchdog device to feed during service checks. |
| `drivers` | registry | `{}` | Driver selections available to the core. |

## Service entries

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `name` | string | Required | Distinct nonblank name. Pattern `\S`. |
| `command` | list of string | Required | Program followed by its arguments. The first element must be nonblank. Minimum items 1. |
| `restarts` | integer (1–4294967295) | Required | Restart attempts allowed per window after the initial start. |
| `window_secs` | integer (1–4294967295) | Required | Rolling restart window in seconds. |

## Driver registry

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `python` | string or null | Optional | Driver interpreter. Defaults to python3 on PATH. |
| `status_size` | [integer (64–1024), integer (32–1024)] or null | `[122, 250]` | Diagnostic frame width and height in pixels. |
| `display` | display or null | Optional | Select a display driver by name and options. |
| `power` | power or null | Optional | Select a power driver by name and options. |
| `indicator` | indicator or null | Optional | Select an indicator driver by name and options. |
| `input` | input or null | Optional | Select an input driver by name and options. |

## Driver selection

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `name` | string | Required | Installed entry-point name. Pattern `\S`. |
| `options` | object | `{}` | Options accepted by the selected driver. |

## IIO voltage options

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `device` | string | Required | Absolute path on the device. Pattern `^/`. |
| `channel` | integer ≥ 0 | `0` | IIO voltage channel number. |
| `divider` | number > 0 | `1` | Ratio of battery voltage to input voltage. |

## Headless display options

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `output` | string | Optional | Optional path for the rendered PNG. Pattern `\S`. |

## Headless power options

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `millivolts` | integer (0–4294967295) | `3700` | Reading returned to the core. |

## Headless input options

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `pressed` | boolean | `false` | Button state returned to the core. |

The Waveshare V4 display and headless indicator accept an empty options object.

## Capture worker

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `phy` | string | Required | Dedicated physical radio. Pattern `^phy[0-9]+$`. |
| `interface` | string | Required | Monitor interface name. Pattern `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}$`. |
| `directory` | string | `"captures"` | Directory for rotating observation files. Pattern `^[^\u0000]*$`. |
| `channels` | list of integer (1–233) | `[1, 6, 11]` | Channel numbers in observation order. Minimum items 1. |
| `dwell_ms` | integer (10–60000) | `1000` | Milliseconds spent on each channel. |
| `max_bytes` | integer (128–2147483648) | `4194304` | Maximum bytes in one pcap file, including its header. |
| `file_count` | integer (1–1024) | `8` | Maximum number of observation files retained. |
| `allow_ssids` | list of string | `[]` | Selected network names. An empty list observes all eligible names. |
| `deny_ssids` | list of string | `[]` | Excluded network names. This list takes precedence. |

The HTTP settings object includes all six editable fields. The API accepts at most 233 channel entries. Each SSID is at most 32 UTF-8 bytes.

## Device plugin settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `plugins` | object | Required | Entries keyed by installed manifest name. |

In `/var/lib/fwc/plugin-config.json`, each `plugins.NAME` entry has these fields. The file is limited to 65536 bytes. Use Settings → Plugin permissions to change individual grants while preserving other settings. [Permission defaults and denial behavior](plugin-author.md)

## Per-plugin entry

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `permissions` | list of status.read, networks.read, widget.write, export.write, display.write, radio.tx, radio.config, radio.raw, network.out | Optional | Explicit grants replace the five legacy defaults. |
| `settings` | object | `{}` | Values read by this plugin on its next run. |

## fwc-plugin-hop settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `channels` | list of integer (1–233) | `[1, 6, 11]` | Receive channels in rotation order. Minimum items 1. Maximum items 233. |

## pcap-sink settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `window_seconds` | integer (1–604800) | `3600` | Maximum age of a retained file from its last write. |

## geo-log settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `device` | string | Optional | USB receiver path under /dev/ttyACM*, /dev/ttyUSB* or /dev/serial/by-id/. Omit to pause new fixes. |
| `baud` | 4800, 9600, 38400, 115200 | `9600` | Receiver serial line speed. |
| `window_seconds` | integer (1–60) | `10` | Maximum fix age and allowed time difference between a fix and a network record. |

## export-web settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `endpoint` | string | Required | Exact HTTPS destination for aggregate observation summaries. Pattern `^https://`. |

## stats-faces settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `epoch_seconds` | integer (30–3600) | `300` | Duration of each score epoch. |

## smart-sweep settings

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `window_seconds` | integer (1–300) | `60` | Age limit for observed client activity. |
| `min_dwell_ms` | integer (10–60000) | `250` | Receive dwell with zero observed clients. |
| `max_dwell_ms` | integer (10–60000) | `1750` | Upper dwell bound, at least min_dwell_ms. |
| `step_ms` | integer (1–60000) | `75` | Additional dwell per observed client. |

The keys above go under `plugins.NAME.settings`. The peers-mesh, activity-widget, file-export and status-display packages have no device-specific setting keys. The manifest's `interval_seconds` controls the time between completed invocations.

## Plugin manifest

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `name` | string | Required | Must match its directory name. Pattern `^[a-z][a-z0-9-]{0,39}$`. |
| `api_version` | 1 | Required | Plugin SDK contract implemented by main.py. |
| `permissions` | list of status.read, networks.read, widget.write, export.write, display.write, radio.tx, radio.config, radio.raw, network.out | Required | Inputs the host supplies and results the plugin may produce. |
| `interval_seconds` | integer (1–3600) | Required | Delay in seconds following a successful invocation. |
| `max_restarts` | integer (0–10) | Required | Retries allowed after consecutive failed invocations. |
| `budget` | budget | Required | Per-invocation resource limits. |

## Plugin budget

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `seconds` | integer (1–60) | Required | Elapsed seconds per invocation. |
| `cpu_seconds` | integer (1–10) | Required | CPU seconds per invocation. |
| `memory_mib` | integer (32–96) | Required | Address-space limit in MiB. |

## Image description

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `board` | string | Required | Reference board and firmware. |
| `architecture` | "x86_64" | Required | Target processor architecture. |
| `slot_mib` | integer ≥ 384 | Required | Capacity of each OS slot in MiB. |
| `data_mib` | integer ≥ 32 | Required | Capacity of the persistent volume in MiB. |
| `epoch` | integer ≥ 315532800 | Required | Unix timestamp used for reproducible assembly. |
| `files` | list of mapping | Required | File mappings. Paths must not contain parent traversal. |

## Image file mapping

| Field | Value | Presence or default | Use |
| --- | --- | --- | --- |
| `source` | string | Required | Path relative to the project root. |
| `destination` | string | Required | Absolute path on the device. Pattern `^/`. |
