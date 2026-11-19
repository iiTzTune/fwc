"""Read battery voltage from a Linux IIO ADC channel."""

import math
from pathlib import Path


class Power:
    def __init__(self, options):
        if not isinstance(options, dict) or options.keys() - {
            "device",
            "channel",
            "divider",
        }:
            raise ValueError(
                "iio-voltage expects device, optional channel and optional divider"
            )
        device = options.get("device")
        if not isinstance(device, str) or not Path(device).is_absolute():
            raise ValueError("device must be an absolute IIO device directory")
        self.device = Path(device)
        self.channel = options.get("channel", 0)
        if type(self.channel) is not int or self.channel < 0:
            raise ValueError("channel must be a nonnegative integer")
        self.divider = options.get("divider", 1)
        if (
            type(self.divider) not in (int, float)
            or not math.isfinite(self.divider)
            or self.divider <= 0
        ):
            raise ValueError("divider must be a positive finite number")

    def _attribute(self, name):
        path = self.device / f"in_voltage{self.channel}_{name}"
        try:
            return float(path.read_text())
        except FileNotFoundError:
            return float((self.device / f"in_voltage_{name}").read_text())

    def read_mv(self):
        raw = int((self.device / f"in_voltage{self.channel}_raw").read_text())
        scale = self._attribute("scale")
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("IIO voltage scale must be positive and finite")
        try:
            offset = self._attribute("offset")
        except FileNotFoundError:
            offset = 0
        voltage = (raw + offset) * scale * self.divider
        if not math.isfinite(voltage) or voltage < 0:
            raise ValueError("ADC returned an invalid battery voltage")
        return math.floor(voltage + 0.5)
