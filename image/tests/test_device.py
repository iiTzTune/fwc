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


def test_pi_board_configuration_and_virtual_profile(tmp_path):
    config = builder.validate(json.loads((ROOT / "pi-zero-2w/device.json").read_text()))
    assert config["architecture"] == "aarch64"
    root = tmp_path / "root/usr/share/fwc"
    root.mkdir(parents=True)
    builder.populate_data(tmp_path, config)
    data = tmp_path / "data"
    core = json.loads((data / "core.json").read_text())
    assert core["watchdog"] == "/dev/watchdog0"
    assert (
        core["drivers"]["display"]["name"]
        == core["drivers"]["input"]["name"]
        == "waveshare144lcd"
    )
    assert core["drivers"]["power"]["name"] == "pisugar-s"
    assert core["drivers"]["status_size"] == [128, 128]
    assert {s["name"] for s in core["services"]} == {
        "web",
        "plugins",
        "capture",
        "input",
    }
    virtual = json.loads((root / "qemu-core.json").read_text())
    assert virtual["services"] == [s for s in core["services"] if s["name"] != "input"]
    capture = json.loads((data / "capture.json").read_text())
    assert capture["interface"] == "fwcmon0" and "phy" not in capture
