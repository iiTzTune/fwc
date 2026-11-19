# Field Wireless Companion

FWC is a personal wireless network observation and connectivity-logging device
for the owner's own networks and surroundings.

## Development

Use Linux with stable Rust (1.89 or newer, cargo, rustfmt and clippy), a C
toolchain for bundled SQLite, uv 0.13 and just. uv manages Python. Run
`just ci` at the repository root to check Rust, exercise recovery, lint and
test Python, build all Python distributions, build the static front-end into
`build/web`, and resolve the image file list. CI checks Python 3.11–3.13.

To run the core locally, save `{}` as `config.json` and run
`cargo run -- ./device-state ./config.json`. Use a dedicated state directory.
The default configuration starts the local bus with an empty service registry.

The Rust daemon lives in `core/`. The uv workspace includes `capture/`,
`drivers/` and `plugins/`. The static companion page lives in `web/` and
the device file list in `image/`.

See the [core interfaces](docs/CORE.md), [build plan](docs/BUILD_PLAN.md)
and [project glossary](docs/GLOSSARY.md).

For the observation worker, see the [capture configuration and event schema](capture/README.md)
and [live virtual-radio check](capture/HWSIM.md). For hardware selection, see
the [driver registry and operations](drivers/README.md) and
[display and ADC setup](drivers/HARDWARE.md).
