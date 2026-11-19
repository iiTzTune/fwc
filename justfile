ci: rust python image

rust:
    cargo fmt --all -- --check
    cargo clippy --workspace --all-targets --locked -- -D warnings
    cargo test --workspace --locked
    cargo build --workspace --locked
    uv run --locked python core/tests/faults/recovery.py

python:
    uv run --locked ruff check .
    uv run --locked pytest
    uv build --all-packages --out-dir build/packages

web:
    mkdir -p build/web
    cp web/index.html build/web/index.html

image: web
    uv run --locked python image/dry_run.py
