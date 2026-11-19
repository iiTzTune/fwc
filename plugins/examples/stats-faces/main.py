"""Summarize the current observation epoch on the companion display."""

import time

from fwc_sdk import display, read_event


def face(networks, settings, now_ns):
    seconds = settings.get("epoch_seconds", 300)
    if (
        set(settings) - {"epoch_seconds"}
        or type(seconds) is not int
        or not 30 <= seconds <= 3600
    ):
        raise ValueError("epoch_seconds must be an integer from 30 to 3600")
    width = seconds * 1_000_000_000
    start = now_ns - now_ns % width
    active = [row for row in networks if start <= row["time_ns"] <= now_ns]
    channels = len({row["channel"] for row in active})
    score = len(active) + max(0, channels - 1)
    mood = "(-_-) Resting" if not active else "(o_o) Curious"
    if score >= 4:
        mood = "(^_^) Busy"
    label = time.strftime("%H:%M:%S UTC", time.gmtime(start // 1_000_000_000))
    network_unit = "network" if len(active) == 1 else "networks"
    channel_unit = "channel" if channels == 1 else "channels"
    return f"{mood} | {label} | {len(active)} {network_unit}, {channels} {channel_unit} | Score {score}"


if __name__ == "__main__":
    event = read_event()
    display(face(event["networks"], event.get("settings", {}), time.time_ns()))
