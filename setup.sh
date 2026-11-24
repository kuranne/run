#!/usr/bin/env bash
set -euo pipefail

# Build and install the Rust `run` CLI binary into ~/.local/bin (or $PREFIX/bin).
PREFIX="${PREFIX:-$HOME/.local}"
BINDIR="${PREFIX}/bin"
MANDIR="${PREFIX}/share/man/man1"

if ! command -v cargo >/dev/null 2>&1; then
    echo "Error: cargo (Rust toolchain) is required. Install from https://rustup.rs" >&2
    exit 1
fi

echo "Building run (release)..."
cargo build --release

mkdir -p "${BINDIR}" "${MANDIR}"
install -m 755 "target/release/run" "${BINDIR}/run"
if [ -f "docs/man/run.1" ]; then
    install -m 644 "docs/man/run.1" "${MANDIR}/run.1"
fi

echo "Installed run to ${BINDIR}/run"
case ":${PATH}:" in
    *":${BINDIR}:"*) ;;
    *) echo "Add '${BINDIR}' to your PATH to invoke 'run' globally." ;;
esac
