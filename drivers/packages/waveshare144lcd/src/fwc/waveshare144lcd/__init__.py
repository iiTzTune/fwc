"""Waveshare 128 × 128 companion display and HAT controls."""

import struct
import time

from . import linux

CONTROLS = {
    "up": 6,
    "down": 19,
    "left": 5,
    "right": 26,
    "center": 13,
    "button1": 21,
    "button2": 20,
}

# Panel timings, power, gamma, RGB565 and memory orientation from the
# Waveshare 1.44-inch HAT reference initialization (default U2D_R2L).
INITIALIZE = (
    (0xB1, "012c2d"),
    (0xB2, "012c2d"),
    (0xB3, "012c2d012c2d"),
    (0xB4, "07"),
    (0xC0, "a20284"),
    (0xC1, "c5"),
    (0xC2, "0a00"),
    (0xC3, "8a2a"),
    (0xC4, "8aee"),
    (0xC5, "0e"),
    (0xE0, "0f1a0f182f2820221f1b233700070210"),
    (0xE1, "0f1b0f17332c292e3030393f00070310"),
    (0xF0, "01"),
    (0xF6, "00"),
    (0x3A, "05"),
    (0x36, "68"),
)


def paint(io, pixels):
    for high in (True, False, True):
        io.reset(high)
        time.sleep(0.01)
    for register, value in INITIALIZE:
        io.write(False, bytes([register]))
        io.write(True, bytes.fromhex(value))
    time.sleep(0.2)
    io.write(False, b"\x11")
    time.sleep(0.12)
    io.write(False, b"\x29")
    # The controller RAM starts one column and two rows before the panel.
    for register, start in ((0x2A, 1), (0x2B, 2)):
        io.write(False, bytes([register]))
        io.write(True, struct.pack(">HH", start, start + 127))
    io.write(False, b"\x2c")
    io.write(True, pixels)


class Display:
    def __init__(self, options):
        if options != {}:
            raise ValueError("waveshare144lcd display expects an empty options object")

    def render(self, width, height, pixels):
        from PIL import Image, ImageOps

        if (width, height) not in ((128, 128), (122, 250)):
            raise ValueError("expected native 128 × 128 or portrait 122 × 250 frame")
        if len(pixels) != ((width + 7) // 8) * height:
            raise ValueError("invalid packed display frame length")
        frame = Image.frombytes("1", (width, height), pixels).convert("L")
        if frame.size != (128, 128):
            frame = ImageOps.pad(
                frame, (128, 128), method=Image.Resampling.LANCZOS, color=255
            )
        # Encode grayscale (including shaded edges on scaled frames) to RGB565.
        shades = [
            struct.pack(">H", ((v >> 3) << 11) | ((v >> 2) << 5) | (v >> 3))
            for v in range(256)
        ]
        words = b"".join(shades[v] for v in frame.tobytes())
        with linux.panel() as io:
            paint(io, words)


class Input:
    def __init__(self, options):
        if options != {}:
            raise ValueError("waveshare144lcd input expects an empty options object")

    def read_controls(self):
        bits = linux.read_buttons(tuple(CONTROLS.values()))
        return [name for index, name in enumerate(CONTROLS) if bits & (1 << index)]

    def read(self):
        return bool(self.read_controls())
