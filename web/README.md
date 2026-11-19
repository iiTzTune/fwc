# Companion web service

The core supervises a loopback HTTP service. It uses the core socket for the
live snapshot and a read-only SQLite connection for committed observations.
The service, static assets and OpenAPI document must share a directory.
Use the installed workspace interpreter so the web service can validate
settings with the capture worker's parser.

Add this service to the core configuration, adjusting the installed paths.
The second path argument must name the configuration used by the capture
worker. Open `http://127.0.0.1:8080/` from the device.

```json
{
  "name":"web",
  "command":["/opt/fwc/.venv/bin/python","/opt/fwc/web/server.py","/var/lib/fwc","/etc/fwc/capture.json","--port","8080"],
  "restarts":3,
  "window_secs":60
}
```

The dashboard shows the process-lifetime counters, plugin states, peers and
new activity. The records page shows the latest committed observation for
each network identifier. Settings are saved atomically while keeping the
radio, interface and capture directory selected by the operator. Saved values
take effect on the next capture worker start. The plain status page at
`/fallback` needs no scripts. Its file, `fallback.html`, also opens offline.

## HTTP contract

The service publishes OpenAPI 3.1 at `/openapi.json`. Local clients use
`127.0.0.1` or `localhost` and the configured port. Browser writes use the
same origin and JSON content type. Settings accept channels, dwell time,
file size/count and the two SSID lists. The request body limit is 16 KiB.
All six editable properties must be present on each PUT.

| Method and path | Result |
| --- | --- |
| GET /api/status | Service names, start counts and PIDs, bus client count, committed events, dropped metrics and queue overflows. |
| GET /api/networks | A page in ascending network-identifier order. Supply `limit` (1–100) and `after` (previous `next` key). |
| GET /api/peers | Latest report per peer, ordered by descending sequence, capped at 256. |
| GET /api/plugins | Latest plugin state per name, ordered by descending sequence, capped at 256. |
| GET or PUT /api/config | Current editable settings or saved replacement values. |
| GET /api/events | Server-sent events for new durable activity. Reconnect with `Last-Event-ID` to resume after a sequence. |

A producer publishes `peer.seen` with string fields `peer` (stable identifier)
and `name` (display name). The plugin host publishes durable `plugin.status`
events with string fields `name`, `state` and `detail`. State is one of
`running`, `waiting`, `stopped` or `unavailable`. Each state transition
uses a new stable event ID under the normal core publish/acknowledge protocol.
Peer and plugin lists include the latest committed sequence as `seq`.

The SSE stream emits `event: activity`, `id` equal to the committed
sequence and a JSON `data` line with `seq`, `id`, `topic`, and `data`.
An initial connection starts at the current end of the activity log. A client
can request all retained events with `Last-Event-ID: 0`. Up to four streams
can be active. Transient bus metrics are available through the core bus.

## Verification

Run `uv run pytest web/` with matching Chromium and chromedriver executables
on PATH. Tests launch the core and its supervised web worker, publish actual
bus events, check HTTP responses against the served schema, and navigate the
dashboard, record pages, settings form and plain status page in Chromium.
Run `just api-schema-check` for the live API contract checks alone.
