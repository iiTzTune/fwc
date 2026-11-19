import errno
import struct
from contextlib import contextmanager
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from fwc import waveshare144lcd as hat
from fwc.waveshare144lcd import input_events, linux
from PIL import Image

GOLDEN = Path(__file__).with_name("golden") / "frame.png"
PINS = [
    ("up", 6),
    ("down", 19),
    ("left", 5),
    ("right", 26),
    ("center", 13),
    ("button1", 21),
    ("button2", 20),
]


class SimulatedST7735S:
    def __init__(self):
        self.reset = []
        self.commands = []
        self.data_mode = None
        self.chunks = []

    def ioctl(self, fd, request, data):
        assert fd == 123 and request == linux.SET_VALUES
        bits, mask = linux.LINE_VALUES.unpack(data)
        if mask == 1:
            self.reset.append(bool(bits & 1))
        else:
            assert mask == 2
            self.data_mode = bool(bits & 2)
        return 0

    def write(self, chunk):
        if self.data_mode:
            self.commands[-1][1].extend(chunk)
            if self.commands[-1][0] == 0x2C:
                self.chunks.append(len(chunk))
        else:
            (register,) = chunk
            self.commands.append((register, bytearray()))
        return len(chunk)

    def frame(self):
        registers = dict(self.commands)
        assert registers[0x3A] == b"\x05" and registers[0x36] == b"\x68"
        assert struct.unpack(">HH", registers[0x2A]) == (1, 128)
        assert struct.unpack(">HH", registers[0x2B]) == (2, 129)
        words = list(struct.iter_unpack(">H", registers[0x2C]))
        assert len(words) == 128 * 128
        return Image.frombytes(
            "L", (128, 128), bytes((word[0] >> 11) * 255 // 31 for word in words)
        )


@pytest.fixture
def panel(monkeypatch):
    simulated = SimulatedST7735S()
    monkeypatch.setattr(linux.fcntl, "ioctl", simulated.ioctl)
    monkeypatch.setattr(hat.time, "sleep", lambda _: None)

    @contextmanager
    def open_panel():
        yield linux.PanelIO(simulated, 123)

    monkeypatch.setattr(linux, "panel", open_panel)
    return simulated


def test_golden_frame_and_spi_replay(panel):
    display = entry_points(group="fwc.drivers.display")["waveshare144lcd"].load()({})
    raw = bytearray([255] * 2048)
    for y in range(128):
        for x in range(128):
            if (
                x == 0
                or y == 127
                or (13 <= x <= 53 and 17 <= y <= 31)
                or (x == 127 and y == 0)
            ):
                raw[y * 16 + x // 8] &= ~(0x80 >> (x % 8))
    display.render(128, 128, raw)
    assert panel.reset == [True, False, True]
    assert [cmd for cmd, _ in panel.commands] == [
        0xB1,
        0xB2,
        0xB3,
        0xB4,
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC4,
        0xC5,
        0xE0,
        0xE1,
        0xF0,
        0xF6,
        0x3A,
        0x36,
        0x11,
        0x29,
        0x2A,
        0x2B,
        0x2C,
    ]
    assert panel.chunks == [4096] * 8
    with Image.open(GOLDEN) as expected:
        assert panel.frame().tobytes() == expected.convert("L").tobytes()


def test_existing_portrait_frame_is_fitted(panel):
    hat.Display({}).render(122, 250, bytes(4000))
    image = panel.frame()
    assert image.getpixel((0, 0)) == image.getpixel((127, 127)) == 255
    assert image.getpixel((64, 0)) == image.getpixel((64, 127)) == 0
    with Image.open(GOLDEN.with_name("portrait.png")) as expected:
        assert image.tobytes() == expected.convert("L").tobytes()


def test_short_transfer_fails(monkeypatch, panel):
    monkeypatch.setattr(panel, "write", lambda _: 0)
    with pytest.raises(OSError, match="incomplete display transfer"):
        hat.Display({}).render(128, 128, bytes(2048))


@pytest.mark.parametrize("control,pin", PINS)
def test_input_table_and_active_low(monkeypatch, control, pin):
    @contextmanager
    def acquire(pins, flags):
        assert pins == tuple(pin for _, pin in PINS)
        assert flags == linux.INPUT | linux.ACTIVE_LOW | linux.PULL_UP
        yield 101

    def ioctl(fd, request, values):
        assert fd == 101 and request == linux.GET_VALUES
        assert linux.LINE_VALUES.unpack(values)[1] == 127
        # The kernel returns logical active bits for physical low inputs.
        values[:] = linux.LINE_VALUES.pack(
            1 << list(dict(PINS).values()).index(pin), 127
        )

    monkeypatch.setattr(linux, "lines", acquire)
    monkeypatch.setattr(linux.fcntl, "ioctl", ioctl)
    driver = entry_points(group="fwc.drivers.input")["waveshare144lcd"].load()({})
    assert driver.read_controls() == [control]
    assert driver.read() is True


def test_release_and_denied_read(monkeypatch):
    driver = hat.Input({})
    monkeypatch.setattr(linux, "read_buttons", lambda pins: 0)
    assert driver.read() is False

    def denied(pins):
        raise PermissionError(errno.EACCES, "access denied")

    monkeypatch.setattr(linux, "read_buttons", denied)
    with pytest.raises(PermissionError):
        driver.read()


def test_event_service_debounces_controls_and_preserves_chords(monkeypatch):
    frames = [
        [],
        [],
        ["up"],
        [],
        ["up", "button1"],
        ["up", "button1"],
        ["button1"],
        ["button1"],
    ]
    ticks = iter([0, 0.04, 0.05, 0.06, 0.07, 0.11, 0.12, 0.16])
    observed = []

    class Driver:
        def read_controls(self):
            return frames.pop(0)

    class Bus:
        def publish(self, topic, data):
            observed.append((topic, data))

    monkeypatch.setattr(input_events.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(input_events.time, "sleep", lambda _: None)
    input_events.run(Driver(), Bus(), lambda: not frames)
    assert observed == [
        *(("input.event", {"control": c, "pressed": False}) for c, _ in PINS),
        ("input.event", {"control": "up", "pressed": True}),
        ("input.event", {"control": "button1", "pressed": True}),
        ("input.event", {"control": "up", "pressed": False}),
    ]


def test_config_and_frame_validation():
    with pytest.raises(ValueError):
        hat.Input({"pin": 16})
    with pytest.raises(ValueError):
        hat.Display({"spi": 5})
    with pytest.raises(ValueError):
        hat.Display({}).render(128, 128, bytes(128))
