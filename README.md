# Field Wireless Companion

FWC is a handheld wireless observation and connectivity logger for the owner's
networks and surroundings. The companion dashboard shows observed networks,
association events, service health and plugin status. Observation records stay
in the device's activity log.

## Install on a board

The handheld target is a Raspberry Pi Zero 2 W with a Waveshare 1.44-inch LCD
HAT, a PiSugar S and an Archer T2U Nano on the powered USB-Ethernet hub. The
Pi build and the original x86-64 build have separate disk images. The Pi
creates its device identity on first boot and uses the USB radio for observation.

1. Build the Pi image with `just pi-image`, or use the x86-64 release for the UP Board. [Release verification](docs/releases/v0.1.1.md)
2. Provision the writable partition and boot the selected image. [Bring-up guide](docs/bringup.md)
3. Open `http://127.0.0.1:8080/` on the device for the dashboard, or
   `/fallback` for plain status. [Web service](web/README.md)

The hardware session covers physical boot, the panel, controls, power, watchdog
and radio. Each check has a command and an expected result. [Hardware ledger](docs/bringup-checklist.md)

## Run a local core

On Linux, use Rust 1.89 or newer with Cargo and a C compiler for SQLite.
From a checkout, these commands start a headless core in a new temporary
directory with an empty service registry.

```sh
state=$(mktemp -d)
echo '{}' > "$state/core.json"
cargo run -- "$state" "$state/core.json"
```

The core writes structured logs, a SQLite activity log and a Unix socket in
the state directory. A service configuration starts the optional workers.
[Configuration reference](docs/configuration.md) · [Core bus](docs/CORE.md)

## Guides

| Task | Guide |
| --- | --- |
| Configure channels, filters and rotation | [Capture worker](capture/README.md) |
| Select drivers and check the board | [Driver registry](drivers/README.md) · [Panel and ADC](drivers/HARDWARE.md) · [Bring-up ledger](docs/bringup-checklist.md) |
| Write and install a plugin | [Plugin author guide](docs/plugin-author.md) |
| Stage and check an update | [A/B updates](image/UPDATE.md) |

## Development

Run `just ci` on x86-64 Linux for the full local gate. It exercises Rust,
the uv Python workspace, the API and Chromium UI, service and plugin recovery,
UEFI rollback, image assembly, guest boot and documentation. CI tests Python
3.11–3.13. The image and boot checks have additional Linux toolchain
requirements. [Image build prerequisites](docs/bringup.md) · [UEFI test prerequisites](image/UPDATE.md)

Use `cargo test` for the Rust tests and `uv run --locked pytest` for Python.
The browser tests need matching Chrome/Chromium and ChromeDriver. Plugin tests
need bubblewrap, user namespaces and libseccomp. Run `just docs` to build the
offline guide at `build/docs/index.html`.
