import json
import shutil
from pathlib import Path

from conftest import request
from test_browser import browser  # noqa: F401

PACKAGE = Path(__file__).resolve().parents[2] / "plugins/examples/geo-log"


def test_records_map_and_grant_removal(device, browser):  # noqa: F811
    port, state, _ = device
    shutil.copytree(PACKAGE, state / "plugins/geo-log")
    (state / "exports").mkdir()
    record = {
        "seq": 3,
        "network": "02:00:00:00:00:01",
        "ssid": "Field",
        "time_ns": 1,
        "fix_time_ns": 2,
        "latitude": 41.12345,
        "longitude": -88.09123,
    }
    (state / "exports/geo-log.json").write_text(json.dumps([record]))
    assert request(port, "GET", "/api/map") == (200, {"items": [record]})
    browser.open(f"http://127.0.0.1:{port}/records")
    browser.visible("#geo-map")
    browser.text("#coordinates", "Field · 41.12345, -88.09123")
    (state / "plugin-config.json").write_text(
        '{"plugins":{"geo-log":{"permissions":[]}}}'
    )
    assert request(port, "GET", "/api/map") == (200, {"items": []})
