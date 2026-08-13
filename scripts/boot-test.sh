#!/usr/bin/env bash
# Headless boot gate (D-0017). Verdict from serial + QEMU status together.
# Usage: scripts/boot-test.sh [cargo-feature]
#   no arg              → default image, expect PASS
#   panic-selftest      → FAIL (panic line echoed)
#   hang-selftest       → HANG
set -u

EXPECT="${EXPECT:-M0 BOOT OK}"
TIMEOUT_S="${TIMEOUT_S:-3}"
FEATURE="${1:-}"
TARGET="riscv64gc-unknown-none-elf"
KERNEL="target/${TARGET}/debug/kestrel"
QEMU="qemu-system-riscv64"
QEMU_ARGS=(-machine virt -nographic -bios default)

feat=()
if [ -n "$FEATURE" ]; then
    feat=(--features "$FEATURE")
fi

cargo build "${feat[@]}"
rm -f serial.log

set +e
timeout --foreground "$TIMEOUT_S" "$QEMU" "${QEMU_ARGS[@]}" -kernel "$KERNEL" > serial.log 2>&1
status=$?
set -e

if grep -a -q 'PANIC' serial.log; then
    echo 'TEST FAIL: panic'
    grep -a 'PANIC' serial.log
    exit 1
fi
if [ "$status" -eq 124 ]; then
    echo "TEST HANG: timed out after ${TIMEOUT_S}s waiting for \"${EXPECT}\""
    exit 2
fi
if grep -a -q "$EXPECT" serial.log && [ "$status" -eq 0 ]; then
    echo "TEST PASS: found \"${EXPECT}\""
    exit 0
fi
echo "TEST FAIL: \"${EXPECT}\" not found (qemu status=${status})"
echo '----------------------------------------'
cat serial.log
exit 1
