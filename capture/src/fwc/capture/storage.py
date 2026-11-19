"""A bounded rotating set of standard 802.11 pcap files."""

import errno
import os
import re
import shutil
import struct

HEADER = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 4096, 105)


class Ring:
    def __init__(self, directory, max_bytes, count, usage=shutil.disk_usage):
        self.directory = directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.max_bytes, self.count, self.usage = max_bytes, count, usage
        self.files = sorted(
            path
            for path in directory.glob("activity-*.pcap")
            if re.fullmatch(r"activity-[0-9]{20}\.pcap", path.name)
        )
        self.index = (
            int(self.files[-1].stem.removeprefix("activity-")) if self.files else 0
        )
        self.file = None
        for path in self.files.copy():
            if path.stat().st_size > max_bytes:
                path.unlink()
                self.files.remove(path)
        self.prune(count)
        if self.files:
            self.repair_tail()

    def repair_tail(self):
        path = self.files[-1]
        with path.open("rb+") as stream:
            size = os.fstat(stream.fileno()).st_size
            if stream.read(len(HEADER)) == HEADER:
                offset = len(HEADER)
                while offset + 16 <= size:
                    stream.seek(offset)
                    _, _, length, original = struct.unpack("<IIII", stream.read(16))
                    if (
                        not 0 < length == original <= 4096
                        or offset + 16 + length > size
                    ):
                        break
                    offset += 16 + length
                if offset < size:
                    stream.truncate(offset)
                return
        path.unlink()
        self.files.pop()

    def prune(self, keep):
        while len(self.files) > keep:
            self.files[0].unlink(missing_ok=True)
            self.files.pop(0)

    def space(self, additional=0):
        usage = self.usage(self.directory)
        available = usage.used + usage.free
        return available > 0 and (usage.used + additional) * 10 < available * 9

    def append(self, record, timestamp_ns):
        seconds, ns = divmod(timestamp_ns, 1_000_000_000)
        data = (
            struct.pack("<IIII", seconds, ns // 1000, len(record), len(record)) + record
        )
        if len(data) + len(HEADER) > self.max_bytes:
            return False
        rotate = self.file is None or self.file.tell() + len(data) > self.max_bytes
        if not self.space(len(data) + (len(HEADER) if rotate else 0)):
            self.close()
            return False
        last_complete = 0 if rotate else self.file.tell()
        try:
            if rotate:
                self.close()
                self.prune(self.count - 1)
                self.index += 1
                path = self.directory / f"activity-{self.index:020d}.pcap"
                self.file = path.open("xb", buffering=0)
                self.files.append(path)
                if self.file.write(HEADER) != len(HEADER):
                    raise OSError(errno.ENOSPC, "Incomplete pcap header")
                last_complete = len(HEADER)
            if self.file.write(data) != len(data):
                raise OSError(errno.ENOSPC, "Incomplete pcap record")
        except OSError as error:
            if self.file is not None:
                try:
                    self.file.truncate(last_complete)
                finally:
                    self.close()
                if not last_complete:
                    self.files.pop().unlink()
            if error.errno in (errno.ENOSPC, errno.EDQUOT):
                return False
            raise
        return True

    def close(self):
        if self.file:
            self.file.close()
            self.file = None
