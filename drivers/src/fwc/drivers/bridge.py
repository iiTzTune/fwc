"""Load configured Python drivers through installed distribution entry points."""

import json
import sys
from contextlib import redirect_stdout
from importlib.metadata import entry_points

METHODS = {
    "display": "render",
    "power": "read_mv",
    "indicator": "set",
    "input": "read",
}


def resolve(role, spec, points=None):
    group = f"fwc.drivers.{role}"
    if points is None:
        points = entry_points(group=group)
    name = spec["name"]
    matches = [point for point in points if point.group == group and point.name == name]
    if not matches:
        available = ", ".join(sorted(p.name for p in points))
        raise ValueError(f"{role} driver {name!r} is missing (available: {available})")
    if len(matches) > 1:
        raise ValueError(f"multiple installed {role} drivers named {name!r}")
    driver = matches[0].load()(spec.get("options", {}))
    if not callable(getattr(driver, METHODS[role], None)):
        raise TypeError(f"{role} driver {name!r} needs method {METHODS[role]}")
    return driver


def status_frame(width, height, message):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("1", (width, height), color=1)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=12)
    margin, line_height = 4, 16
    line = ""
    y = margin
    for character in " ".join(message.split()):
        if font.getlength(line + character) > width - 2 * margin:
            prefix, space, rest = line.rpartition(" ")
            draw.text((margin, y), prefix if space else line, font=font, fill=0)
            y += line_height
            if y + line_height > height:
                return image.tobytes()
            line = rest if space else ""
        line = (line + character).lstrip()
    draw.text((margin, y), line, font=font, fill=0)
    return image.tobytes()


def dispatch(request):
    role = request["role"]
    driver = resolve(role, request["spec"])
    if request["probe"]:
        return None
    args = request["args"]
    if role == "display":
        width, height = args["width"], args["height"]
        if "text" in args:
            pixels = status_frame(width, height, args["text"])
        else:
            pixels = bytes.fromhex(args["pixels"])
        driver.render(width, height, pixels)
    elif role == "power":
        return driver.read_mv()
    elif role == "indicator":
        driver.set(args["enabled"])
    else:
        return driver.read()
    return None


def main():
    try:
        with redirect_stdout(sys.stderr):
            value = dispatch(json.load(sys.stdin))
        reply = {"value": value}
    except Exception as error:  # noqa: BLE001 - surface driver diagnostics to the core.
        reply = {"error": f"{type(error).__name__}: {error}"}
    print(json.dumps(reply))


if __name__ == "__main__":
    main()
