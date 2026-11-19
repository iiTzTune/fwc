"""Load the Pi image's required driver entry points using its installed Python."""

from importlib.metadata import distribution, entry_points

REQUIRED = [
    (
        "fwc-drivers-waveshare144lcd",
        "display",
        "waveshare144lcd",
        "fwc.waveshare144lcd:Display",
    ),
    (
        "fwc-drivers-waveshare144lcd",
        "input",
        "waveshare144lcd",
        "fwc.waveshare144lcd:Input",
    ),
    ("fwc-drivers-pisugar", "power", "pisugar-s", "fwc.pisugar:Power"),
    ("fwc-drivers", "display", "dummy", "fwc.drivers.dummy:Display"),
    ("fwc-iio", "power", "iio-voltage", "fwc.iio:Power"),
]


def check():
    for package, role, name, target in REQUIRED:
        group = f"fwc.drivers.{role}"
        try:
            installed = distribution(package)
            points = list(entry_points(group=group, name=name))
            if len(points) != 1:
                raise ValueError(f"found {len(points)} matching entry points")
            point = points[0]
            if point.dist.name != package or point.value != target:
                raise ValueError(
                    f"resolved {point.dist.name} {point.value}, expected {target}"
                )
            if not isinstance(point.load(), type):
                raise TypeError(f"{target} must load a driver class")
            print(
                f"Loaded {installed.name} {installed.version} {group}/{name}",
                flush=True,
            )
        except Exception as error:
            raise RuntimeError(f"{package} {group}/{name}: {error}") from error


if __name__ == "__main__":
    try:
        check()
    except Exception as error:
        raise SystemExit(f"FWC driver check failed: {error}") from error
    print("FWC driver check passed (4 distributions, 5 entry points)", flush=True)
