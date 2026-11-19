"""Linux monitor interface and receive-only management-frame selection."""

import ctypes
import hashlib
import re
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path


class Instruction(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("yes", ctypes.c_ubyte),
        ("no", ctypes.c_ubyte),
        ("argument", ctypes.c_uint32),
    ]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("code", ctypes.POINTER(Instruction))]


def select_management(sock):
    # Read the little-endian radiotap length, then inspect the frame type there.
    instructions = [
        (0x30, 0, 0, 2),  # load low length byte
        (0x07, 0, 0, 0),  # save in X
        (0x30, 0, 0, 3),  # load high byte
        (0x64, 0, 0, 8),  # shift left
        (0x4C, 0, 0, 0),  # combine with X
        (0x07, 0, 0, 0),
        (0x50, 0, 0, 0),  # load byte at X
    ]
    kinds = [0x80, 0x10, 0x30, 0xA0]
    for i, kind in enumerate(kinds):
        instructions.append((0x15, len(kinds) - i, 0, kind))
    instructions.extend([(0x06, 0, 0, 0), (0x06, 0, 0, 4096)])
    code = (Instruction * len(instructions))(*(Instruction(*i) for i in instructions))
    program = Program(len(code), code)
    sock.setsockopt(
        socket.SOL_SOCKET, getattr(socket, "SO_ATTACH_FILTER", 26), bytes(program)
    )


class RadioUnavailable(Exception):
    """The selected observation device is absent."""


def supports_monitor(info):
    modes = False
    for line in info.splitlines():
        entry = line.strip()
        if entry.startswith("*") and modes:
            if entry == "* monitor":
                return True
        else:
            modes = entry in {
                "Supported interface modes:",
                "software interface modes (can always be added):",
            }
    return False


class LinuxRadio:
    def __init__(self, settings, sys_class=Path("/sys/class")):
        self.settings = settings
        self.selected = None
        self.interface = settings.interface
        self.net = sys_class / "net"
        self.radios = sys_class / "ieee80211"
        directory = str(Path(settings.directory).resolve()).encode()
        self.label = "fwc-capture-" + hashlib.sha256(directory).hexdigest()[:24]
        self.managed = False

    @staticmethod
    def command(*args):
        return subprocess.run(
            args, capture_output=True, text=True, timeout=5, check=True
        ).stdout

    def identity(self, phy):
        path = self.radios / phy
        device = (path / "device").resolve(strict=True)
        bus = (device / "subsystem").resolve(strict=True).name
        driver = (device / "driver").resolve().name
        return (path.resolve(strict=True), bus, driver)

    def select(self):
        source = self.settings.observation_interface
        requested = self.settings.phy
        if source:
            try:
                physical = (self.net / source / "phy80211").resolve(strict=True)
            except FileNotFoundError as exc:
                raise RadioUnavailable(f"waiting for {source}") from exc
            if requested and requested != physical.name:
                raise ValueError(
                    "observation interface and phy select different radios"
                )
            requested = physical.name
        candidates = (
            (requested,)
            if requested
            else sorted(
                (
                    p.name
                    for p in self.radios.glob("phy*")
                    if re.fullmatch(r"phy[0-9]+", p.name)
                ),
                key=lambda phy: int(phy[3:]),
            )
        )
        for phy in candidates:
            try:
                identity = self.identity(phy)
            except FileNotFoundError:
                continue
            _, bus, driver = identity
            if (
                bus == "sdio"
                or driver == "brcmfmac"
                or (bus != "usb" and not self.settings.phy)
            ):
                if requested:
                    raise ValueError(
                        "selected radio is reserved for connectivity or is not USB"
                    )
                continue
            try:
                info = self.command("iw", "phy", phy, "info")
            except subprocess.CalledProcessError:
                if not (self.radios / phy).exists():
                    continue
                raise
            if supports_monitor(info):
                return phy, identity
            if requested:
                raise ValueError("selected radio does not advertise monitor mode")
        raise RadioUnavailable("waiting for a monitor-capable observation radio")

    def attached(self):
        try:
            return self.identity(self.phy) == self.selected
        except FileNotFoundError:
            return False

    def check_monitor(self):
        interface = self.net / self.interface
        try:
            matches = (interface / "phy80211").resolve(strict=True) == self.selected[0]
            radiotap = (interface / "type").read_text().strip() == "803"
        except FileNotFoundError as exc:
            raise RadioUnavailable("monitor interface unavailable") from exc
        if not self.attached():
            raise RadioUnavailable("observation radio disconnected")
        if not (matches and radiotap):
            raise ValueError(
                "monitor interface belongs to another radio or has a different type"
            )

    def __enter__(self):
        self.phy, self.selected = self.select()
        interface = self.net / self.interface
        try:
            if not interface.exists():
                if not self.attached():
                    raise RadioUnavailable("observation radio disconnected")
                self.command(
                    "iw",
                    "phy",
                    self.phy,
                    "interface",
                    "add",
                    self.interface,
                    "type",
                    "monitor",
                )
                self.managed = True
            self.check_monitor()
            if self.managed:
                self.command(
                    "ip", "link", "set", "dev", self.interface, "alias", self.label
                )
            else:
                self.managed = (interface / "ifalias").read_text().strip() == self.label
            self.check_monitor()
            self.command("ip", "link", "set", "dev", self.interface, "up")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.managed:
            self.managed = False
            try:
                self.check_monitor()
            except (RadioUnavailable, ValueError):
                return
            self.command("iw", "dev", self.interface, "del")

    @contextmanager
    def listen(self, channel):
        self.check_monitor()
        self.command("iw", "dev", self.interface, "set", "channel", str(channel))
        # Bind only after installing the filter. A new socket starts each dwell.
        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, 0) as sock:
            select_management(sock)
            sock.bind((self.interface, 3))
            yield sock
