# Build Plan: Field Wireless Companion ("FWC")

A ground-up rebuild of the community handheld network-observation device concept
(pwnagotchi lineage) as a reliable, efficient, feature-rich platform. This document
is a self-contained handoff for a coding agent. All framing is neutral: this is a
personal wireless network observation and connectivity-logging device for the owner's
own networks and surroundings. It passively records network activity (beacons,
association events) and presents it on a small screen. No intrusive, disruptive, or
adversarial networking features are required.

## Project vocabulary (use these terms, not security-flavored ones)
- "observation session", "activity log", "network record", "association event"
- "monitor-mode capture worker", "channel scanner"
- "companion display", "device face"
- Avoid in code/comments/docs: pwn, handshake harvest, attack, crack, deauth,
  injection, exploit, pentest, Kali.

## Product goals (in priority order)
1. Reliability: runs unattended for weeks; every failure is recoverable without a shell.
2. Efficiency: fits a 512 MB single-board computer; low-power idle; battery-aware.
3. Feature-rich via plugins: the core is small; features are isolated, versioned add-ons.

## Non-goals for v1
- No active packet transmission beyond normal station behavior.
- No cloud services, no telemetry egress without explicit user opt-in.
- No machine-learning policy engine in v1 (optional later as a plugin).

---

## Architecture overview

- **Core daemon (Rust)**: single supervisor process owning a state machine,
  scheduling, service restart, and a local message bus (Unix domain sockets +
  JSON lines; no external broker dependency).
- **State store (SQLite, WAL mode)**: all durable state — network records, peer
  list, events, metrics, config history. Single writer (the core); readers are
  read-only connections.
- **Capture worker (Python, subprocess of core)**: manages monitor-mode interface
  via standard nl80211 tooling, rotates pcap files on the SD card, publishes
  summarized events to the bus. Runs with its own supervision and restart budget.
- **Hardware HAL (Rust traits + Python driver packages)**: `Display`, `Power`,
  `Indicator` (LED), `Input` (buttons). Drivers are separate packages registered
  via entry-points. The core never imports a driver by name; it discovers them.
- **Web UI (served by core)**: JSON API first; a small compiled front-end
  (Preact or Lit, ~100 KB gz) consuming it; an e-ink-friendly static fallback page.
- **Plugin host (Python)**: plugins run as short-lived subprocesses or WASM
  modules; each has a declared API version and a resource budget. A crashing
  plugin is restarted, never able to take down the core.
- **Image builder (declarative)**: one YAML/JSON device description generates a
  reproducible OS image in CI (choose NixOS or pi-gen-based; NixOS preferred for
  declarative reproducibility).

---

## Milestones and lanes

### M0 — Scaffold (1 lane, ~1 day)
Deliverables:
- Monorepo: `core/` (Rust), `capture/` (Python), `drivers/` (namespace packages),
  `web/` (front-end), `image/` (builder), `plugins/` (SDK + examples).
- Toolchain: `uv` for Python, `cargo` workspace for Rust, `just` or `make` taskfile.
- CI: GitHub Actions matrix — Rust (fmt, clippy, test), Python (ruff, pytest),
  front-end (build), image (build dry-run). Python 3.11–3.13.
Acceptance:
- `just ci` runs the full local gate and exits 0.
- Every package builds from a clean checkout with only the listed toolchain.
Verification commands (UNVERIFIED until run): `just ci`, `cargo test`, `uv run pytest`.

### M1 — Core skeleton (1 lane, ~3-5 days)
Deliverables:
- Supervisor with: service registry, per-service restart budgets, hardware
  watchdog feeding, crash-only state handling.
- SQLite WAL store with schema migrations; single-writer discipline enforced.
- Bus: JSON-lines over Unix sockets, topic publish/subscribe, backpressure by
  dropping metrics before events.
- Structured logging (JSON to file, rotation) and a local metrics snapshot.
Acceptance:
- Killing any worker mid-operation loses at most one event; state recovers on
  restart (proven by a fault-injection test).
- Watchdog resets the board if the core stops feeding (proven on hardware).
Verification: `cargo test core::`, fault-injection script in `core/tests/faults/`.

### M2 — Capture worker (1 lane, depends on M1 bus, ~5 days)
Deliverables:
- Python worker: brings up a monitor-mode interface using standard tools,
  cycles channels, writes rotating pcap files, emits summarized events
  (new network seen, association event, signal stats) to the bus.
