ci: rust python image image-ab-test

rust:
    cargo fmt --all -- --check
    cargo clippy --workspace --all-targets --locked -- -D warnings
    cargo test --workspace --locked
    cargo build --workspace --locked
    uv run --locked python core/tests/faults/recovery.py
    uv run --locked python core/tests/driver_swap.py
    uv run --locked pytest -q capture/tests/test_virtual_radio.py

python:
    uv run --locked ruff check .
    uv run --locked pytest
    uv build --all-packages --out-dir build/packages

web:
    mkdir -p build/web
    cp web/index.html web/fallback.html web/server.py web/app.js web/style.css web/openapi.json build/web/

image: web
    uv run --locked python image/dry_run.py

golden-render:
    uv run --locked pytest drivers/tests/test_golden.py

api-schema-check:
    uv run --locked pytest -q web/tests/test_api.py

image-ab-test:
    cargo build --locked --bin fwc-update
    python3 image/tests/qemu_ab.py
