from fwc.capture.observation import decode, peer_element
from test_observation import packet


def test_capture_identifies_only_complete_companion_elements():
    element = bytes.fromhex("dd15024657430101046e6f646502080000000000000001")
    for tail, expected in [
        (element, element.hex()),
        (element[:-1], None),
        (b"\x03\x01\x06" + element, element.hex()),
        (b"\xdd\x05other", None),
    ]:
        raw = packet(extra=tail)
        observation = decode(raw)
        assert peer_element(raw, observation) == expected
        assert observation.record == decode(packet()).record
