"""Resolve the device file list without creating an image."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for item in json.loads((root / "image/device.json").read_text())["files"]:
    source = root / item["source"]
    if not source.is_file():
        raise SystemExit(f"Missing image input: {source}")
    print(f"{source.relative_to(root)} -> {item['destination']}")
