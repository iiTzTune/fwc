# FWC — Field Wireless Companion

A personal wireless network observation and connectivity-logging device for the
owner's own networks and surroundings. Ground-up rebuild of the community handheld
network-observation device concept (pwnagotchi lineage), optimized for reliability,
efficiency, and plugin extensibility.

## Status
Pre-scaffold. The build plan is the source of truth:
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md)

## Repository layout (M0 scaffold, currently empty)
- `core/` — Rust supervisor daemon, state store (SQLite WAL), message bus
- `capture/` — Python capture worker (monitor-mode, standard nl80211 tooling)
- `drivers/` — hardware HAL driver packages (Display, Power, Indicator, Input)
- `web/` — JSON API + small front-end, e-ink-friendly static fallback
- `plugins/` — plugin SDK and example plugins
- `image/` — declarative OS image builder

## Engineering rules
See `docs/BUILD_PLAN.md` — "Engineering rules for every lane". Highlights:
- Tests-first for state machines and parsers
- No secrets in the repo; device identity material generated at first boot
- Config is schema-validated at boot; invalid config is readable, never a crash loop
- Each milestone lane owns its top-level directory exclusively
