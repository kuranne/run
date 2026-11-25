#!/usr/bin/env bash
# Fast local Rust verification gate: tests, clippy (-D warnings), rustdoc (-D warnings), and pycache cleanup.
# Usage: scripts/dev-check.sh [--offline]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -z "${CARGO_HOME:-}" ] && [ -d "/tmp/run_cargo_home" ]; then
    export CARGO_HOME="/tmp/run_cargo_home"
fi

OFFLINE_FLAG=""
if [ "${1:-}" = "--offline" ] || [ "${CARGO_HOME:-}" = "/tmp/run_cargo_home" ]; then
    OFFLINE_FLAG="--offline"
fi

echo "==> cargo test --workspace"
cargo test $OFFLINE_FLAG --workspace -q

echo "==> cargo clippy (-D warnings)"
cargo clippy $OFFLINE_FLAG --workspace --all-targets -- -D warnings

echo "==> cargo doc (-D warnings)"
RUSTDOCFLAGS="-D warnings" cargo doc $OFFLINE_FLAG --workspace --no-deps

find tests -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
echo "==> dev-check passed."
