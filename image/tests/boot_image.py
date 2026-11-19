"""Boot a disposable copy twice and check the API and on-disk device identity."""

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from qemu_ab import locate


def firmware(kind):
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


def boot(disk, variables, log):
    accel = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
    command = [
        "qemu-system-x86_64",
        "-machine",
        f"q35,accel={accel}",
        "-m",
        "512",
        "-smp",
        "2",
        "-drive",
        f"if=pflash,format=raw,readonly=on,file={firmware('CODE')}",
        "-drive",
        f"if=pflash,format=raw,file={variables}",
        "-drive",
        f"if=virtio,format=raw,file={disk}",
        "-nic",
        "none",
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
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and guest.poll() is None:
                text = log.read_text(errors="replace")
                match = re.search(
                    r"FWC ready id=([a-f0-9-]{36}) services=([a-z,]+)", text
                )
                if match:
                    assert {"web", "plugins"} <= set(match[2].split(","))
                    print(match.group(0), flush=True)
                    return match[1]
                time.sleep(0.25)
            raise RuntimeError(log.read_text(errors="replace")[-10000:])
        finally:
            guest.terminate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fwc-image"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        work = Path(tmp)
        disk = work / "board.img"
        subprocess.run(
            ["cp", "--reflink=auto", "--sparse=always", args.image, disk], check=True
        )
        variables = work / "vars.fd"
        shutil.copy(firmware("VARS"), variables)
        first = boot(disk, variables, work / "boot1.log")
        assert boot(disk, variables, work / "boot2.log") == first
    print(
        "Two UEFI boots reached the live companion API with the same persistent device ID."
    )


if __name__ == "__main__":
    main()
