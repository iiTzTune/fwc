#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
cargo build --locked -p fwc-core
uv run --locked pytest -q capture/tests/test_virtual_radio.py
