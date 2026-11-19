"""Select the next receive channel after the last confirmed transition."""

from fwc_sdk import read_event, set_channel

snapshot = read_event()
channels = snapshot["settings"].get("channels", [1, 6, 11])
if (
    not isinstance(channels, list)
    or not 1 <= len(channels) <= 233
    or not all(type(channel) is int and 1 <= channel <= 233 for channel in channels)
    or len(set(channels)) != len(channels)
):
    raise ValueError("channel plan requires distinct integers from 1 to 233")

previous = snapshot.get("last_radio", {}).get("channel")
index = (channels.index(previous) + 1) % len(channels) if previous in channels else 0
set_channel(channels[index])
