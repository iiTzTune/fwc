"""Set a bounded receive dwell for each channel with recent clients."""

from fwc_sdk import configure_radio, read_event


def milliseconds(settings, key, default):
    value = settings.get(key, default)
    low = 1 if key == "step_ms" else 10
    if type(value) is not int or not low <= value <= 60000:
        raise ValueError(f"{key} must be an integer from {low} through 60000")
    return value


event = read_event()
settings = event["settings"]
if set(settings) - {"window_seconds", "min_dwell_ms", "max_dwell_ms", "step_ms"}:
    raise ValueError("unknown smart-sweep setting")
minimum = milliseconds(settings, "min_dwell_ms", 250)
maximum = milliseconds(settings, "max_dwell_ms", 1750)
step = milliseconds(settings, "step_ms", 75)
if minimum > maximum:
    raise ValueError("min_dwell_ms exceeds max_dwell_ms")
plan = {
    channel: min(maximum, minimum + step * count)
    for channel, count in event["client_density"].items()
}
configure_radio(dwell_ms=minimum, dwell_by_channel=plan)
