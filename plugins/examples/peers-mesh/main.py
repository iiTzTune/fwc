from fwc_sdk import export, read_event
from peer_wire import peer_records

snapshot = read_event()
export(peer_records(snapshot.get("peers", []), snapshot.get("peer_elements", [])))
