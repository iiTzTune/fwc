# Receive dwell by client density

Smart-sweep refreshes a dwell plan every five seconds. For each channel, the
worker adds `step_ms` per recently observed client to `min_dwell_ms`, up to
`max_dwell_ms`. A channel with no recent clients uses `min_dwell_ms`. The
worker continues scanning the device's configured channels in order.

| Device setting | Default | Range |
| --- | --- | --- |
| `window_seconds` | 60 | 1–300 |
| `min_dwell_ms` | 250 | 10–60000 |
| `max_dwell_ms` | 1750 | minimum dwell through 60000 |
| `step_ms` | 75 | 1–60000 |

The host examines the latest 4096 association events and selects observations
within the time window. A repeated join counts once. A join on another network
moves the client to the new channel. A departure from the current network
removes it. Only per-channel counts reach the plugin.

Set both `radio.tx` and `radio.config` in this plugin's device grants.
Revoking either restores the device dwell at the next dwell boundary.
