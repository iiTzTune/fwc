# Build and bring up a companion

The handheld target is a Raspberry Pi Zero 2 W with 512 MiB RAM, a Waveshare
1.44-inch ST7735S LCD HAT, PiSugar S, and an externally powered Waveshare
USB-Ethernet hub carrying the Archer T2U Nano. The onboard Wi-Fi provides
normal connectivity. The x86-64 UP Board image remains available.

## Build and check the Pi image

Use an ARM64 Linux build host with Cargo, uv, just, GNU tar and bubblewrap.
An x86-64 host also needs a working AArch64 binfmt interpreter usable inside
a mount namespace. On Ubuntu 26.04 the `qemu-user` and `qemu-user-binfmt`
packages supply user-mode emulation. Check for the `F` flag in
`/proc/sys/fs/binfmt_misc/qemu-aarch64`. Install `qemu-system-arm` and
`qemu-efi-aarch64` for the guest boots.

```sh
just pi-image
(cd build/image/pi-zero-2w && sha256sum -c SHA256SUMS)
```

The guest boots a writable copy of the ARM64 disk twice with 512 MiB and
checks the first-boot identity through both boots. Each boot loads the
Waveshare display and input, PiSugar S power, dummy display and IIO power
entry points using the guest's installed Python. Pi firmware, the GPIO
header, USB hub and battery require the board checks in the ledger.

## Build and check the x86-64 image

On x86-64 Linux install Python 3.11+, GNU tar, Cargo, uv, just and bubblewrap
with working user namespaces. Allow 8 GiB of build space, internet access for
the initial locked Alpine packages, wheels and Rust sources, and 3 GiB for
outputs. The builder runs without host mounts or elevated privileges. Run from
a clean checkout.

```sh
just image build
(cd build/image && sha256sum -c SHA256SUMS)
python3 image/tests/boot_image.py build/image/fwc-x86_64.img
```

The boot check requires qemu-system-x86_64 and matching OVMF CODE/VARS
firmware. The same firmware overrides as the [slot test](../image/UPDATE.md)
apply. The check uses a private writable copy, boots it twice, and confirms the
live API reports its workers with the same first-boot identity.

The board description defines the slot size, data partition, epoch and file
mappings. The build downloads content verified against `image/alpine.lock`.
Keep a copy of `~/.cache/fwc-image/inputs` for offline access to exact Alpine
artifacts after repository rotation. Cargo uses workspace lockfile checksums and
builds vendored sources with a pinned Alpine compiler at a fixed source path.
Python wheel contents, kernel, root filesystems and EFI tools are assembled
in the locked build environment. The slot artifact is a complete ext4 filesystem
for the M6 installer. The disk image contains the EFI volume, two copies of the
slot, and a separate writable data volume.

## Flash

For the Pi, use the ARM64 image from the build above and a spare microSD
card. For the UP Board, use the x86-64 image and a removable drive. Select
the file for the intended board. For a signed release, verify the signature
and decompress its disk image first. [Release verification](releases/v0.1.0.md)

Identify the selected card or drive by its model, serial and capacity. Unmount its
partitions. The following command overwrites the selected **whole drive**.
Set FWC_DRIVE to its stable by-id path, then check the resolved device before
writing.

```sh
FWC_DISK=build/image/pi-zero-2w/fwc-pi-zero-2w.img
lsblk -o NAME,PATH,MODEL,SERIAL,SIZE,MOUNTPOINTS
lsblk -o NAME,TYPE,SIZE,MOUNTPOINTS "$FWC_DRIVE"
test "$(lsblk -dn -o TYPE "$FWC_DRIVE")" = disk &&
  sudo dd if="$FWC_DISK" of="$FWC_DRIVE" bs=4M conv=fsync status=progress
```

Seat the Pi HAT and battery with power disconnected. Set the PiSugar S
auto-start switch ON for GPIO3 external-power sensing. Connect the hub to
the USB data port and supply it externally. Insert the microSD card. On
the x86 board, boot the drive through the removable-media UEFI menu.
Use 115200 baud on the serial console. The boot selection and Linux slot
identifier appear there. The core supervises the web service and plugin host.
The companion UI listens on the device at http://127.0.0.1:8080.
A startup message reports the persistent device ID and service names.

## Set up the board

The writable partition has the GPT name FWC-DATA. On a stopped device, mount
partition 4 on a Linux workstation. Its root holds `core.json`, `capture.json`,
`plugins/` and the actual observation state. Device identity is generated on
first boot. The packaged root account is locked. To enable local console login,
provide a root-only password database on the data partition before boot.

```sh
sudo mount -o rw /dev/disk/by-partlabel/FWC-DATA /mnt
sudo sh -c 'umask 077; HASH=$(openssl passwd -6) && printf "root:%s:20000:0:99999:7:::\n" "$HASH" > /mnt/shadow'
sudo umount /mnt
```

The Pi image selects a USB monitor-capable interface automatically. For a
specific adapter, set `observation_interface` in `capture.json` to the
source interface. Confirm the T2U Nano USB ID and driver during bring-up.
The x86 image supplies a separate `capture-service.json` object to add to
the `services` array in `core.json` after choosing its observation radio.
Set network preferences and rotation limits using the [capture reference](../capture/README.md).
Install plugins by copying a reviewed example directory from
`/usr/share/fwc/examples` to `/var/lib/fwc/plugins` and restarting the host.
Select hardware drivers and their interpreter using the
[hardware reference](../drivers/HARDWARE.md). Keep an initial serial boot log. The PiSugar S reports external power.
Use a separate meter for the cutoff test. Check the LCD, all seven controls
and the physical watchdog on the designated board.

Record results in the [bring-up evidence ledger](bringup-checklist.md).
