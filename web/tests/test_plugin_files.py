import asyncio
import json
import shutil
import time
from pathlib import Path

from conftest import request
from fwc.capture.storage import Ring
from fwc.plugins.host import Manifest, deliver
from fwc.plugins.sandbox import invoke

EXAMPLES = Path(__file__).resolve().parents[2] / "plugins/examples"


def test_download_and_revoke(device):
    port, state, _ = device
    folder = state / "plugins/pcap-sink"
    shutil.copytree(EXAMPLES / folder.name, folder)
    package = Manifest.load(folder)
    captures = state / "captures"
    (state / "capture.json").write_text(json.dumps({"directory": str(captures)}))
    ring = Ring(captures, 128, 3)
    for i in range(3):
        assert ring.append(bytes([i]) * 60, time.time_ns())
    ring.close()
    asyncio.run(deliver(package, asyncio.run(invoke(package, {})), state))
    code, archive = request(port, "GET", "/api/captures")
    assert code == 200 and archive["items"]
    name = archive["items"][0]["name"]
    path = "/api/captures/" + name
    assert request(port, "GET", path) == (200, (captures / name).read_bytes())
    (state / "plugin-config.json").write_text(
        '{"plugins":{"pcap-sink":{"permissions":[]}}}'
    )
    assert request(port, "GET", "/api/captures") == (200, {"items": []})
    assert request(port, "GET", path)[0] == 404
