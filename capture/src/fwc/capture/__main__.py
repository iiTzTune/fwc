import argparse
import fcntl
import signal
import subprocess
import sys
import time
from pathlib import Path

from .control import Commands, Control
from .radio import LinuxRadio, RadioUnavailable
from .worker import Publisher, read_settings, run


def observe(settings, bus, publisher):
    previous = None
    while True:
        radio = LinuxRadio(settings)
        try:
            with radio, Commands(bus) as commands:
                previous = None
                run(
                    settings,
                    radio,
                    publisher,
                    control=Control(bus.parent, settings, commands),
                )
            return
        except RadioUnavailable as error:
            message = str(error)
        except (OSError, subprocess.SubprocessError):
            if radio.selected is None or radio.attached():
                raise
            message = "observation radio disconnected"
        if previous != message:
            print(message, file=sys.stderr, flush=True)
            previous = message
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description="Run a wireless observation session")
    parser.add_argument("config", type=Path)
    parser.add_argument("bus", type=Path)
    args = parser.parse_args()
    settings = read_settings(args.config, args.bus.parent)
    directory = Path(settings.directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    publisher = Publisher(args.bus)

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        with (directory / "capture.lock").open("a") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observe(settings, args.bus, publisher)
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
