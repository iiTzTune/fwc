"""Sample GPIO character-device v2 lines on the Pi Zero 2 W."""

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
def lines(pins, flags, consumer):
    with open("/dev/gpiochip0", "rb", buffering=0) as chip:
        # The input service and one-shot HAL reader briefly sample the same lines.
        if flags & INPUT:
            fcntl.flock(chip, fcntl.LOCK_EX)
        offsets = [*pins, *([0] * (64 - len(pins)))]
        request = bytearray(
            LINE_REQUEST.pack(
                *offsets,
                consumer,
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


def read_active_low(pins, consumer, *, pull_up=False):
    flags = INPUT | ACTIVE_LOW
    if pull_up:
        flags |= PULL_UP
    with lines(pins, flags, consumer) as fd:
        mask = (1 << len(pins)) - 1
        result = bytearray(LINE_VALUES.pack(0, mask))
        fcntl.ioctl(fd, GET_VALUES, result)
        return LINE_VALUES.unpack(result)[0]
