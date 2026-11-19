# Observation plugins

The core supervises the Python host as a service. Point it at a directory of
installed plugins and the core state directory. The port must match the companion
web service. The host and web service may start in either order.

```json
{
  "name": "plugins",
  "command": ["/opt/fwc/.venv/bin/python", "-m", "fwc.plugins", "/etc/fwc/plugins", "/var/lib/fwc", "--port", "8080"],
  "restarts": 2,
  "window_secs": 60
}
```

Linux needs bubblewrap 0.8 or newer, user namespaces, libseccomp.so.2 and
/usr/bin/python3 with its standard library. The host uses Python 3.11–3.13.
Display output also uses the installed FWC drivers package and Pillow. Install
an example by copying its whole directory below the configured plugin
directory. The host accepts up to 32 installed packages. Start with activity-widget or file-export for headless operation.
Select a 122 × 250 display driver before enabling status-display. Restart the
host after editing installed manifests or scripts.

## Manifest and execution

Each directory has a matching lowercase plugin name, a manifest.json and one
main.py. The version 1 manifest requires every field in this example.

```json
{
  "name": "activity-widget",
  "api_version": 1,
  "permissions": ["networks.read", "widget.write"],
  "interval_seconds": 30,
  "max_restarts": 3,
  "budget": {"seconds": 3, "cpu_seconds": 1, "memory_mib": 48}
}
```

Names use up to 40 lowercase ASCII letters, digits and hyphens, starting with
a letter. Intervals range from 1 to 3600 seconds. The per-run budgets range
from 1 to 60 seconds of elapsed time, 1 to 10 seconds of CPU and 32 to 96 MiB
of virtual memory. Output and diagnostic files each have an 8 KiB limit.
The host allows up to two plugin processes at once. After each successful run,
it waits one interval. A failed run consumes one of 0–10 allowed restarts.
Retries wait 1, 2, 4 or more seconds, capped at 30. A successful run resets
the failure counter. An exhausted plugin stays unavailable until the host
restarts. A version mismatch produces a logged diagnostic before execution.
The dashboard and configured 122 × 250 display receive manifest diagnostics.

The host copies the selected script into a fresh private Linux namespace for
each run. The runner has read-only interpreter libraries and SDK, private
network and process namespaces, and a system call allowlist. HTTP, core bus
and display access take place in the host. If the namespace or process limits
cannot be applied, the run fails and counts toward its restart budget.

## SDK and results

Import helpers from fwc_sdk in main.py. read_event() reads a typed Snapshot
from standard input. Produce exactly one JSON result with one SDK helper.
Installed scripts can use the Python standard library. The examples show
each supported output.

| Permission | Input or result |
| --- | --- |
| status.read | Snapshot.status contains committed activity and client counts from GET /api/status. |
| networks.read | Snapshot.networks contains up to 16 records from the first page of GET /api/networks. |
| widget.write | widget(text) updates the plugin detail on the M4 dashboard. |
| export.write | export(records) atomically replaces STATE_DIRECTORY/exports/NAME.json. |
| display.write | display(text) renders a short message through the configured HAL display. |

Only the declared input fields are present. Widget and display text allow
1–160 characters. For the file exporter, the host chooses the destination
and limits the encoded list to 8 KiB. The host records lifecycle changes as
plugin.status events and retries a pending event with the same ID if the bus
disconnects. The companion service presents the latest state at GET /api/plugins.

## Verification

Run `uv run pytest plugins/` for the manifest, SDK, resource limits and real
service tests. Run `just plugin-smoke` for the two ten-second failures with
a supervised capture loop and a live companion API.

For the device grant table, channel and dwell commands, and current package
examples, see the [plugin author guide](../docs/plugin-author.md). The
[configuration reference](../docs/configuration.md) lists per-plugin settings.
