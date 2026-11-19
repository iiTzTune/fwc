import errno
from contextlib import contextmanager

import pytest
from fwc.drivers import bridge, gpio

SPEC = {"name": "pisugar-s"}
STATUS = {
    "role": "power",
    "spec": SPEC,
    "probe": False,
    "args": {"action": "power_status"},
}


def test_gpio3_wall_power_edges(monkeypatch):
    # Simulated physical levels, replayed as the logical bits Linux returns.
    levels = iter([1, 0, 1])

    @contextmanager
    def acquire(pins, flags, consumer):
        assert pins == (3,) and consumer == b"fwc-pisugar"
        assert flags == gpio.INPUT | gpio.ACTIVE_LOW
        yield 31

    def ioctl(fd, request, values):
        assert (fd, request) == (31, gpio.GET_VALUES)
        assert gpio.LINE_VALUES.unpack(values) == (0, 1)
        values[:] = gpio.LINE_VALUES.pack(int(next(levels) == 0), 1)

    monkeypatch.setattr(gpio, "lines", acquire)
    monkeypatch.setattr(gpio.fcntl, "ioctl", ioctl)
    for external in [False, True, False]:
        assert bridge.dispatch(STATUS) == {
            "capability": "coarse",
            "external_power": external,
            "charge_state": "unknown" if external else "discharging",
            "millivolts": None,
        }


def test_gpio_error_remains_a_driver_error(monkeypatch):
    def denied(*args):
        raise PermissionError(errno.EACCES, "GPIO access denied")

    monkeypatch.setattr(gpio, "read_active_low", denied)
    assert bridge.dispatch(STATUS | {"probe": True}) is None
    with pytest.raises(PermissionError):
        bridge.dispatch(STATUS)
    with pytest.raises(ValueError, match="battery voltage unavailable"):
        bridge.dispatch(STATUS | {"args": None})


def test_settings_cannot_enable_voltage_or_shutdown():
    with pytest.raises(ValueError):
        bridge.dispatch(
            STATUS
            | {
                "spec": {"name": "pisugar-s", "options": {"threshold_mv": 3400}},
                "probe": True,
            }
        )


def test_voltage_only_selection_remains_usable():
    dummy = STATUS | {"spec": {"name": "dummy", "options": {"millivolts": 3800}}}
    assert bridge.dispatch(dummy | {"args": None}) == 3800
    assert bridge.dispatch(dummy) == {
        "capability": "none",
        "external_power": None,
        "charge_state": "unknown",
        "millivolts": None,
    }


@pytest.mark.parametrize(
    ("capability", "voltage"),
    [("coarse", 3600), ("full", None), ("full", False), ("other", None)],
)
def test_reject_mismatched_capabilities(capability, voltage):
    from types import SimpleNamespace

    driver = SimpleNamespace(
        capability=capability,
        read_status=lambda: {
            "external_power": True,
            "charge_state": "unknown",
            "millivolts": voltage,
        },
    )
    with pytest.raises(ValueError):
        bridge.power_status(driver)
