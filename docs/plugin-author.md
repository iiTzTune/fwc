# Write a plugin

A plugin receives an observation snapshot and writes one result. The host
collects the requested inputs from the local API, starts a new process for each
invocation, and routes its result to the dashboard, a local file or the display.
The plugin imports the version 1 SDK as `fwc_sdk`.

## A widget for owned networks

Create a directory named `owned-count`. Put these two files in it. The manifest
asks to read network records and write a dashboard widget.

**manifest.json**

```json
{
  "name": "owned-count",
  "api_version": 1,
  "permissions": ["networks.read", "widget.write"],
  "interval_seconds": 30,
  "max_restarts": 2,
  "budget": {"seconds": 3, "cpu_seconds": 1, "memory_mib": 48}
}
```

**main.py**

```python
from fwc_sdk import read_event, widget

records = read_event()["networks"]
owned = sum(record["owned"] for record in records)
widget(f"{owned} owned networks among {len(records)} sampled")
```

`networks.read` supplies up to 16 network records. The count describes that
sample. Each record includes the name, identifier, current ownership flag,
channel, timestamp and activity sequence.

## Install and observe

On the board, copy the complete directory to `/var/lib/fwc/plugins/owned-count`.
Reboot the board so the host discovers the new manifest. Inspect the dashboard
or request the latest plugin status locally.

```sh
curl -fsS http://127.0.0.1:8080/api/plugins
```

The running widget's detail contains the sampled counts. A refused manifest
has an unavailable state and a diagnostic. Keep the declared name equal to
the directory name. The SDK has one result per invocation, so each successful
handler writes a single result with its chosen SDK helper.

## Test locally

From a development checkout, run `uv run --locked pytest plugins/` to exercise
the SDK, resource limits, manifest validation and example packages. Run
`just plugin-smoke` to observe continuity while a sample plugin fails and
restarts. On the board, the supplied examples are under `/usr/share/fwc/examples`.
The exporter updates `/var/lib/fwc/exports/NAME.json`. The display example
needs a configured display with 122 × 250 frame support.

For permission names, input and result contracts, limits and retry behavior,
see the [plugin host and SDK](../plugins/README.md) and
[manifest settings](configuration.md).

## Receive-only radio contract

Frame transmission is not yet implemented. The manifest reserves `radio.raw`
for this interface. The host denies it even if a device configuration lists
it. The worker accepts receive-channel selection and capture settings only.
Neither a manifest entry nor a grant enables frame transmission.

## Request and grant permissions

List every permission the plugin needs in its manifest. Open **Settings →
Plugin permissions** and select a requested permission beside the installed
plugin name. Each click saves that grant in `plugin-config.json`. The host
checks grants before invoking the plugin and again before delivering its
result. A missing grant leaves the plugin unavailable. The host reports
the following typed error in `plugin.status` and `GET /api/plugins`, then
checks for a restored grant on its next retry.

```json
{"code":"permission_denied","permission":"radio.tx"}
```

| Manifest permission | Capability | Default grant |
| --- | --- | --- |
| `status.read` | Session status. The bundled peers-mesh plugin also consumes the nearby peer list. | On |
| `networks.read` | Up to 16 network records per snapshot. | On |
| `widget.write` | A dashboard text result. | On |
| `display.write` | A companion display result. | On |
| `export.write` | A local export. The bundled pcap-sink plugin retains complete capture records. | On |
| `network.out` | The bundled export-web adapter posts a signed summary to the configured HTTPS receiver. | Off |
| `radio.tx` | Select a receive channel through the worker. | Off |
| `radio.config` | With `radio.tx`, change the running scanner or capture retention settings. | Off |
| `radio.raw` | Reserved frame transmission seam. | Unavailable |

Defaults apply when the device entry omits `permissions`. An explicit array
replaces those defaults. An empty array revokes all grants for that plugin.
A grant can be used only when the manifest also requests it.

For example, install `fwc-plugin-hop` from the supplied examples and save this
device configuration to rotate between channels 1 and 6.

```json
{"plugins":{"fwc-plugin-hop":{"permissions":["radio.tx"],"settings":{"channels":[1,6]}}}}
```

The host associates a radio result with the invoking plugin and publishes the
request on `radio/command`. Use `fwc_sdk.set_channel(channel)` to select a
receive channel or `fwc_sdk.configure_radio(**changes)` to submit a settings
change. Supported keys are `channels`, `dwell_ms`, `dwell_by_channel`,
`max_bytes` and `file_count`. A per-channel dwell map uses decimal channel
strings and millisecond values. The worker checks current grants before
applying changes at a dwell boundary. It publishes `radio/state` after
tuning succeeds, including `channel`, `mode`, `dwell_ms` and
`tx_enabled: false`. The matching `radio/result` carries the request ID
and the confirmed state or typed error. Revoking a required grant removes
the corresponding override at the next dwell boundary.

## Supplied packages

| Package | Result |
| --- | --- |
| `fwc-plugin-hop` | Cycles the configured receive channels using the last confirmed transition. |
| `pcap-sink` | Offers retained pcap downloads on Records. |
| `geo-log` | Associates dated USB receiver fixes with network records for the Records map. |
| `peers-mesh` | Merges received FWC elements and HTTP peer observations for local export. [Wire format and vectors](peer-wire.md) |
| `export-web` | Posts aggregate counts with a destination-specific signature. [Receiver setup](../plugins/examples/export-web/README.md) |
| `stats-faces` | Shows epoch statistics and a device face. [Score calculation](../plugins/examples/stats-faces/README.md) |
| `smart-sweep` | Adjusts receive dwell by observed client density. [Dwell settings](../plugins/examples/smart-sweep/README.md) |
| `activity-widget`, `file-export`, `status-display` | Basic examples of a dashboard widget, local file and display result. |

The [configuration reference](configuration.md) lists package setting keys,
ranges and defaults.
