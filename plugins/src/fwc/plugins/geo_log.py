"""Read one dated GPS fix for the geo-log snapshot."""

import calendar
import json
import os
import re
import select
import stat
import termios
import time
from datetime import datetime
from pathlib import Path

BAUD = {
    4800: termios.B4800,
    9600: termios.B9600,
    38400: termios.B38400,
    115200: termios.B115200,
}
USB_TTY = re.compile(r"/dev/tty(?:ACM|USB)[0-9]+")
BY_ID = re.compile(r"/dev/serial/by-id/[a-zA-Z0-9_.:+-]{1,240}")


def serial_path(device):
    if not isinstance(device, str) or not device:
        raise ValueError("device must be a USB serial receiver")
    if USB_TTY.fullmatch(device):
        return Path(device)
    if BY_ID.fullmatch(device):
        target = Path(device).resolve(strict=True)
        if USB_TTY.fullmatch(str(target)):
            return target
    raise ValueError("choose a USB serial receiver or its by-id path")


def coordinate(value, direction, axis):
    width, limit, positive, negative = (
        (2, 90, "N", "S") if axis == "lat" else (3, 180, "E", "W")
    )
    if not re.fullmatch(
        rf"[0-9]{{{width + 2}}}(?:\.[0-9]{{1,9}})?", value
    ) or direction not in (positive, negative):
        raise ValueError("invalid coordinate")
    minutes = float(value[width:])
    degrees = int(value[:width]) + minutes / 60
    if minutes >= 60 or degrees > limit:
        raise ValueError("coordinate outside range")
    return round(degrees if direction == positive else -degrees, 6)


def parse_fix(line, now_ns, window):
    record = re.fullmatch(
        rb"\$([\x20-\x29\x2b-\x7e]{1,100})\*([0-9a-fA-F]{2})", line.rstrip(b"\r\n")
    )
    if not record:
        return None
    body, expected = record.groups()
    check = 0
    for byte in body:
        check ^= byte
    if check != int(expected, 16):
        return None
    fields = body.decode("ascii").split(",")
    if (
        len(fields) < 10
        or not re.fullmatch(r"[A-Z]{2}RMC", fields[0])
        or fields[2] != "A"
    ):
        return None
    if not re.fullmatch(r"[0-9]{6}(?:\.[0-9]{1,9})?", fields[1]) or not re.fullmatch(
        r"[0-9]{6}", fields[9]
    ):
        return None
    hour, _, fractions = fields[1].partition(".")
    try:
        utc = datetime.strptime(fields[9] + hour, "%d%m%y%H%M%S")
        stamp = calendar.timegm(utc.timetuple()) * 1_000_000_000 + int(
            fractions.ljust(9, "0")
        )
        latitude = coordinate(fields[3], fields[4], "lat")
        longitude = coordinate(fields[5], fields[6], "lon")
    except ValueError:
        return None
    if -1_000_000_000 <= now_ns - stamp <= window * 1_000_000_000:
        return {"latitude": latitude, "longitude": longitude, "time_ns": stamp}
    return None


def read_fix(device, baud, window):
    fd = os.open(
        serial_path(device), os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW
    )
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode) or not os.isatty(fd):
            raise ValueError("device must be a serial receiver")
        attrs = termios.tcgetattr(fd)
        attrs[:6] = [
            0,
            0,
            termios.CLOCAL | termios.CREAD | termios.CS8,
            0,
            BAUD[baud],
            BAUD[baud],
        ]
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        pending = b""
        budget = 8192
        deadline = time.monotonic() + 1.25
        while budget and (remaining := deadline - time.monotonic()) > 0:
            if not select.select([fd], [], [], remaining)[0]:
                break
            chunk = os.read(fd, min(1024, budget))
            if not chunk:
                break
            budget -= len(chunk)
            *lines, pending = (pending + chunk).split(b"\n")
            for line in lines:
                fix = parse_fix(line, time.time_ns(), window)
                if fix:
                    return fix
            pending = pending[-110:]
    finally:
        os.close(fd)
    return None


def snapshot(directory, settings):
    from .host import bounded

    if set(settings) - {"device", "baud", "window_seconds"}:
        raise ValueError("unknown geo-log setting")
    window = settings.get("window_seconds", 10)
    baud = settings.get("baud", 9600)
    if type(window) is not int or not 1 <= window <= 60:
        raise ValueError("window_seconds must be between 1 and 60")
    if type(baud) is not int or baud not in BAUD:
        raise ValueError("unsupported receiver baud rate")
    try:
        points = json.loads(bounded(directory / "exports/geo-log.json", 8193))
    except FileNotFoundError:
        points = []
    if not isinstance(points, list) or len(points) > 32:
        raise ValueError("invalid geo-log history")
    fix = None
    if settings.get("device"):
        try:
            fix = read_fix(settings["device"], baud, window)
        except (OSError, termios.error):
            pass
    return {"fix": fix, "points": points}
