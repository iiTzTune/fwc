import json
import time
from contextlib import contextmanager

import pytest
from fwc.capture.control import Commands, Control
from fwc.capture.worker import Settings, run


class Publisher:
    def __init__(self):
        self.records = []

    def publish(self, topic, data):
        self.records.append((topic, data))


def submit(commands, plugin, command, expires=None):
    commands.pending.put_nowait(
        {
            "id": str(time.monotonic_ns()),
            "data": {
                "plugin": plugin,
                "command": command,
                "expires": time.monotonic() + 5 if expires is None else expires,
            },
        }
    )


def test_worker_checks_live_grants_before_tuning(tmp_path):
    settings = Settings("phy0", "sample", str(tmp_path / "files"), channels=(1,))
    config = tmp_path / "plugin-config.json"
    config.write_text(
        json.dumps({"plugins": {"scanner": {"permissions": ["radio.tx"]}}})
    )
    commands = Commands(tmp_path / "bus.sock")
    commands.connected.set()
    control = Control(tmp_path, settings, commands)
    output = Publisher()
    tuned = []

    class Radio:
        @contextmanager
        def listen(self, channel):
            tuned.append(channel)
            yield self

        def settimeout(self, timeout):
            pass

        def recv(self, size):
            if len(tuned) == 1:
                submit(commands, "scanner", {"op": "set_channel", "channel": 11})
                config.write_text('{"plugins":{}}')
            raise TimeoutError

    submit(commands, "scanner", {"op": "set_channel", "channel": 6})
    run(settings, Radio(), output, lambda: len(tuned) == 3, control=control)
    assert tuned == [6, 1, 1]
    assert [d["channel"] for t, d in output.records if t == "radio/state"] == [6, 1]
    assert [
        d["error"] for t, d in output.records if t == "radio/result" and not d["ok"]
    ] == [{"code": "permission_denied", "permission": "radio.tx"}]


@pytest.mark.parametrize(
    "changes",
    [{"channels": [True]}, {"dwell_ms": 3}, {"phy": "phy1"}, {"file_count": -2}],
)
def test_invalid_config_leaves_capture_settings_intact(tmp_path, changes):
    control = Control(
        tmp_path, Settings("phy0", "fwcmon0"), Commands(tmp_path / "bus.sock")
    )
    (tmp_path / "plugin-config.json").write_text(
        '{"plugins":{"scan":{"permissions":["radio.config"]}}}'
    )
    output = Publisher()
    submit(control.commands, "scan", {"op": "configure", "changes": changes})
    assert control.prepare(output) is None
    assert not control.overrides
    assert output.records[0][1]["error"]["code"] == "invalid_command"


def test_config_is_bounded_by_its_owner_grant(tmp_path):
    control = Control(
        tmp_path, Settings("phy0", "fwcmon0"), Commands(tmp_path / "bus.sock")
    )
    grants = tmp_path / "plugin-config.json"
    grants.write_text('{"plugins":{"scan":{"permissions":["radio.config"]}}}')
    output = Publisher()
    changes = {"channels": [1, 6], "dwell_ms": 300, "max_bytes": 500, "file_count": 3}
    submit(control.commands, "scan", {"op": "configure", "changes": changes})
    assert control.prepare(output)
    assert control.overrides == {"radio.config": ("scan", changes)}
    submit(control.commands, "scan", {"op": "set_channel", "channel": 11})
    control.prepare(output)
    assert output.records[-1][1]["error"]["permission"] == "radio.tx"
    grants.write_text("{")
    control.prepare(output)
    assert not control.overrides


@pytest.mark.parametrize("op", ["send_frame", "probe_request"])
def test_frame_commands_have_no_handler(tmp_path, op):
    control = Control(
        tmp_path, Settings("phy0", "fwcmon0"), Commands(tmp_path / "bus.sock")
    )
    output = Publisher()
    submit(control.commands, "scan", {"op": op})
    assert control.prepare(output) is None
    assert not control.overrides
    assert output.records[-1][1]["error"]["code"] == "invalid_command"
