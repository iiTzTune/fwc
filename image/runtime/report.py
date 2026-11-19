"""Print the device identity and ready service names on the boot console."""

import json
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

for attempt in range(60):
    try:
        with urlopen("http://127.0.0.1:8080/api/status", timeout=2) as response:
            status = json.load(response)
        services = sorted(s["name"] for s in status["services"] if s["pid"])
        if services and all(s["pid"] for s in status["services"]):
            identity = Path("/var/lib/fwc/device-id").read_text().strip()
            print(f"FWC ready id={identity} services={','.join(services)}", flush=True)
            break
    except (URLError, OSError, ValueError):
        pass
    time.sleep(1)
