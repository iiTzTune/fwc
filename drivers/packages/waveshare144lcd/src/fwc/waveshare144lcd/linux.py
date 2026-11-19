"""Pi Zero 2 W GPIO character-device v2 and spidev transfers."""

import fcntl
import os
import struct
from contextlib import contextmanager

# linux/gpio.h, struct gpio_v2_line_request. Each of the ten attributes
# consists of an attribute ID, padding, a 64-bit value, and its line mask.
LINE_REQUEST = struct.Struct("=64I32sQI5I240sII5Ii")
LINE_VALUES = struct.Struct("=QQ")
GET_LINES = (3 << 30) | (LINE_REQUEST.size << 16) | 0xB407
GET_VALUES = (3 << 30) | (LINE_VALUES.size << 16) | 0xB40E
SET_VALUES = (3 << 30) | (LINE_VALUES.size << 16) | 0xB40F
INPUT = 1 << 2
OUTPUT = 1 << 3
ACTIVE_LOW = 1 << 1
PULL_UP = 1 << 8


@contextmanager
def lines(pins, flags):
    with open("/dev/gpiochip0", "rb", buffering=0) as chip:
        # The input service and one-shot HAL reader briefly sample the same lines.
        if flags & INPUT:
            fcntl.flock(chip, fcntl.LOCK_EX)
        offsets = [*pins, *([0] * (64 - len(pins)))]
        request = bytearray(
            LINE_REQUEST.pack(
                *offsets,
                b"fwc-lcd",
                flags,
                0,
                *([0] * 5),
                b"",
                len(pins),
                0,
                *([0] * 5),
                -1,
            )
        )
        fcntl.ioctl(chip, GET_LINES, request)
        fd = struct.unpack_from("=i", request, len(request) - 4)[0]
        try:
            yield fd
        finally:
            os.close(fd)


def read_buttons(pins):
    with lines(pins, INPUT | ACTIVE_LOW | PULL_UP) as fd:
        mask = (1 << len(pins)) - 1
        result = bytearray(LINE_VALUES.pack(0, mask))
        fcntl.ioctl(fd, GET_VALUES, result)
        return LINE_VALUES.unpack(result)[0]


class PanelIO:
    def __init__(self, spi, gpio):
        self.spi, self.gpio = spi, gpio

    def reset(self, high):
        fcntl.ioctl(self.gpio, SET_VALUES, LINE_VALUES.pack(int(high), 1))

    def write(self, data_mode, data):
        fcntl.ioctl(self.gpio, SET_VALUES, LINE_VALUES.pack(int(data_mode) << 1, 2))
        for i in range(0, len(data), 4096):
            chunk = data[i : i + 4096]
            if self.spi.write(chunk) != len(chunk):
                raise OSError("incomplete display transfer")


@contextmanager
def panel():
    with (
        open("/dev/spidev0.0", "wb", buffering=0) as spi,
        lines((27, 25), OUTPUT) as fd,
    ):
        # linux/spi/spidev.h write mode, bits per word, and maximum frequency.
        fcntl.ioctl(spi, 0x40016B01, bytes([0]))
        fcntl.ioctl(spi, 0x40016B03, bytes([8]))
        fcntl.ioctl(spi, 0x40046B04, struct.pack("=I", 16_000_000))
        yield PanelIO(spi, fd)
