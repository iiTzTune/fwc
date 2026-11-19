import struct
from collections import namedtuple

from fwc.capture.storage import Ring

Usage = namedtuple("Usage", ["total", "used", "free"])


def test_growth_limits_recovery_and_watermark(tmp_path):
    usage = [Usage(10000, 0, 10000)]
    ring = Ring(tmp_path, 128, 2, lambda path: usage[0])
    for _ in range(15):
        assert ring.append(bytes(40), 10_000_000_000)
    ring.close()
    files = sorted(tmp_path.glob("*.pcap"))
    assert len(files) == 2
    assert all(f.stat().st_size <= 128 for f in files)
    assert struct.unpack_from("<I", files[-1].read_bytes())[0] == 0xA1B2C3D4
    usage[0] = Usage(10000, 8990, 1010)
    ring = Ring(tmp_path, 128, 1, lambda path: usage[0])
    assert not ring.append(bytes(40), 10_000_000_000)
    assert len(list(tmp_path.glob("*.pcap"))) == 1
    usage[0] = Usage(10000, 8000, 2000)
    assert ring.append(bytes(40), 10_000_000_000)
    ring.close()


def test_filling_volume_stops_before_threshold_and_resumes(tmp_path):
    external = [8000]

    def usage(directory):
        allocated = sum(f.stat().st_size for f in directory.glob("*.pcap"))
        used = external[0] + allocated
        return Usage(10000, used, 10000 - used)

    ring = Ring(tmp_path, 256, 8, usage)
    attempts = [ring.append(bytes(44), 1) for _ in range(100)]
    assert attempts[0] and not attempts[-1]
    high = usage(tmp_path).used
    assert 8900 <= high < 9000
    for _ in range(100):
        assert not ring.append(bytes(44), 1)
    assert usage(tmp_path).used == high
    external[0] -= 500
    assert ring.append(bytes(44), 2)
    ring.close()


def test_restart_repairs_only_the_incomplete_record(tmp_path):
    ring = Ring(tmp_path, 2048, 3, lambda _: Usage(10000, 0, 10000))
    assert ring.append(bytes(32), 1)
    ring.close()
    (path,) = tmp_path.glob("*.pcap")
    valid = path.read_bytes()
    with path.open("ab") as out:
        out.write(struct.pack("<IIII", 0, 0, 40, 40) + b"partial")
    ring = Ring(tmp_path, 2048, 3)
    assert path.read_bytes() == valid
    broken = tmp_path / "activity-00000000000000000002.pcap"
    broken.write_bytes(b"x")
    ring = Ring(tmp_path, 2048, 3)
    assert not broken.exists()
