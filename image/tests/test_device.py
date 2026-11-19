import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("board_image", ROOT / "build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_declared_board():
    config = json.loads((ROOT / "device.json").read_text())
    assert builder.validate(config)["architecture"] == "x86_64"
    for key, value in (("slot_mib", True), ("data_mib", 1), ("architecture", "other")):
        with pytest.raises(ValueError):
            builder.validate(config | {key: value})
    with pytest.raises(ValueError):
        builder.validate(config | {"extra": 1})
