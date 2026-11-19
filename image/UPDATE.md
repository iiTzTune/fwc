# Slot updates

The removable-media x86-64 EFI loader reads the FWC-BOOT FAT partition. The
two ext4 slots each contain a complete OS with its own /boot/vmlinuz and
/boot/initrd. The fourth partition holds device state across updates. The
production GPT names and UUIDs live in `boot/layout.json`.

## Install

Supply the release's complete ext4 slot image URL and its expected SHA-256.
Run as the device administrator. The command streams directly to the inactive
partition, flushes it, reads back the downloaded bytes, compares the checksum,
and arms a one-time boot. It holds an exclusive update lock and opens the
target block device exclusively. A failed or interrupted transfer leaves the
currently running slot selected.

```sh
fwc-update install "$slot_url" "$expected_sha256"
reboot
```

HTTPS downloads and absolute file URLs are supported. Reboot into confirmed
mode after a successful trial before staging a subsequent update. A new
installation retires the old rollback copy when it begins rewriting that slot.

## Boot and recovery

Before loading a trial kernel, GRUB clears the pending selection on the ESP.
If the trial cannot load its kernel or initrd, GRUB tries the confirmed slot
immediately. If the trial resets before passing health checks, the next boot
selects the confirmed slot. The loader requests a reboot five seconds after a
kernel panic. After four consecutive healthy core snapshots, the new slot becomes
confirmed and the old slot becomes the rollback copy. Two consecutive boots
reporting the Linux watchdog card-reset bit cause a return to that copy.
The current boot ID prevents duplicate reset counts when the hook is retried.

Mount the ESP read/write at /boot/efi. Make the GPT by-partlabel links available
at /dev/disk/by-partlabel. Install `fwc-update`, curl with HTTPS support and a
CA bundle, sha256sum, and `boot/health.sh` into both slots. The init system
must launch the health hook on each boot, including when the core service
fails to start. Pass the actual Unix bus path.

```sh
/usr/lib/fwc/health.sh /var/lib/fwc/bus.sock
```

The hook checks for a responsive core and a live PID for each configured
worker. A failed trial has 45 seconds to recover before the hook requests a
reboot. It also reboots a trial if reading or recording its state fails.
For a hang before userspace can request a reboot, the board must supply a
watchdog that remains armed during early boot. Verify its reset indication and
boot coverage on the target board.

## Image builder

Create a new initial selection with `fwc-update init-env FILE`. Use
`boot.assemble.make_esp(output, environment)` to populate the FAT boot image.
Use `boot.assemble.assemble(output, esp, slot_a, slot_b, data)` to write a new
disk image from the four filesystem images. Slots must have equal size. Both
helpers require unused output paths. Fixed GUIDs identify the boot medium;
boot with one FWC image attached. `FWC_GRUB_MODULES` overrides the GRUB
x86_64-efi module directory.

## Test

Run `cargo test update::` and `just image-ab-test`. The guest test stages a
complete candidate with the same installer against regular image files using
`fwc-update stage-files DIRECTORY URL SHA256`. The directory holds
`grubenv`, `A.img` and `B.img`. OVMF then loads the production EFI selector
from a disposable GPT disk. The trial kernel's init process resets the guest
before confirmation and the test checks that the confirmed slot runs next.

The test needs a readable x86-64 Linux kernel with an EFI entry and support
for a gzip initramfs, matched OVMF firmware and variable templates, QEMU,
GRUB EFI modules and grub-mkimage, gdisk, mtools, dosfstools, e2fsprogs,
cpio, Python 3.11 or newer and a C compiler with static libc. It uses KVM
when available and QEMU software emulation otherwise. Set `FWC_TEST_KERNEL`,
`FWC_OVMF_CODE` and `FWC_OVMF_VARS` to override local discovery.
