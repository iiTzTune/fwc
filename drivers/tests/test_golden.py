"""Compare displayed pixels to a fixed reference across both display packages."""

from importlib import metadata
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

WIDTH, HEIGHT = 122, 250
GOLDEN = Path(__file__).parent / "golden" / "device-face.png"


def face_frame():
    # Each span is an inclusive black rectangle on the white portrait display.
    spans = [
        (0, 0, 121, 1),
        (0, 248, 121, 249),
        (0, 0, 1, 249),
        (120, 0, 121, 249),
        (25, 97, 33, 105),
        (84, 97, 92, 105),
        (43, 137, 77, 139),
        (39, 132, 42, 136),
        (78, 132, 81, 136),
        (4, 5, 14, 8),
    ]
    stride = (WIDTH + 7) // 8
    pixels = bytearray([255]) * (stride * HEIGHT)
    for left, top, right, bottom in spans:
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                pixels[y * stride + x // 8] &= ~(128 >> (x % 8))
    return bytes(pixels)


def display_factory(name):
    return metadata.entry_points(group="fwc.drivers.display")[name].load()


def assert_golden(image):
    with Image.open(GOLDEN) as reference:
        assert image.size == reference.size
        assert image.convert("L").tobytes() == reference.convert("L").tobytes()


def test_dummy_golden(tmp_path):
    output = tmp_path / "frame.png"
    display_factory("dummy")({"output": str(output)}).render(
        WIDTH, HEIGHT, face_frame()
    )
    with Image.open(output) as image:
        assert_golden(image)


def test_panel_golden(monkeypatch):
    from fwc import waveshare

    panel = Mock()
    panel.init.return_value = 0
    monkeypatch.setattr(waveshare, "_open_panel", lambda: panel)
    display_factory("waveshare-2in13-v4")({}).render(WIDTH, HEIGHT, face_frame())
    panel.init.assert_called_once_with()
    panel.sleep.assert_called_once_with()
    (frame,) = panel.display.call_args.args
    assert len(frame) == 4000
    assert_golden(Image.frombytes("1", (WIDTH, HEIGHT), bytes(frame)))


def test_panel_returns_to_sleep_after_transfer_error(monkeypatch):
    from fwc import waveshare

    panel = Mock()
    panel.init.return_value = 0
    panel.display.side_effect = OSError("transfer incomplete")
    monkeypatch.setattr(waveshare, "_open_panel", lambda: panel)
    with pytest.raises(OSError, match="transfer incomplete"):
        waveshare.Display({}).render(WIDTH, HEIGHT, face_frame())
    panel.sleep.assert_called_once_with()


def test_panel_validates_before_open(monkeypatch):
    from fwc import waveshare

    open_panel = Mock()
    monkeypatch.setattr(waveshare, "_open_panel", open_panel)
    with pytest.raises(ValueError):
        waveshare.Display({"port": 0})
    driver = waveshare.Display({})
    with pytest.raises(ValueError):
        driver.render(121, HEIGHT, face_frame())
    with pytest.raises(ValueError):
        driver.render(WIDTH, HEIGHT, b"")
    open_panel.assert_not_called()
