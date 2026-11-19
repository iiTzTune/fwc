from pathlib import Path

import pytest
from fwc.capture import radio
from fwc.capture.worker import Settings


@pytest.mark.parametrize("labelled,removed", [(True, True), (False, False)])
def test_reuse_preserves_interface_ownership(tmp_path, monkeypatch, labelled, removed):
    settings = Settings("phy3", "fwcmon", str(tmp_path))
    monitor = radio.LinuxRadio(settings)
    net = tmp_path / "net"
    interface = net / "fwcmon"
    interface.mkdir(parents=True)
    (interface / "phy80211").symlink_to(tmp_path / "phy3")
    (interface / "type").write_text("803\n")
    (interface / "ifalias").write_text(monitor.label if labelled else "")
    monkeypatch.setattr(
        radio, "Path", lambda path: net if path == "/sys/class/net" else Path(path)
    )
    calls = []
    monkeypatch.setattr(monitor, "command", lambda *args: calls.append(args))
    with monitor:
        assert ("ip", "link", "set", "dev", "fwcmon", "up") in calls
    assert (("iw", "dev", "fwcmon", "del") in calls) == removed
