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
handler calls one of `widget`, `export` or `display` once.

## Test locally

From a development checkout, run `uv run --locked pytest plugins/` to exercise
the SDK, resource limits, manifest validation and example packages. Run
`just plugin-smoke` to observe continuity while a sample plugin fails and
restarts. On the board, the supplied examples are under `/usr/share/fwc/examples`.
The exporter updates `/var/lib/fwc/exports/NAME.json`; the display example
needs a configured display with 122 × 250 frame support.

For permission names, input and result contracts, limits and retry behavior,
see the [plugin host and SDK](../plugins/README.md) and
[manifest settings](configuration.md).

## Receive-only radio contract

Frame transmission is not yet implemented. The manifest reserves `radio.raw`
for this interface. The host denies it even if a device configuration lists
it. The worker accepts receive-channel selection and capture settings only.
Neither a manifest entry nor a grant enables frame transmission.
