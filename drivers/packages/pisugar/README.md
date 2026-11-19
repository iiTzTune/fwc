# PiSugar S power driver

On the Pi Zero 2 W, select `{"power":{"name":"pisugar-s","options":{}}}`
inside the core's `drivers` object. Turn the PiSugar S auto-start function
switch **ON** and set `dtparam=i2c_arm=off` in the Pi boot configuration.
The S holds BCM GPIO3 (physical pin 5) low when external power reaches its
charge input. It releases the line when the input supply is removed. With
this switch OFF the line serves the button instead. The driver requests
GPIO3 as an input and releases it after each sample.
[PiSugar S manual](https://docs.pisugar.com/docs/product-wiki/battery/pisugar-s-series)

The S has no I²C device address or battery register map. Its `coarse`
capability reports a power source. With external input present,
`charge_state` is `unknown` because the signal cannot distinguish charging
from charge completion. With external input absent, `discharging` describes
the expected state of a Pi powered by the pack. Keep the externally powered
USB hub connected during the desk check and confirm the hub does not supply
power upstream to the Pi. The pack supplies no battery voltage, capacity
percentage or runtime estimate.

## Check the board

Run on the Pi using the core's Python interpreter. Repeat once with power
at the PiSugar charge input and once with that input disconnected.

```sh
printf '%s\n' '{"role":"power","spec":{"name":"pisugar-s"},"probe":false,"args":{"action":"power_status"}}' | python3 -m fwc.drivers.bridge
```

Expected with external input:

```json
{"value":{"capability":"coarse","external_power":true,"charge_state":"unknown","millivolts":null}}
```

Expected from the pack alone:

```json
{"value":{"capability":"coarse","external_power":false,"charge_state":"discharging","millivolts":null}}
```

An inaccessible GPIO chip or a line already claimed by another device
returns a driver error. A missing reading does not indicate that the pack
is empty. PCB revision, switch mode and both physical edge readings await
on-desk verification.

Voltage-based software shutdown is skipped on a coarse pack. Protection
comes from the pack's hardware cutoff. PiSugar specifies cutoff below 3 V
and restart above 3.6 V. Cutoff under load on the assembled stack remains
an open hardware check. Record the actual voltage with an independent
meter using the manufacturer's test conditions.
[PiSugar S battery protection](https://docs.pisugar.com/docs/product-wiki/battery/pisugar-s-series#battery-protection)

## Drop-in full capability driver

A future INA219-backed package can supply the same power status contract.
It needs a board-specific bus, address, shunt calibration, measurement
point and current direction, plus an independent way to establish
external-power presence. Place the monitor on an independently wired I²C
bus if PiSugar S auto-start still owns GPIO3. The following is a package
stub for a future implementation.

```toml
[project.entry-points."fwc.drivers.power"]
ina219 = "fwc.ina219:Power"
```

```python
class Power:
    capability = "full"

    def __init__(self, options):
        self.options = options

    def read_mv(self):
        raise NotImplementedError("battery voltage needs board calibration")

    def read_status(self):
        # Return external_power, charge_state and measured millivolts.
        raise NotImplementedError(
            "power source and current sensing need board validation"
        )
```

No INA219 entry point is installed by the PiSugar S package. A validated
full driver would be selected through `drivers.power.name`.
