# Build and bring up a companion

The reference target is an UP Board with an Atom x5-Z8350, 64-bit UEFI,
and a USB observation radio. The removable x86-64 EFI boot path also runs in
QEMU with 512 MiB memory. The included ath9k_htc firmware supports USB radios
in that family. The Waveshare vendor transport targets Raspberry Pi SPI/GPIO.
An x86 installation needs a board-compatible transport and its wiring verified.

## Build

On x86-64 Linux install Python 3.11+, GNU tar, Cargo, uv, just and bubblewrap
with working user namespaces. Allow 8 GiB of build space, internet access for
the initial locked Alpine packages, wheels and Rust sources, and 2 GiB for
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

Identify an expendable USB drive by its model, serial and capacity. Unmount its
partitions. The following command overwrites the selected **whole drive**.
Set FWC_DRIVE to its stable by-id path, then check the resolved device before
writing.

```sh
lsblk -o NAME,PATH,MODEL,SERIAL,SIZE,MOUNTPOINTS
lsblk -o NAME,TYPE,SIZE,MOUNTPOINTS "$FWC_DRIVE"
test "$(lsblk -dn -o TYPE "$FWC_DRIVE")" = disk &&
  sudo dd if=build/image/fwc-x86_64.img of="$FWC_DRIVE" bs=4M conv=fsync status=progress
```

Boot through the board's removable-media UEFI menu with this drive attached.
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

Select the dedicated USB radio's phy in `capture.json`. Add the service object
from `capture-service.json` to the `services` array in `core.json` and reboot.
The default configuration lets the owner select the radio before observation
starts. Set SSID preferences and rotation limits using the [capture reference](../capture/README.md).
Install plugins by copying a reviewed example directory from
`/usr/share/fwc/examples` to `/var/lib/fwc/plugins` and restarting the host.
Select hardware drivers and their interpreter using the
[hardware reference](../drivers/HARDWARE.md). Keep an initial boot log, compare the
voltage against a meter, check the button and panel visually, and verify the
watchdog on a designated test board.

Record results in the [bring-up evidence ledger](bringup-checklist.md).
