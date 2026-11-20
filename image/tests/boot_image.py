"""Boot a disposable copy twice and check the API and on-disk device identity."""

import argparse
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pi_access
from qemu_ab import locate


def check_pi_partitions(disk):
    layout = json.loads(
        (Path(__file__).resolve().parents[1] / "boot/layout.json").read_text()
    )
    with disk.open("rb") as stream:
        mbr = stream.read(512)
        assert mbr[-2:] == bytes.fromhex("55aa") and mbr[450] == 0x0C
        mirror = struct.unpack_from("<II", mbr, 454)
        header = stream.read(512)
        assert header[:8] == b"EFI PART"
        assert str(uuid.UUID(bytes_le=header[56:72])) == layout["disk_guid"]
        table, _, size = struct.unpack_from("<QII", header, 72)
        stream.seek(table * layout["sector_bytes"])
        for index, spec in enumerate(layout["partitions"]):
            entry = stream.read(size)
            assert str(uuid.UUID(bytes_le=entry[16:32])) == spec["guid"]
            assert entry[56:128].decode("utf-16-le").rstrip("\0") == spec["label"]
            if index == 3:
                data_start, data_end = struct.unpack_from("<QQ", entry, 32)
            if index == 0:
                start, end = struct.unpack_from("<QQ", entry, 32)
                assert mirror == (start, end - start + 1)
    print(
        "Pi FAT32 boot mirror matches GPT. Both root slots and DATA retain their partition IDs.",
        flush=True,
    )
    return data_start, data_end - data_start + 1


def firmware(kind, pi=False):
    if pi:
        names = (
            ("QEMU_EFI.fd", "AAVMF_CODE.fd")
            if kind == "CODE"
            else ("QEMU_VARS.fd", "AAVMF_VARS.fd")
        )
        return locate(
            f"FWC_AAVMF_{kind}",
            [
                root / name
                for root in (Path("/usr/share/AAVMF"), Path("/usr/share/edk2/aarch64"))
                for name in names
            ],
        )
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return locate(
        f"FWC_OVMF_{kind}",
        [
            root / name
            for root in [
                data / "edk2/x64",
                Path("/usr/share/edk2/x64"),
                Path("/usr/share/OVMF"),
            ]
            for name in [f"OVMF_{kind}.4m.fd", f"OVMF_{kind}_4M.fd", f"OVMF_{kind}.fd"]
        ],
    )


def boot(disk, variables, log, pi=False):
    architecture = "aarch64" if pi else "x86_64"
    native = platform.machine() == architecture
    accel = "kvm" if native and os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
    forwards = pi_access.ports() if pi else None
    command = [
        f"qemu-system-{architecture}",
        "-machine",
        f"{'virt' if pi else 'q35'},accel={accel}",
        *(
            [
                "-cpu",
                "host" if accel == "kvm" else "cortex-a53",
                "-smbios",
                "type=1,product=FWC ARM image check",
            ]
            if pi
            else []
        ),
        "-m",
        "512",
        "-smp",
        "2",
        "-drive",
        f"if=pflash,format=raw,readonly=on,file={firmware('CODE', pi)}",
        "-drive",
        f"if=pflash,format=raw,file={variables}",
        "-drive",
        f"if=virtio,format=raw,file={disk}",
        "-nic",
        (
            f"user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{forwards[0]}-:8080,"
            f"hostfwd=tcp:127.0.0.1:{forwards[1]}-:22"
        )
        if pi
        else "none",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "stdio",
        "-no-reboot",
    ]
    with (
        log.open("wb") as stream,
        subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT) as guest,
    ):
        try:
            deadline = time.monotonic() + (360 if pi else 180)
            while time.monotonic() < deadline and guest.poll() is None:
                text = log.read_text(errors="replace")
                failure = re.search(r"FWC driver check failed: [^\r\n]+", text)
                if pi and failure:
                    raise RuntimeError(failure.group(0))
                match = re.search(
                    r"FWC ready id=([a-f0-9-]{36}) services=([a-z,]+)", text
                )
                if match:
                    required = (
                        {"web", "plugins", "capture"} if pi else {"web", "plugins"}
                    )
                    assert required <= set(match[2].split(","))
                    assert "Starting FWC slot A (trial=0)" in text
                    if pi:
                        driver_check = (
                            "FWC driver check passed (4 distributions, 5 entry points)"
                        )
                        assert driver_check in text, (
                            "Guest did not confirm its driver imports"
                        )
                        print(driver_check, flush=True)
                        pi_access.check(log.parent, forwards, match[1])
                    print(match.group(0), flush=True)
                    return match[1]
                time.sleep(0.25)
            raise RuntimeError(log.read_text(errors="replace")[-10000:])
        finally:
            guest.terminate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--target", choices=("x86_64", "pi-zero-2w"), default="x86_64")
    args = parser.parse_args()
    pi = args.target == "pi-zero-2w"
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fwc-image"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        work = Path(tmp)
        disk = work / "board.img"
        subprocess.run(
            ["cp", "--reflink=auto", "--sparse=always", args.image, disk], check=True
        )
        if pi:
            pi_access.provision(disk, work, *check_pi_partitions(disk))
        variables = work / "vars.fd"
        shutil.copy(firmware("VARS", pi), variables)
        first = boot(disk, variables, work / "boot1.log", pi)
        assert boot(disk, variables, work / "boot2.log", pi) == first
    print(
        "Two UEFI boots reached the live companion API with the same persistent device ID."
    )


if __name__ == "__main__":
    main()
