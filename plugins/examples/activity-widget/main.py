from fwc_sdk import read_event, widget

records = read_event()["networks"]
widget(f"Networks on the first page: {len(records)}")
