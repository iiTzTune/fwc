"""Summarize the host's bounded network sample by receive channel."""

import json
from collections import Counter

from fwc_sdk import read_event

records = read_event()["networks"]
counts = Counter(record["channel"] for record in records)
summary = {
    "version": 1,
    "networks": len(records),
    "owned": sum(record["owned"] for record in records),
    "channels": [
        {"channel": channel, "networks": count}
        for channel, count in sorted(counts.items())
    ],
}
print(json.dumps({"action": "upload_summary", "summary": summary}))
