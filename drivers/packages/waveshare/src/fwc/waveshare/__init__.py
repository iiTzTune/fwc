"""Full-frame companion display for the Waveshare 2.13 inch V4 panel."""


def _open_panel():
    try:
        from waveshare_epd.epd2in13_V4 import EPD
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Waveshare 2.13 inch V4 needs the board's waveshare_epd library "
            f"and SPI/GPIO bindings (missing {error.name})"
        ) from error
    return EPD()


class Display:
    def __init__(self, options):
        if options != {}:
            raise ValueError("waveshare-2in13-v4 expects an empty options object")

    def render(self, width, height, pixels):
        if (width, height) != (122, 250):
            raise ValueError(
                "Waveshare 2.13 inch V4 expects a 122 × 250 portrait frame"
            )
        if len(pixels) != 4000:
            raise ValueError("Waveshare 2.13 inch V4 expects 16 packed bytes per row")

        panel = _open_panel()
        if panel.init() != 0:
            raise OSError("Waveshare panel initialization failed")
        try:
            panel.display(bytearray(pixels))
        finally:
            panel.sleep()
