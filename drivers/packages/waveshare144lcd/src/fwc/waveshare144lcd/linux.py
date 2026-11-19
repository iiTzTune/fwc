"""SPI panel transactions and HAT button input."""

import fcntl
import struct
from contextlib import contextmanager

from fwc.drivers import gpio


def read_buttons(pins):
    return gpio.read_active_low(pins, b"fwc-lcd", pull_up=True)


class PanelIO:
    def __init__(self, spi, gpio):
        self.spi, self.gpio = spi, gpio

    def reset(self, high):
        fcntl.ioctl(self.gpio, gpio.SET_VALUES, gpio.LINE_VALUES.pack(int(high), 1))

    def write(self, data_mode, data):
        fcntl.ioctl(
            self.gpio, gpio.SET_VALUES, gpio.LINE_VALUES.pack(int(data_mode) << 1, 2)
        )
        for i in range(0, len(data), 4096):
            chunk = data[i : i + 4096]
            if self.spi.write(chunk) != len(chunk):
                raise OSError("incomplete display transfer")


@contextmanager
def panel():
    with (
        open("/dev/spidev0.0", "wb", buffering=0) as spi,
        gpio.lines((27, 25), gpio.OUTPUT, b"fwc-lcd") as fd,
    ):
        # linux/spi/spidev.h write mode, bits per word, and maximum frequency.
        fcntl.ioctl(spi, 0x40016B01, bytes([0]))
        fcntl.ioctl(spi, 0x40016B03, bytes([8]))
        fcntl.ioctl(spi, 0x40046B04, struct.pack("=I", 16_000_000))
        yield PanelIO(spi, fd)
