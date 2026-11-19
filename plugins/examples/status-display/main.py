from fwc_sdk import display, read_event

records = read_event()["networks"]
name = records[0]["ssid"] if records else "No observations yet"
display(f"Field Wireless Companion    {name}")
