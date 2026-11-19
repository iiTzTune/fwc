# Live capture acceptance in a Linux guest

Use a disposable Linux VM with the mac80211_hwsim module, hostapd, iw,
iproute2, mount/umount, Python, and the Linux core binary. A guest network
adapter is unnecessary. Install the capture package or set PYTHONPATH to
the workspace's capture/src directory.

Start with a fresh guest, load `mac80211_hwsim` with `radios=3`, and confirm
`iw dev` shows wlan0, wlan1, and wlan2 on phy0, phy1, and phy2 respectively.
Start hostapd inside the guest using the following configuration.

```ini
interface=wlan0
driver=nl80211
ssid=fwc-virtual
hw_mode=g
channel=1
beacon_int=100
```

From the workspace root in the guest, run the following as root.

```sh
PYTHONPATH=capture/src python3 capture/tests/hwsim_guest.py target/debug/fwc-core
```

The script subscribes to three topics on the core's Unix socket. It times
network and signal delivery from worker launch, then times association delivery
from the third radio's connection request. Each deadline is one two-second
dwell. It checks the switch to channel 6, file count and size, pauses writes
by filling its private 1 MiB tmpfs above 90%, and releases space to confirm
writes resume. All capture files and core state live in the guest's temporary
test directory.
