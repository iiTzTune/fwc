"""Boot the production GPT/UEFI selector twice on one disposable QEMU disk."""

import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[1]
sys.path.insert(0, str(TESTS.parent / "boot"))
from assemble import assemble, make_esp


def locate(setting, candidates):
    if setting in os.environ:
        candidates = [Path(os.environ[setting])]
    for path in candidates:
        if path.is_file() and os.access(path, os.R_OK):
            return path
    raise SystemExit(f"Set {setting} to a readable file")


def run(temporary):
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    firmware = [
        data_home / "edk2/x64",
        Path("/usr/share/edk2/x64"),
        Path("/usr/share/OVMF"),
    ]

    def ovmf(kind):
        names = [f"OVMF_{kind}.4m.fd", f"OVMF_{kind}_4M.fd", f"OVMF_{kind}.fd"]
        return locate(f"FWC_OVMF_{kind}", [d / n for d in firmware for n in names])

    kernel = locate(
        "FWC_TEST_KERNEL",
        [
            Path("/usr/lib/modules", os.uname().release, "vmlinuz"),
            *sorted(Path("/usr/lib/modules").glob("*/vmlinuz"), reverse=True),
            Path("/boot", "vmlinuz-" + os.uname().release),
            *(
                sorted(Path("/boot").glob("vmlinuz-*"), reverse=True)
                if os.access("/boot", os.R_OK)
                else []
            ),
        ],
    )
    binary = (
        Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target")) / "debug/fwc-update"
    )
    initramfs = temporary / "ram"
    initramfs.mkdir()
    (initramfs / "proc").mkdir()
    subprocess.run(
        ["cc", "-static", "-Os", TESTS / "ab_init.c", "-o", initramfs / "init"],
        check=True,
    )
    root = temporary / "root"
    boot = root / "boot"
    boot.mkdir(parents=True)
    shutil.copyfile(kernel, boot / "vmlinuz")
    archive = subprocess.run(
        ["cpio", "--quiet", "-o", "-H", "newc", "--owner=0:0"],
        cwd=initramfs,
        input=b".\nproc\ninit\n",
        stdout=subprocess.PIPE,
        check=True,
    )
    (boot / "initrd").write_bytes(gzip.compress(archive.stdout, mtime=0))

    def ext4(name, size, source=None):
        path = temporary / name
        with path.open("xb") as stream:
            stream.truncate(size * 1024**2)
        subprocess.run(
            ["mkfs.ext4", "-q", "-F", *(["-d", source] if source else []), path],
            check=True,
        )
        return path

    slot_a = ext4("A.img", 64, root)
    slot_b = ext4("B.img", 64)
    data = ext4("data.img", 8)
    env = temporary / "grubenv"
    subprocess.run([binary, "init-env", env], check=True)
    with slot_a.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    subprocess.run(
        [binary, "stage-files", temporary, slot_a.as_uri(), checksum], check=True
    )
    esp = temporary / "esp.img"
    make_esp(esp, env)
    disk = temporary / "board.img"
    assemble(disk, esp, slot_a, slot_b, data)
    variables = temporary / "vars.fd"
    shutil.copyfile(ovmf("VARS"), variables)
    accelerator = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
    qemu = [
        "qemu-system-x86_64",
        "-machine",
        f"q35,accel={accelerator}",
        "-m",
        "512",
        "-drive",
        f"if=pflash,format=raw,readonly=on,file={ovmf('CODE')}",
        "-drive",
        f"if=pflash,format=raw,file={variables}",
        "-drive",
        f"file={disk},format=raw,if=virtio",
        "-nic",
        "none",
        "-display",
        "none",
        "-serial",
        "stdio",
        "-monitor",
        "none",
        "-device",
        "isa-debug-exit,iobase=0xf4,iosize=1",
    ]
    log = temporary / "serial.log"
    with log.open("wb") as stream:
        try:
            result = subprocess.run(
                qemu, timeout=180, stdout=stream, stderr=subprocess.STDOUT, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(log.read_text(errors="replace")[-6000:]) from error
    output = log.read_text(errors="replace")
    trial = "FWC_TEST_FAILED_TRIAL_B"
    confirmed = "FWC_TEST_CONFIRMED_A"
    if (
        result.returncode != 33
        or output.count(trial) != 1
        or output.count(confirmed) != 1
        or output.index(trial) > output.index(confirmed)
    ):
        raise RuntimeError(output[-6000:])
    print("QEMU UEFI: failed trial B -> reboot -> confirmed A (one reboot)")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fwc-ab-") as directory:
        run(Path(directory))
