#!/usr/bin/env bash
# Provision the Whimbrel toolchain. Mirrors docs/SETUP.md §1–§2 — keep in lockstep.
# Safe to re-run. Does not (and must not) try to turn a VM into the D-0055
# bench host.
set -euo pipefail

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update

# Ubuntu 26.04 split RISC-V QEMU into qemu-system-riscv; qemu-system-riscv64
# no longer ships from qemu-system-misc. 24.04/22.04 still use the old
# package. Prefer the new name when the archive has it.
if apt-cache show qemu-system-riscv >/dev/null 2>&1; then
    QEMU_PKG=qemu-system-riscv
else
    QEMU_PKG=qemu-system-misc
fi

$SUDO apt-get install -y \
    "$QEMU_PKG" \
    gdb-multiarch \
    build-essential \
    tshark \
    curl git

if ! command -v qemu-system-riscv64 >/dev/null 2>&1; then
    echo "qemu-system-riscv64 not on PATH after installing $QEMU_PKG" >&2
    exit 1
fi

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
