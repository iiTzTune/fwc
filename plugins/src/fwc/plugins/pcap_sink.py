"""Retain bounded snapshots of complete capture files for the local dashboard."""

import errno
import json
import os
import re
import stat
import struct
import time
from contextlib import contextmanager
from pathlib import Path

from fwc.capture.contract import plugin_config, require
from fwc.capture.storage import HEADER

NAME = re.compile(r"activity-[0-9]{20}\.pcap")
ARCHIVE = "retained-pcap"
MAX_FILE = 8 * 1024**2
MAX_TOTAL = 32 * 1024**2
MAX_FILES = 32


def cutoff(settings):
    seconds = settings.get("window_seconds", 3600)
    if type(seconds) is not int or not 1 <= seconds <= 604800:
        raise ValueError("window_seconds must be an integer from 1 through 604800")
    return time.time_ns() - seconds * 1_000_000_000


@contextmanager
def folder(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def entries(fd):
    result = {}
    for name in os.listdir(fd):
        if NAME.fullmatch(name):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                result[name] = (info.st_mtime_ns, info.st_size)
    return result


def read_regular(fd, name, oldest):
    if not NAME.fullmatch(name):
        raise FileNotFoundError(name)
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise FileNotFoundError(name) from error
        raise
    with os.fdopen(handle, "rb") as stream:
        info = os.fstat(handle)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mtime_ns < oldest
            or not len(HEADER) < info.st_size <= MAX_FILE
        ):
            raise FileNotFoundError(name)
        # A bounded prefix ends at the observed size even if capture appends later.
        data = stream.read(info.st_size)
        if len(data) != info.st_size:
            raise FileNotFoundError(name)
        return info.st_mtime_ns, data


def complete(data):
    if not data.startswith(HEADER):
        return False
    pos = len(HEADER)
    while pos < len(data):
        if pos + 16 > len(data):
            return False
        _, micros, size, original = struct.unpack_from("<IIII", data, pos)
        if not 0 < size == original <= 4096 or micros >= 1_000_000:
            return False
        pos += 16 + size
    return pos == len(data)


def available(fd, amount):
    fs = os.fstatvfs(fd)
    used = fs.f_blocks - fs.f_bfree
    return (used * fs.f_frsize + amount) * 10 < (used + fs.f_bavail) * fs.f_frsize * 9


def replace(fd, name, data, changed):
    # The host runs one interval at a time. Clean up any interrupted prior copy.
    temporary = ".pcap-part"
    try:
        os.unlink(temporary, dir_fd=fd)
    except FileNotFoundError:
        pass
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.utime(handle, ns=(changed, changed))
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def sync(directory, settings):
    """Copy recent complete snapshots from the device's configured capture ring."""
    oldest = cutoff(settings)
    source = Path(
        json.loads((directory / "capture.json").read_text()).get(
            "directory", "captures"
        )
    )
    archive = directory / ARCHIVE
    archive.mkdir(mode=0o700, exist_ok=True)
    with folder(archive) as dest:
        retained = entries(dest)
        for name, (stamp, size) in retained.copy().items():
            if stamp < oldest or size > MAX_FILE:
                os.unlink(name, dir_fd=dest)
                del retained[name]
        try:
            with folder(source) as src:
                observed = entries(src)
                for name in sorted(
                    observed, key=lambda n: (observed[n][0], n), reverse=True
                ):
                    stamp, size = observed[name]
                    if stamp < oldest:
                        break
                    if size > MAX_FILE or observed[name] == retained.get(name):
                        continue
                    try:
                        stamp, data = read_regular(src, name, oldest)
                    except FileNotFoundError:
                        continue
                    if not complete(data) or not available(dest, len(data)):
                        continue
                    selected = retained | {name: (stamp, len(data))}
                    total = sum(nbytes for _, nbytes in selected.values())
                    for candidate in sorted(
                        selected, key=lambda n: (selected[n][0], n)
                    ):
                        if len(selected) <= MAX_FILES and total <= MAX_TOTAL:
                            break
                        total -= selected.pop(candidate)[1]
                    if name not in selected:
                        break
                    for expired in retained.keys() - selected.keys():
                        os.unlink(expired, dir_fd=dest)
                    replace(dest, name, data, stamp)
                    retained = selected
        except FileNotFoundError:
            pass  # The capture directory can appear after the plugin starts.
    return f"{len(retained)} capture files retained"


def read_settings(directory):
    granted, settings = plugin_config(directory, "pcap-sink")
    require(granted, ["export.write"])
    return cutoff(settings)


def list_files(directory):
    oldest = read_settings(directory)
    try:
        with folder(directory / ARCHIVE) as fd:
            files = entries(fd)
    except FileNotFoundError:
        return []
    return [
        {"name": name, "size": size, "updated_ns": stamp}
        for name, (stamp, size) in sorted(
            files.items(), key=lambda entry: (entry[1][0], entry[0]), reverse=True
        )
        if stamp >= oldest and len(HEADER) < size <= MAX_FILE
    ]


def read_file(directory, name):
    oldest = read_settings(directory)
    with folder(directory / ARCHIVE) as fd:
        _, data = read_regular(fd, name, oldest)
        return data
