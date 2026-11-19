"""Report settled HAT control states through the core bus."""

import argparse
import time
from pathlib import Path

from fwc.capture.worker import Publisher

from . import CONTROLS, Input


class Debounce:
    def __init__(self):
        self.candidates = {}
        self.stable = {}

    def sample(self, active, now):
        changes = []
        for control in CONTROLS:
            pressed = control in active
            candidate = self.candidates.get(control)
            if candidate is None or candidate[0] != pressed:
                self.candidates[control] = (pressed, now)
            elif now - candidate[1] >= 0.03 and self.stable.get(control) != pressed:
                self.stable[control] = pressed
                changes.append({"control": control, "pressed": pressed})
        return changes


def run(driver, publisher, stopping=lambda: False):
    debounce = Debounce()
    while not stopping():
        for event in debounce.sample(driver.read_controls(), time.monotonic()):
            publisher.publish("input.event", event)
        time.sleep(0.01)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bus", type=Path)
    args = parser.parse_args()
    publisher = Publisher(args.bus)
    try:
        run(Input({}), publisher)
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
