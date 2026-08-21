#!/usr/bin/env bash
set -euo pipefail
# M4 benchmark surface (D-0055 / T4.1). Implementation is scripts/bench.py;
# this wrapper is the name PLAN.md and just bench-* invoke.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 scripts/bench.py "$@"
