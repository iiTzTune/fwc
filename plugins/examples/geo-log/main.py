"""Associate recent network records with a dated position from the receiver."""

import json

from fwc_sdk import export, read_event

event = read_event()
window = event["settings"].get("window_seconds", 10) * 1_000_000_000
points = {p["seq"]: p for p in event["points"]}
fix = event["fix"]
if fix:
    for record in event["networks"]:
        if abs(record["time_ns"] - fix["time_ns"]) <= window:
            points.setdefault(
                record["seq"],
                {
                    "seq": record["seq"],
                    "ssid": record["ssid"],
                    "network": record["network"],
                    "time_ns": record["time_ns"],
                    "latitude": fix["latitude"],
                    "longitude": fix["longitude"],
                    "fix_time_ns": fix["time_ns"],
                },
            )

history = sorted(points.values(), key=lambda item: item["seq"])[-32:]
# Leave room for the SDK result envelope inside the bounded output file.
while len(json.dumps(history)) > 8000:
    del history[0]
export(history)
