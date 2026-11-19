"""Build filesystems inside the pinned Alpine tool environment."""

import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from boot.assemble import assemble, make_esp

WORK = Path("/work")
ROOT = WORK / "root"
EPOCH = int(os.environ["SOURCE_DATE_EPOCH"])


def cpio(tree, output):
    """Write a sorted newc archive with fixed inode numbers and ownership."""
    with (
        output.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream,
    ):
        for inode, path in enumerate([*sorted(tree.rglob("*")), None], start=1):
            if path is None:
                name, mode, data = "TRAILER!!!", 0, b""
            else:
                name = str(path.relative_to(tree))
                mode = path.lstat().st_mode
                data = (
                    os.fsencode(os.readlink(path))
                    if stat.S_ISLNK(mode)
                    else path.read_bytes()
                    if stat.S_ISREG(mode)
                    else b""
                )
            encoded = os.fsencode(name) + b"\0"
            fields = (
                inode,
                mode,
                0,
                0,
                1,
                EPOCH,
                len(data),
                0,
                0,
                0,
                0,
                len(encoded),
                0,
            )
            header = (
                b"070701"
                + b"".join(f"{field:08x}".encode() for field in fields)
                + encoded
            )
            stream.write(header + b"\0" * (-len(header) % 4))
            stream.write(data + b"\0" * (-len(data) % 4))


def initramfs():
    ram = WORK / "ram"
    for dirname in ["bin", "sbin", "proc", "sys", "dev", "newroot"]:
        (ram / dirname).mkdir(parents=True)
    shutil.copy2("/bin/busybox.static", ram / "bin/busybox")
    shutil.copy2("/src/image/runtime/initramfs", ram / "init")
    for name in ["kmod", "blkid"]:
        program = Path(shutil.which(name)).resolve()
        shutil.copy2(program, ram / "sbin" / name)
        result = subprocess.check_output(["ldd", program], text=True)
        for lib in re.findall(r"(/[^\s(]+)", result):
            dest = ram / lib.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lib, dest)
    (ram / "sbin/modprobe").symlink_to("kmod")
    modules = next((ROOT / "lib/modules").iterdir())
    dest = ram / modules.relative_to(ROOT)
    dest.mkdir(parents=True)
    for path in modules.glob("modules.*"):
        shutil.copy2(path, dest / path.name)
    depends = {}
    for row in (modules / "modules.dep").read_text().splitlines():
        key, values = row.split(":", 1)
        depends[key] = values.split()
    initial = {
        "virtio_pci",
        "virtio_mmio",
        "virtio_blk",
        "ahci",
        "sd_mod",
        "mmc_block",
        "sdhci_pci",
        "sdhci_acpi",
        "bcm2835",
        "bcm2835_dma",
        "sdhci_iproc",
        "xhci_pci",
        "usb_storage",
        "uas",
        "ext4",
        "vfat",
        "nls_cp437",
        "nls_iso8859_1",
    }
    queue = [
        key
        for key in depends
        if Path(key).name.split(".")[0].replace("-", "_") in initial
    ]
    while queue:
        key = queue.pop()
        if (dest / key).exists():
            continue
        (dest / key).parent.mkdir(exist_ok=True, parents=True)
        shutil.copy2(modules / key, dest / key)
        queue.extend(depends[key])
    cpio(ram, ROOT / "boot/initrd")


def ext4(name, tree, size, fsid):
    dest = WORK / name
    with dest.open("xb") as stream:
        stream.truncate(size * 1024**2)
    subprocess.run(
        [
            "mke2fs",
            "-q",
            "-t",
            "ext4",
            "-F",
            "-b",
            "4096",
            "-U",
            fsid,
            "-E",
            f"hash_seed={fsid},lazy_itable_init=0,lazy_journal_init=0",
            "-d",
            tree,
            dest,
        ],
        check=True,
    )
    # ext4 records host inode change times even when source mtimes are fixed.
    # Address image inodes by number so links and unusual pathnames also work.
    directories = [
        tree,
        *(p for p in tree.rglob("*") if not p.is_symlink() and p.is_dir()),
    ]
    listings = "".join(f'ls -p -l "/{p.relative_to(tree)}"\n' for p in directories)
    result = subprocess.check_output(
        ["debugfs", "-f", "-", dest], input=listings, text=True
    )
    inodes = sorted(
        {2, 11}
        | {int(i) for i in re.findall(r"^/(\d+)/", result, re.MULTILINE) if int(i)}
    )
    commands = "".join(
        f"set_inode_field <{inode}> {field} {value}\n"
        for inode in inodes
        for field, value in [
            ("atime", f"@{EPOCH}"),
            ("mtime", f"@{EPOCH}"),
            ("ctime", f"@{EPOCH}"),
            ("crtime", f"@{EPOCH}"),
            ("atime_extra", 0),
            ("mtime_extra", 0),
            ("ctime_extra", 0),
            ("crtime_extra", 0),
        ]
    )
    subprocess.run(
        ["debugfs", "-w", "-f", "-", dest],
        input=commands,
        text=True,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    return dest


def disk():
    cfg = json.loads((WORK / "device.json").read_text())
    pi = cfg["architecture"] == "aarch64"
    slot = ext4(
        "fwc-slot.ext4", ROOT, cfg["slot_mib"], "188fe787-3e03-4d50-a0ab-000000000001"
    )
    data = ext4(
        "data.ext4",
        WORK / "data",
        cfg["data_mib"],
        "188fe787-3e03-4d50-a0ab-000000000002",
    )
    environment = WORK / "grubenv"
    subprocess.run([ROOT / "usr/bin/fwc-update", "init-env", environment], check=True)
    esp = WORK / "esp.fat"
    files = {}
    if pi:
        board = WORK / "board"
        dtb = board / "fwc-zero-2-w.dtb"
        overlay = board / "hat.dtbo"
        subprocess.run(
            [
                "dtc",
                "-@",
                "-I",
                "dts",
                "-O",
                "dtb",
                "-o",
                overlay,
                "/src/image/pi-zero-2w/hat.dts",
            ],
            check=True,
        )
        subprocess.run(
            [
                "fdtoverlay",
                "-i",
                ROOT / "boot/dtbs-lts/broadcom/bcm2837-rpi-zero-2-w.dtb",
                "-o",
                dtb,
                overlay,
            ],
            check=True,
        )
        files = {
            "config.txt": Path("/src/image/pi-zero-2w/config.txt"),
            dtb.name: dtb,
            "u-boot.bin": board / "usr/share/u-boot/rpi_arm64/u-boot.bin",
            **{
                name: board / "boot" / name
                for name in ("bootcode.bin", "start.elf", "fixup.dat")
            },
        }
    make_esp(esp, environment, architecture=cfg["architecture"], boot_files=files)
    output = WORK / ("fwc-pi-zero-2w.img" if pi else "fwc-x86_64.img")
    assemble(output, esp, slot, slot, data, pi=pi)
    with (WORK / "SHA256SUMS").open("w") as listing:
        for path in [output, slot]:
            with path.open("rb") as source:
                print(
                    hashlib.file_digest(source, "sha256").hexdigest(),
                    " " + path.name,
                    file=listing,
                )


if __name__ == "__main__":
    {"initrd": initramfs, "disk": disk}[sys.argv[1]]()
