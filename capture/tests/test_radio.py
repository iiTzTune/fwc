from pathlib import Path

import pytest
from fwc.capture import __main__, radio
from fwc.capture.worker import Settings


def physical(root, name, bus="usb", driver="rtw_8821au"):
    phy = root / "ieee80211" / name
    device = phy / "device"
    device.mkdir(parents=True)
    for link, value in [("subsystem", bus), ("driver", driver)]:
        target = root / link / value
        target.mkdir(parents=True, exist_ok=True)
        (device / link).symlink_to(target)
    return phy


def link(root, name, phy, kind="1", alias=""):
    net = root / "net" / name
    net.mkdir(parents=True)
    (net / "phy80211").symlink_to(phy)
    (net / "type").write_text(kind)
    (net / "ifalias").write_text(alias)
    return net


def commands(monitor, monkeypatch, supported=()):
    calls = []

    def invoke(*args):
        calls.append(args)
        if args[:2] == ("iw", "phy") and args[-1] == "info":
            if args[2] in supported:
                return "Supported interface modes:\n * managed\n * monitor\n"
            return "Supported interface modes:\n * managed\n"

    monkeypatch.setattr(monitor, "command", invoke)
    return calls


@pytest.mark.parametrize("labelled", [True, False])
def test_reuse_preserves_interface_ownership(tmp_path, monkeypatch, labelled):
    monitor = radio.LinuxRadio(Settings("phy3", "fwcmon", str(tmp_path)), tmp_path)
    phy = physical(tmp_path, "phy3", "platform", "mac80211_hwsim")
    link(tmp_path, "fwcmon", phy, "803", monitor.label if labelled else "")
    calls = commands(monitor, monkeypatch, ["phy3"])
    with monitor:
        assert ("ip", "link", "set", "dev", "fwcmon", "up") in calls
    assert (("iw", "dev", "fwcmon", "del") in calls) == labelled


def test_first_usb_with_mode_in_numeric_phy_order(tmp_path, monkeypatch):
    physical(tmp_path, "phy0", "sdio", "brcmfmac")
    for phy in ["phy10", "phy1", "phy2"]:
        physical(tmp_path, phy)
    monitor = radio.LinuxRadio(Settings.parse({}), tmp_path)
    calls = commands(monitor, monkeypatch, ["phy2", "phy10"])
    assert monitor.select()[0] == "phy2"
    assert calls == [("iw", "phy", n, "info") for n in ("phy1", "phy2")]


def test_named_usb_selection_and_selector_mismatch(tmp_path, monkeypatch):
    phy = physical(tmp_path, "phy7")
    link(tmp_path, "wlx7", phy)
    monitor = radio.LinuxRadio(
        Settings.parse({"observation_interface": "wlx7"}), tmp_path
    )
    commands(monitor, monkeypatch, ["phy7"])
    assert monitor.select()[0] == "phy7"
    monitor = radio.LinuxRadio(
        Settings(phy="phy0", observation_interface="wlx7"), tmp_path
    )
    with pytest.raises(ValueError, match="different radios"):
        monitor.select()


@pytest.mark.parametrize("bus,driver", [("sdio", "other"), ("usb", "brcmfmac")])
@pytest.mark.parametrize(
    "selector", [{"phy": "phy0"}, {"observation_interface": "wlx9"}]
)
def test_connectivity_radio_is_never_claimed(
    tmp_path, monkeypatch, bus, driver, selector
):
    link(tmp_path, "wlx9", physical(tmp_path, "phy0", bus, driver))
    monitor = radio.LinuxRadio(Settings.parse(selector), tmp_path)
    calls = commands(monitor, monkeypatch, ["phy0"])
    with pytest.raises(ValueError, match="connectivity"), monitor:
        pytest.fail("reserved radio claimed")
    assert calls == []


def test_foreign_monitor_untouched_even_when_replaced(tmp_path, monkeypatch):
    own = physical(tmp_path, "phy2")
    onboard = physical(tmp_path, "phy0", "sdio", "brcmfmac")
    monitor = radio.LinuxRadio(Settings(), tmp_path)
    net = link(tmp_path, "fwcmon0", onboard, "803", monitor.label)
    calls = commands(monitor, monkeypatch, ["phy2"])
    with pytest.raises(ValueError), monitor:
        pytest.fail("existing monitor uses connectivity radio")
    assert calls == [("iw", "phy", "phy2", "info")]
    (net / "phy80211").unlink()
    (net / "phy80211").symlink_to(own)
    with monitor:
        calls.clear()
        (net / "phy80211").unlink()
        (net / "phy80211").symlink_to(onboard)
        with pytest.raises(ValueError), monitor.listen(1):
            pytest.fail("replaced monitor retuned")
    assert calls == []


def test_wait_then_use_usb_without_supervisor_restart(tmp_path, monkeypatch):
    settings = Settings()
    monitor = radio.LinuxRadio(settings, tmp_path)
    calls = commands(monitor, monkeypatch, ["phy4"])
    monkeypatch.setattr(__main__, "LinuxRadio", lambda _: monitor)
    delays = []

    def plug_in(delay):
        delays.append(delay)
        phy = physical(tmp_path, "phy4")
        link(tmp_path, "fwcmon0", phy, "803")

    from contextlib import nullcontext

    monkeypatch.setattr(__main__.time, "sleep", plug_in)
    monkeypatch.setattr(__main__, "Commands", lambda _: nullcontext())
    monkeypatch.setattr(__main__, "run", lambda *args, **kwargs: None)
    __main__.observe(settings, Path("/test/bus"), None)
    assert delays == [2]
    assert ("ip", "link", "set", "dev", "fwcmon0", "up") in calls
