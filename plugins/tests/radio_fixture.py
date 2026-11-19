"""Exercise the production observation loop with a fixed virtual radio."""

import socket
import struct
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from fwc.capture.control import Commands, Control
from fwc.capture.worker import Publisher, Settings, run


class Radio:
    @contextmanager
    def listen(self, channel):
        source, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        done = threading.Event()

        def generate():
            ap = bytes.fromhex("020000000001")
            station = bytes.fromhex("020000000002")
            radio = struct.pack("<BBHIb", 0, 0, 9, 1 << 5, -49)
            addresses = bytes(3) + station + ap + ap + bytes(2)
            with source:
                source.send(radio + b"\x80" + addresses + bytes(12) + b"\x00\x04home")
                while not done.wait(0.2):
                    source.send(radio + b"\x10" + addresses + bytes(6))

        thread = threading.Thread(target=generate)
        thread.start()
        with receiver:
            try:
                yield receiver
            finally:
                done.set()
                thread.join(2)


if __name__ == "__main__":
    state = Path(sys.argv[1])
    publisher = Publisher(state / "bus.sock")
    settings = Settings(
        "phy0", "sample", str(state / "pcaps"), channels=(1,), dwell_ms=1000
    )
    try:
        with Commands(state / "bus.sock") as commands:
            run(
                settings, Radio(), publisher, control=Control(state, settings, commands)
            )
    finally:
        publisher.close()
