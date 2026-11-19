"""PiSugar S power-source status on the Pi Zero 2 W."""

from fwc.drivers import gpio


class Power:
    capability = "coarse"

    def __init__(self, options):
        if options != {}:
            raise ValueError("pisugar-s power expects an empty options object")

    def read_status(self):
        external = bool(gpio.read_active_low((3,), b"fwc-pisugar"))
        return {
            "external_power": external,
            "charge_state": "unknown" if external else "discharging",
            "millivolts": None,
        }

    def read_mv(self):
        raise ValueError("battery voltage unavailable with PiSugar S")
