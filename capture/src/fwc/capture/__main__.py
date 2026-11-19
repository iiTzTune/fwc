import argparse
import fcntl
import signal
from pathlib import Path

from .radio import LinuxRadio
from .worker import Publisher, read_settings, run


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
            with LinuxRadio(settings) as radio:
                run(settings, radio, publisher)
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
