#!/usr/bin/env bash
set -euo pipefail
# Debug-profile cargo wrapper (finding 14 / D-0055).
# Merges `-C force-frame-pointers=yes` onto the riscv target rustflags
# without replacing linker.ld in `.cargo/config.toml`. `RUSTFLAGS` would
# drop the linker script; `just` interpolation ate quotes on `--config`.
# Release builds must call `cargo` directly, not this wrapper.
exec cargo --config 'target.riscv64gc-unknown-none-elf.rustflags=["-C","force-frame-pointers=yes"]' "$@"
