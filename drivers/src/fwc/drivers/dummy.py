"""Headless driver implementations."""

import struct
import tempfile
import zlib
from pathlib import Path


def _options(options, *keys):
    unknown = options.keys() - set(keys)
    if unknown:
        raise ValueError(f"unknown options: {sorted(unknown)}")


def _chunk(name, data):
    return (
        struct.pack(">I", len(data))
        + name
        + data
        + struct.pack(">I", zlib.crc32(name + data))
    )


class Display:
    def __init__(self, options):
        _options(options, "output")
        self.output = None
        if "output" in options:
            value = options["output"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError("output must be a PNG pathname")
            self.output = Path(value)

    def render(self, width, height, pixels):
        if self.output is None:
            return
        stride = (width + 7) // 8
        rows = b"".join(
            b"\x00" + pixels[start : start + stride]
            for start in range(0, len(pixels), stride)
        )
        header = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
        with tempfile.NamedTemporaryFile(dir=self.output.parent, delete=False) as file:
            path = Path(file.name)
        try:
            path.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", header)
                + _chunk(b"IDAT", zlib.compress(rows))
                + _chunk(b"IEND", b"")
            )
            path.replace(self.output)
        finally:
            path.unlink(missing_ok=True)


class Power:
    def __init__(self, options):
        _options(options, "millivolts")
        self.value = options.get("millivolts", 3700)
        if type(self.value) is not int or not 0 <= self.value <= 2**32 - 1:
            raise ValueError("millivolts must be an unsigned 32-bit integer")

    def read_mv(self):
        return self.value


class Input:
    def __init__(self, options):
        _options(options, "pressed")
        self.pressed = options.get("pressed", False)
        if type(self.pressed) is not bool:
            raise ValueError("pressed must be boolean")

    def read(self):
        return self.pressed


class Indicator:
    def __init__(self, options):
        _options(options)

    def set(self, enabled):
        pass
