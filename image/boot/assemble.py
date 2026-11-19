"""Build the FWC UEFI boot volume and assemble regular partition image files."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

BOOT = Path(__file__).resolve().parent


def make_esp(output, environment, *, architecture="x86_64", boot_files=None):
    """Create the removable-media ESP from an initialized GRUB environment."""
    with tempfile.TemporaryDirectory() as temporary:
        arm = architecture == "aarch64"
        efi = Path(temporary) / ("BOOTAA64.EFI" if arm else "BOOTX64.EFI")
        directory = os.environ.get("FWC_GRUB_MODULES")
        subprocess.run(
            [
                "grub-mkimage",
                *(["-d", directory] if directory else []),
                "-O",
                "arm64-efi" if arm else "x86_64-efi",
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
            (efi, f"BOOT/{efi.name}"),
            (BOOT / "grub.cfg", "FWC/grub.cfg"),
            (environment, "FWC/grubenv"),
            (environment, "FWC/grubenv.safe"),
        ]:
            subprocess.run(["mcopy", "-i", output, file, f"::/EFI/{dest}"], check=True)
        for name, source in (boot_files or {}).items():
            subprocess.run(["mcopy", "-i", output, source, f"::/{name}"], check=True)


def assemble(output, esp, slot_a, slot_b, data, *, pi=False):
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
    if pi:
        # Mirror only the boot volume into the legacy table read by Pi firmware.
        subprocess.run(["sgdisk", "--hybrid=1", output], check=True)
    with output.open("r+b") as disk:
        if pi:
            disk.seek(446)
            entries = [bytearray(disk.read(16)) for _ in range(4)]
            for index, entry in enumerate(entries):
                if int.from_bytes(entry[8:12], "little") == starts[0] // sector:
                    entry[4] = 0x0C  # FAT32 LBA
                    entries.insert(0, entries.pop(index))
                    break
            else:
                raise ValueError("Missing boot partition in the Pi legacy table")
            disk.seek(446)
            disk.write(b"".join(entries))
        for offset, source in zip(starts, sources, strict=True):
            disk.seek(offset)
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, disk, length=1024**2)
        disk.flush()
        os.fsync(disk.fileno())
