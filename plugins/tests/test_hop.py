import asyncio
from pathlib import Path

import pytest
from fwc.plugins.host import Manifest
from fwc.plugins.sandbox import invoke

PACKAGE = Path(__file__).resolve().parents[1] / "examples/fwc-plugin-hop"


@pytest.mark.parametrize(
    ("settings", "last", "expected"),
    [
        ({}, {}, 1),
        ({"channels": [11, 3]}, {"channel": 1}, 11),
        ({"channels": [11, 3]}, {"channel": 11}, 3),
        ({"channels": [11, 3]}, {"channel": 3}, 11),
    ],
)
def test_channel_cycle(settings, last, expected):
    package = Manifest.load(PACKAGE)
    assert package.permissions == {"radio.tx"}
    result = asyncio.run(invoke(package, {"settings": settings, "last_radio": last}))
    assert result == {
        "action": "radio",
        "command": {"op": "set_channel", "channel": expected},
    }


@pytest.mark.parametrize("channels", [[], [True], [0], [234], [1, 6, 1], "1,6"])
def test_invalid_plan_returns_no_command(channels):
    with pytest.raises(ValueError, match="channel plan"):
        asyncio.run(
            invoke(Manifest.load(PACKAGE), {"settings": {"channels": channels}})
        )
