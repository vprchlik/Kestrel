#!/usr/bin/env bash
# Provision the Whimbrel toolchain. Mirrors docs/SETUP.md — keep in lockstep.
# Used by .cursor/environment.json to set up cloud-agent VMs; safe to re-run.
set -euo pipefail

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update
$SUDO apt-get install -y \
    qemu-system-misc \
    gdb-multiarch \
    build-essential \
    tshark \
    curl git

if ! command -v rustup >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
        sh -s -- -y --default-toolchain stable
    # shellcheck disable=SC1091
    . "$HOME/.cargo/env"
fi

# Run from the repo root so rust-toolchain.toml selects the pinned toolchain.
rustup target add riscv64gc-unknown-none-elf
rustup component add llvm-tools-preview rust-src

command -v just          >/dev/null 2>&1 || cargo install just
command -v cargo-objdump >/dev/null 2>&1 || cargo install cargo-binutils

echo "== toolchain verification =="
qemu-system-riscv64 --version | head -n1
rustc --version
just --version
echo "OK"
