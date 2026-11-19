"""Ask the host to retain complete observation files for the configured window."""

import json

print(json.dumps({"action": "pcap_sink"}))
