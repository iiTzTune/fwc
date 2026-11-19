# Waveshare 1.44-inch LCD HAT

Both entry points use the configuration key `waveshare144lcd` and an empty
options object. Set the core driver `status_size` to `[128,128]` for native
status frames. The display also fits and centers existing 122 × 250 device
faces, then transfers RGB565 to the ST7735S using the vendor default scan
orientation. Native input pixels are packed MSB first. The visible address
window starts at column 1 and row 2 in this orientation.

On the Pi Zero 2 W enable `dtparam=spi=on` and `gpio=24=op,dh` in the boot
firmware configuration to hold the backlight on. Set `gpio=27=op,dh,pu` to
idle the reset line high. Give the selected services access to
`/dev/spidev0.0` and `/dev/gpiochip0`. The driver selects SPI mode 0, eight
bits per word and a 16 MHz clock. Rendering sets BCM27 (reset) and BCM25
(data/command) through the Linux GPIO v2 interface. The SPI device manages
BCM8 (CE0), BCM11 (clock) and BCM10 (data). Initialization, full-frame transfer
and release occur in each HAL operation.

| Control | BCM GPIO |
| --- | --- |
| up | 6 |
| down | 19 |
| left | 5 |
| right | 26 |
| center | 13 |
| button1 (KEY1) | 21 |
| button2 (KEY2) | 20 |

The input reader requests active-low lines with internal pull-ups. HAL
`read_input` returns whether any of these controls is pressed. The board
configuration can supervise the following companion service for individual
press and release events.

```sh
python3 -m fwc.waveshare144lcd.input_events /var/lib/fwc/bus.sock
```

The service samples every 10 ms and publishes one `input.event` after a
control has settled for 30 ms, including each control's initial state.
Its payload contains `control` and boolean `pressed`. The publisher retries
an unacknowledged event with the same identifier when reconnecting. Sampling
resumes after delivery. A tap entirely within an outage can go unobserved.
The vendor table also assigns KEY3 to BCM16; this device profile uses KEY1 and
KEY2.

Panel orientation, scan offsets, backlight persistence and all seven controls
await physical confirmation. [Waveshare pinout](https://www.waveshare.com/wiki/1.44inch_LCD_HAT)
and [Waveshare reference driver](https://files.waveshare.com/upload/f/fa/1.44inch-LCD-HAT-Code.7z).