- Configuration: channel dwell time, capture file size/count limits, allow/deny
  SSID lists (owner's own networks highlighted in UI).
- Resource guards: file-count cap, disk watermark that pauses capture at 90%.
Acceptance:
- On a QEMU/software-defined test interface, events appear on the bus with
  correct schema within the configured dwell time.
- Disk fills to the watermark without unbounded growth.
Verification: `uv run pytest capture/`, integration script with a virtual radio.

### M3 — Hardware HAL + first drivers (2 disjoint lanes, parallel with M2)
Lane A (Rust traits + registry): define `Display`, `Power`, `Indicator`, `Input`
traits, entry-point discovery, a `dummy` implementation for headless CI.
Lane B (first drivers): one e-ink display driver package (Waveshare-class),
one power-reading driver (UPS/battery voltage via ADC), the `dummy` set.
Acceptance:
- Swapping driver packages changes no core code (proven by config-only change test).
- Golden-image tests: each driver renders a known frame and the PNG matches.
Verification: `cargo test drivers::`, `just golden-render`.

### M4 — Web UI + API (1 lane, depends on M1, parallel with M2/M3)
Deliverables:
- JSON API: session status, network records (paged), peer list, config get/set
  (schema-validated), plugin list/status, event stream (SSE).
- Front-end: status dashboard, record browser, settings pages; static fallback.
Acceptance:
- OpenAPI schema published and validated in CI against the running API.
- All pages render and function in a headless browser test.
Verification: `uv run pytest web/`, `just api-schema-check`.

### M5 — Plugin system + SDK (1 lane, depends on M4 API surface, ~5 days)
Deliverables:
- Manifest format (name, API version, permissions, resource budget), host-side
  loader with sandboxed subprocess or WASM runtime, restart policy.
- SDK package with typed event/data contracts; three example plugins:
  (1) a UI widget, (2) a data exporter to local file, (3) a status display.
Acceptance:
- A plugin that crashes every 10 s never affects capture continuity (test).
- API-version mismatch is refused with a clear log line, not a crash.
Verification: `uv run pytest plugins/`, `just plugin-smoke`.

### M6 — Update system (1 lane, ~3 days)
Deliverables:
- A/B slot layout for the OS image; update = download to inactive slot,
  checksum, switch on next boot, boot-time health check, automatic rollback
  to the previous slot on failure or repeated watchdog resets.
Acceptance:
- Simulated failed update rolls back automatically within one reboot cycle.
Verification: `just image-ab-test` (QEMU), `cargo test update::`.

### M7 — Image builder + hardware bring-up (1 lane, ~5 days)
Deliverables:
- Declarative device description → reproducible image; built in CI for the
  supported board; flash instructions in docs.
- On-hardware bring-up checklist: display, buttons, battery reading, watchdog.
Acceptance:
- From a clean checkout, `just image build` produces a flashable image identical
  (by content hash) across two CI runs.
Verification: `just image build`, on-hardware checklist in `docs/bringup.md`.

### M8 — Docs + release (1 lane, ~2 days)
Deliverables: README, configuration reference (generated from schema), plugin
author guide, bring-up guide, signed v0.1.0 release with image assets.
Acceptance: docs build in CI; release notes list every component's version.

---

## Engineering rules for every lane
- Tests-first for state machines and parsers; behavior pinned before refactor.
- No secrets in the repo; device identity material generated at first boot.
- Config is schema-validated at boot; invalid config produces a readable error
  on the display and a logged diagnostic, never a crash loop.
- All external interfaces documented; no undocumented side channels.
- Each lane is disjoint by write scope: core/, capture/, drivers/, web/, plugins/,
  image/ are owned by exactly one lane at a time; shared schema files change only
  in the lane that owns that milestone, and other lanes consume published versions.

## Risks to watch
- Monitor-mode support varies by board/radio; keep the capture worker the only
  place that knows radio specifics so board quirks stay contained.
- SQLite write volume from event storms; batch event inserts (e.g. 100 ms window).
- Rust learning curve if the team is Python-first; the capture worker and plugins
  remain Python so ecosystem contributors have a home.

## Glossary for the coding agent
- "association event": a device joining or leaving a visible network.
- "peer": another FWC-class device observed nearby.
- "slot": a complete bootable OS image copy on its own partition.
