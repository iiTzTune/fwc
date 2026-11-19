"""Linux monitor interface and receive-only management-frame selection."""

import ctypes
import hashlib
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


class LinuxRadio:
    def __init__(self, settings):
        self.phy, self.interface = settings.phy, settings.interface
        directory = str(Path(settings.directory).resolve()).encode()
        self.label = "fwc-capture-" + hashlib.sha256(directory).hexdigest()[:24]
        self.managed = False

    @staticmethod
    def command(*args):
        return subprocess.run(
            args, capture_output=True, text=True, timeout=5, check=True
        ).stdout

    def __enter__(self):
        interface = Path("/sys/class/net") / self.interface
        if not interface.exists():
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
        try:
            if (interface / "phy80211").resolve().name != self.phy or (
                interface / "type"
            ).read_text().strip() != "803":
                raise ValueError(
                    "selected interface must be a radiotap monitor on the configured radio"
                )
            if self.managed:
                self.command(
                    "ip", "link", "set", "dev", self.interface, "alias", self.label
                )
            else:
                self.managed = (interface / "ifalias").read_text().strip() == self.label
            self.command("ip", "link", "set", "dev", self.interface, "up")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.managed:
            self.command("iw", "dev", self.interface, "del")

    @contextmanager
    def listen(self, channel):
        self.command("iw", "dev", self.interface, "set", "channel", str(channel))
        # Bind only after installing the filter. A new socket starts each dwell.
        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, 0) as sock:
            select_management(sock)
            sock.bind((self.interface, 3))
            yield sock
