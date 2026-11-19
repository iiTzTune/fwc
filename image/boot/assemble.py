"""Build the FWC UEFI boot volume and assemble regular partition image files."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

BOOT = Path(__file__).resolve().parent


def make_esp(output, environment):
    """Create the removable-media ESP from an initialized GRUB environment."""
    with tempfile.TemporaryDirectory() as temporary:
        efi = Path(temporary) / "BOOTX64.EFI"
        directory = os.environ.get("FWC_GRUB_MODULES")
        subprocess.run(
            [
                "grub-mkimage",
                *(["-d", directory] if directory else []),
                "-O",
                "x86_64-efi",
                "-p",
                "/EFI/FWC",
                "-c",
                BOOT / "early.cfg",
                "-o",
                efi,
                "part_gpt",
                "fat",
                "ext2",
                "linux",
                "normal",
                "configfile",
                "search",
                "search_label",
                "loadenv",
                "serial",
                "terminal",
                "regexp",
                "test",
                "echo",
                "sleep",
                "reboot",
                "boot",
            ],
            check=True,
        )
        with output.open("xb") as stream:
            stream.truncate(64 * 1024**2)
        subprocess.run(
            ["mkfs.fat", "--invariant", "-F32", "-n", "FWC-BOOT", output], check=True
        )
        subprocess.run(
            ["mmd", "-i", output, "::/EFI", "::/EFI/BOOT", "::/EFI/FWC"], check=True
        )
        for file, dest in [
            (efi, "BOOT/BOOTX64.EFI"),
            (BOOT / "grub.cfg", "FWC/grub.cfg"),
            (environment, "FWC/grubenv"),
            (environment, "FWC/grubenv.safe"),
        ]:
            subprocess.run(["mcopy", "-i", output, file, f"::/EFI/{dest}"], check=True)


def assemble(output, esp, slot_a, slot_b, data):
    """Write GPT and copy an ESP, two equal-size slots and a data filesystem."""
    layout = json.loads((BOOT / "layout.json").read_text())
    sources = [esp, slot_a, slot_b, data]
    if slot_a.stat().st_size != slot_b.stat().st_size:
        raise ValueError("Slot partitions must be equal in size")
    sector = layout["sector_bytes"]
    align = layout["alignment_sectors"]
    start = align
    starts = []
    args = ["sgdisk", "--clear", f"--disk-guid={layout['disk_guid']}"]
    for number, (spec, source) in enumerate(
        zip(layout["partitions"], sources, strict=True), 1
    ):
        size = source.stat().st_size
        if size <= 0 or size % sector:
            raise ValueError(f"{source} must hold a whole number of sectors")
        end = start + size // sector - 1
        starts.append(start * sector)
        args.extend(
            [
                f"--new={number}:{start}:{end}",
                f"--typecode={number}:{spec['type']}",
                f"--change-name={number}:{spec['label']}",
                f"--partition-guid={number}:{spec['guid']}",
            ]
        )
        start = (end + align) // align * align
    with output.open("xb") as disk:
        disk.truncate((start + align) * sector)
    subprocess.run([*args, output], check=True)
    with output.open("r+b") as disk:
        for offset, source in zip(starts, sources, strict=True):
            disk.seek(offset)
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, disk, length=1024**2)
        disk.flush()
        os.fsync(disk.fileno())
