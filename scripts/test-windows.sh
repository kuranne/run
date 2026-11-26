#!/usr/bin/env bash
# Sync working tree and run Rust + unelevated pytest suite on the Windows 11 UTM VM via SSH.
# Usage: scripts/test-windows.sh [--no-sync] [--skip-rust] [pytest args...]
# Env overrides:
#   WIN_HOST (default: dev@192.168.64.3)
#   WIN_DIR  (default: C:/Users/dev/run)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WIN_HOST="${WIN_HOST:-dev@192.168.64.3}"
WIN_DIR="${WIN_DIR:-C:/Users/dev/run}"
DO_SYNC=1
RUN_RUST=1
PYTEST_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --no-sync) DO_SYNC=0 ;;
        --skip-rust) RUN_RUST=0 ;;
        *) PYTEST_ARGS+=("$arg") ;;
    esac
done

if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
    PYTEST_ARGS=("tests/")
fi

if [ "$DO_SYNC" -eq 1 ]; then
    echo "==> Syncing workspace to $WIN_HOST:$WIN_DIR"
    tar -cf - \
        --exclude='.git' \
        --exclude='target' \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='.pytest_cache' \
        --exclude='.run_cache' \
        --exclude='windows-log.log' \
        . | ssh "$WIN_HOST" "tar -xf - -C \"$WIN_DIR\""
fi

WIN_DIR_BS="${WIN_DIR//\//\\}"
VCVARS='C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat'
EXTRA_PATH='C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207\bin\Hostarm64\arm64;C:\Users\dev\.cargo\bin;C:\Users\dev\AppData\Local\Programs\Python\Python312-arm64;C:\Users\dev\AppData\Local\Programs\Python\Python312-arm64\Scripts;C:\Users\dev\AppData\Local\Microsoft\WinGet\Links'
TAIL_PATH='C:\Program Files\Git\usr\bin'

RUST_CMD=""
if [ "$RUN_RUST" -eq 1 ]; then
    RUST_CMD="cargo test --workspace -q && "
fi

PYTEST_STR="${PYTEST_ARGS[*]}"
echo "==> Running Windows build & tests on $WIN_HOST ($PYTEST_STR)"
ssh "$WIN_HOST" "call \"$VCVARS\" arm64 && set \"PATH=$EXTRA_PATH;%PATH%;$TAIL_PATH\" && set \"RUN_BIN=$WIN_DIR_BS\\target\\release\\run.exe\" && cd \"$WIN_DIR_BS\" && ${RUST_CMD}cargo build --release && python tests/run_unelevated.py -m pytest -p no:cacheprovider -o pythonpath=\". tests\" $PYTEST_STR"
