# Companion hardware drivers

The two Python distributions register their factories with the HAL. Selecting a
driver loads and validates its options. Hardware access happens when the HAL
requests a frame or a voltage reading.

## Waveshare 2.13 inch V4

Install `fwc-waveshare` and select `waveshare-2in13-v4` for the display with
`{}` as its options. This adapter uses the monochrome V4 HAT in portrait
orientation. On the board, enable SPI, connect the HAT using its standard
pinout, and install the manufacturer's Raspberry Pi Python library and its
GPIO/SPI dependencies into the driver's Python environment. The module
`waveshare_epd.epd2in13_V4` comes from the vendor's
`RaspberryPi_JetsonNano/python` package. [Vendor package](https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python)

Each render supplies a complete 122 × 250 frame, 16 bytes per row, with a
set bit representing white. The driver initializes the panel, runs a full
refresh and puts it to sleep. A refresh is a blocking hardware operation.
[Panel implementation](https://github.com/waveshareteam/e-Paper/blob/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd2in13_V4.py)

## Linux IIO battery ADC

Install `fwc-iio` and select `iio-voltage` for power. The ADC needs a Linux
IIO driver exposing raw and scale attributes for a voltage input. Example
options for channel 2 with a two-to-one voltage divider follow.

```json
{"device":"/sys/bus/iio/devices/iio:device0","channel":2,"divider":2}
```

Choose the device and channel wired to the battery sense input on the board.
The device directory is required. Channel defaults to zero and divider to one.
Use the ratio of battery voltage to ADC input voltage. Each reading applies
`(raw + offset) × scale × divider` and rounds to the nearest millivolt. The
driver reads channel-specific calibration first, then shared voltage
calibration. An absent offset defaults to zero. A missing scale or unavailable
sample reports a read error. [Linux IIO attribute definitions](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-bus-iio)

## Verification

Run `just golden-render` to compare the dummy display output and the bytes sent
to the Waveshare backend with the checked-in reference PNG. The test frame
marks all four edges and has an asymmetric corner mark to reveal pixel packing
and orientation errors. Run `uv run pytest drivers/tests/test_iio.py` to check
ADC calibration and error handling against a temporary IIO attribute tree.
