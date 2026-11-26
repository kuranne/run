#!/usr/bin/env bash
# Full local verification (macOS / Linux): Rust gate + release build + pytest suite.
# Usage: scripts/test-local.sh [pytest args...]
# Examples:
#   scripts/test-local.sh
#   scripts/test-local.sh tests/blackbox/cli/test_mcp.py
#   scripts/test-local.sh -k "test_tasks"

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -z "${CARGO_HOME:-}" ] && [ -d "/tmp/run_cargo_home" ]; then
    export CARGO_HOME="/tmp/run_cargo_home"
fi

OFFLINE_FLAG=""
if [ "${CARGO_HOME:-}" = "/tmp/run_cargo_home" ]; then
    OFFLINE_FLAG="--offline"
fi

"$ROOT/scripts/dev-check.sh" $OFFLINE_FLAG

echo "==> cargo build --release"
cargo build $OFFLINE_FLAG --release

PYTEST_ARGS=("$@")
if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
    PYTEST_ARGS=("tests/")
fi

echo "==> pytest (${PYTEST_ARGS[*]})"
RUN_BIN="$ROOT/target/release/run" pytest -p no:cacheprovider -o pythonpath=". tests" "${PYTEST_ARGS[@]}"

find tests -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
