#!/bin/sh
# Start alongside the core after mounting the ESP read/write at /boot/efi.
fwc-update boot-health "${1:-/var/lib/fwc/bus.sock}"
result=$?
case "$result" in
    0) exit 0 ;;
    75) sync; exec reboot -f ;;
    *) case " $(cat /proc/cmdline) " in
           *" fwc.trial=1 "*) sync; exec reboot -f ;;
       esac
       exit "$result" ;;
esac
