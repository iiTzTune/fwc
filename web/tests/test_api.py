import http.client
import json
from contextlib import closing

import pytest
from conftest import publish, request
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate


def test_live_contract(device):
    port, state, config = device
    status, spec = request(port, "GET", "/openapi.json")
    assert status == 200
    validate(spec)

    def checked(method, route, body=None, expected=200):
        status, response = request(port, method, route, body)
        assert status == expected, response
        op = spec["paths"][route.split("?")[0]][method.lower()]
        schema = op["responses"][str(status)]["content"]["application/json"]["schema"]
        Draft202012Validator({"components": spec["components"], **schema}).validate(
            response
        )
        return response

    assert checked("GET", "/api/power") == {
        "capability": "none",
        "external_power": None,
        "charge_state": "unknown",
        "millivolts": None,
    }
    common = {"ssid": "Linden", "owned": True, "channel": 6, "time_ns": 123}
    for i in range(3):
        publish(
            state,
            "network.seen",
            {"network": f"02:00:00:00:00:0{i}", **common},
            f"n{i}",
        )
    publish(
        state,
        "association.event",
        {
            "network": "02:00:00:00:00:00",
            **common,
            "station": "02:00:00:00:00:ff",
            "state": "joined",
        },
        "joined",
    )
    for name in ["peer.seen", "plugin.status"]:
        data = (
            {"name": "pulse", "state": "running", "detail": "sample ready"}
            if name.startswith("plugin")
            else {"peer": "field-2", "name": "Nearby companion"}
        )
        publish(state, name, data, name)
    assert checked("GET", "/api/status")["services"][0]["name"] == "web"
    first = checked("GET", "/api/networks?limit=2")
    assert len(first["items"]) == 2 and first["items"][0]["seq"] == 4
    second = checked("GET", "/api/networks?limit=2&after=" + first["next"])
    assert len(second["items"]) == 1 and second["next"] is None
    assert checked("GET", "/api/peers")["items"][0]["peer"] == "field-2"
    assert checked("GET", "/api/plugins")["items"][0]["state"] == "running"
    values = checked("GET", "/api/config")
    values["dwell_ms"] = 1234
    assert checked("PUT", "/api/config", values) == values
    assert json.loads(config.read_text())["directory"] == "/var/lib/fwc/captures"
    assert checked("GET", "/api/config")["dwell_ms"] == 1234
    checked("PUT", "/api/config", {**values, "services": []}, 400)
    checked("GET", "/api/networks?limit=-1", expected=400)
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=4)) as conn:
        conn.request("GET", "/api/events", headers={"Last-Event-ID": "3"})
        with conn.getresponse() as stream:
            assert (
                stream.status == 200
                and stream.getheader("Content-Type") == "text/event-stream"
            )
            lines = []
            while not any(line.startswith(b"data:") for line in lines):
                line = stream.readline()
                assert line
                lines.append(line)
            event = json.loads(lines[-1][5:])
            Draft202012Validator(
                {"components": spec["components"], "$ref": "#/components/schemas/Event"}
            ).validate(event)
            assert b"id: 4\n" in lines and event["topic"] == "association.event"


@pytest.mark.parametrize(
    "change",
    [
        {"file_count": False},
        {"dwell_ms": 9},
        {"channels": []},
        {"allow_ssids": ["ø" * 20]},
    ],
)
def test_config_rejects_invalid_values(device, change):
    port, _, path = device
    original = path.read_bytes()
    values = request(port, "GET", "/api/config")[1]
    assert request(port, "PUT", "/api/config", values | change)[0] == 400
    assert path.read_bytes() == original


def test_local_requests_only(device):
    port, _, path = device
    original = path.read_bytes()
    values = request(port, "GET", "/api/config")[1]
    for headers in [
        {"Origin": "http://elsewhere.example"},
        {"Host": "elsewhere.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ]:
        assert request(port, "PUT", "/api/config", values, headers)[0] == 403
    assert path.read_bytes() == original


def test_live_plugin_grants(device):
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from fwc.capture.contract import plugin_config

    port, state, _ = device
    name = "smart-sweep"
    folder = state / "plugins" / name
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "plugins/examples" / name, folder
    )
    file = state / "plugin-config.json"
    original = {
        "plugins": {name: {"settings": {"step_ms": 80}}, "other": {"permissions": []}}
    }
    file.write_text(json.dumps(original))
    route = "/api/plugin-permissions"
    assert request(port, "GET", route)[1]["items"] == [
        {"name": name, "requested": ["radio.config", "radio.tx"], "granted": []}
    ]

    def toggle(permission, enabled=True):
        return request(
            port,
            "PUT",
            route,
            {"name": name, "permission": permission, "enabled": enabled},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(
            status == 200
            for status, _ in pool.map(toggle, ["radio.tx", "radio.config"])
        )
    assert plugin_config(state, name)[0] == {"radio.tx", "radio.config"}
    assert toggle("radio.tx", False)[0] == 200
    assert plugin_config(state, name) == ({"radio.config"}, {"step_ms": 80})
    assert json.loads(file.read_text())["plugins"]["other"] == {"permissions": []}
    assert toggle("export.write")[0] == 400
    assert toggle("radio.tx", 1)[0] == 400
    assert (
        request(
            port,
            "PUT",
            route,
            {"name": "../bad", "permission": "radio.tx", "enabled": True},
        )[0]
        == 400
    )
    spec = json.loads((folder / "manifest.json").read_text())
    spec["permissions"].append("radio.raw")
    (folder / "manifest.json").write_text(json.dumps(spec))
    assert toggle("radio.raw")[0] == 400
    denied = {"code": "permission_denied", "permission": "radio.tx"}
    publish(
        state,
        "plugin.status",
        {
            "name": name,
            "state": "unavailable",
            "detail": "grant required",
            "error": denied,
        },
        "denied",
    )
    assert request(port, "GET", "/api/plugins")[1]["items"][0]["error"] == denied
    file.write_text('{"wrong":{}}')
    assert toggle("radio.tx")[0] == 503
    assert file.read_text() == '{"wrong":{}}'
