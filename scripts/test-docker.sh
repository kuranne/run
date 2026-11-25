#!/usr/bin/env bash
# Build and run the full lint + doc + Rust + pytest suite inside Fedora Linux (Dockerfile.test).
# Usage: scripts/test-docker.sh [--shell]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${RUN_TEST_IMAGE:-run-test}"

echo "==> Building $IMAGE from Dockerfile.test"
docker build -f Dockerfile.test -t "$IMAGE" .

if [ "${1:-}" = "--shell" ]; then
    exec docker run --rm -it "$IMAGE" /bin/bash
fi

echo "==> Running make lint doc test inside $IMAGE"
exec docker run --rm "$IMAGE"
