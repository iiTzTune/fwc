from importlib.metadata import EntryPoint

import pytest
from fwc.drivers.bridge import resolve


def test_discovery_refuses_ambiguous_entry_points():
    point = EntryPoint("dummy", "fwc.drivers.dummy:Power", "fwc.drivers.power")
    with pytest.raises(ValueError, match="multiple"):
        resolve("power", {"name": "dummy"}, [point, point])


def test_discovery_explains_absent_driver():
    with pytest.raises(ValueError, match="available"):
        resolve("power", {"name": "unknown"}, [])


def test_dummy_power_configuration():
    point = EntryPoint("dummy", "fwc.drivers.dummy:Power", "fwc.drivers.power")
    driver = resolve(
        "power", {"name": "dummy", "options": {"millivolts": 3300}}, [point]
    )
    assert driver.read_mv() == 3300
    with pytest.raises(ValueError):
        resolve("power", {"name": "dummy", "options": {"millivolts": False}}, [point])


def test_status_frame_is_readable_within_panel():
    from fwc.drivers.bridge import status_frame
    from PIL import Image

    width, height = 122, 250
    frame = status_frame(
        width, height, "Capture settings need attention: invalid dwell_ms"
    )
    picture = Image.frombytes("1", (width, height), frame)
    dark = picture.convert("L").point(lambda p: 255 - p).getbbox()
    assert dark is not None
    x0, y0, x1, y1 = dark
    assert 0 < x0 < x1 < width
    assert 0 < y0 < y1 < height
    assert y1 - y0 > 15
