#!/usr/bin/env bash
# D-0079: extract the M-mode shim blob from the donor ELF for QEMU's
# -bios slot. The donor exists only to be objcopied (LLD pads a
# discontiguous .mshim LOAD with ~2 MB of file zeros — DEBUGGING.md);
# booting the donor under its own blob makes QEMU refuse (overlap),
# which is the guard against mixing donor and lane kernels up.
set -euo pipefail
ELF="${1:?usage: mshim-blob.sh <donor-elf> <out.bin>}"
OUT="${2:?usage: mshim-blob.sh <donor-elf> <out.bin>}"
OBJCOPY=""
for oc in llvm-objcopy rust-objcopy riscv64-linux-gnu-objcopy \
    riscv64-buildroot-linux-musl-objcopy \
    bench/linux/build/buildroot-*/output/host/bin/riscv64-buildroot-linux-musl-objcopy; do
    if command -v "$oc" >/dev/null 2>&1 || [ -x "$oc" ]; then
        OBJCOPY="$oc"
        break
    fi
done
[ -n "$OBJCOPY" ] || {
    echo "TEST FAIL: no objcopy (llvm/rust/riscv64-*-objcopy); see docs/SETUP.md" >&2
    exit 1
}
"$OBJCOPY" -O binary --only-section=.mshim "$ELF" "$OUT"
size=$(stat -c%s "$OUT")
# Fail-closed on both ends: 0 = donor built without the mshim feature;
# large = the section picked up something that is not the shim.
if [ "$size" -lt 16 ] || [ "$size" -gt 4096 ]; then
    echo "TEST FAIL: mshim blob is $size bytes (want 16..4096); donor built without --features mshim?" >&2
    exit 1
fi
echo "mshim-blob: $OUT ($size bytes)"
