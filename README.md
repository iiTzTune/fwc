# Field Wireless Companion

FWC is a handheld wireless observation and connectivity logger for the owner's
networks and surroundings. The companion dashboard shows observed networks,
association events, service health and plugin status. Observation records stay
in the device's activity log.

## Install on a board

The v0.1.0 disk image targets the UP Board x5-Z8350 with 64-bit UEFI and a
dedicated USB observation radio. On first boot, the device creates its own
identity and starts the local dashboard and plugin host. Select the dedicated
radio before enabling capture. The physical board, panel, ADC, buttons and
watchdog have outstanding bring-up checks.

1. Download the flash image and verify the release signature. [Release and verification](docs/releases/v0.1.0.md)
2. Provision the writable partition and boot the device. [Bring-up guide](docs/bringup.md)
3. Open `http://127.0.0.1:8080/` on the device for the dashboard, or
   `/fallback` for plain status. [Web service](web/README.md)

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
