from importlib import metadata

import pytest


def power(options):
    return metadata.entry_points(group="fwc.drivers.power")["iio-voltage"].load()(
        options
    )


def test_adc_uses_shared_attributes_and_reads_live_values(tmp_path):
    (tmp_path / "in_voltage2_raw").write_text("1900\n")
    (tmp_path / "in_voltage_scale").write_text("1.125\n")
    (tmp_path / "in_voltage_offset").write_text("-100\n")
    driver = power({"device": str(tmp_path), "channel": 2, "divider": 2})
    assert driver.read_mv() == 4050
    (tmp_path / "in_voltage2_raw").write_text("1800\n")
    (tmp_path / "in_voltage2_scale").write_text("0.75\n")
    (tmp_path / "in_voltage2_offset").write_text("10\n")
    assert driver.read_mv() == 2715


def test_optional_offset_and_missing_scale(tmp_path):
    (tmp_path / "in_voltage0_raw").write_text("4311\n")
    driver = power({"device": str(tmp_path)})
    with pytest.raises(FileNotFoundError):
        driver.read_mv()
    (tmp_path / "in_voltage_scale").write_text("0.5\n")
    assert driver.read_mv() == 2156


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"device": "relative"},
        {"device": "/adc", "channel": True},
        {"device": "/adc", "divider": 0},
        {"device": "/adc", "chanel": 1},
        {"device": "/adc", "divider": float("inf")},
    ],
)
def test_invalid_options(options):
    with pytest.raises(ValueError):
        power(options)


@pytest.mark.parametrize(
    ("raw", "scale"), [("-1", "1"), ("bad", "1"), ("10", "nan"), ("10", "0")]
)
def test_invalid_readings(tmp_path, raw, scale):
    (tmp_path / "in_voltage0_raw").write_text(raw)
    (tmp_path / "in_voltage_scale").write_text(scale)
    with pytest.raises(ValueError):
        power({"device": str(tmp_path)}).read_mv()
