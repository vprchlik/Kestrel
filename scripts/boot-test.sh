#!/usr/bin/env bash
# Headless boot gate (D-0017). Verdict from serial + QEMU status together.
# Usage: scripts/boot-test.sh [cargo-feature]
#   no arg              → default image, expect PASS
#   panic-selftest      → FAIL (panic line echoed)
#   hang-selftest       → HANG
#
# After DRIVER_OK, a sibling watcher fires a hostfwd TCP connect so slirp
# ARPs 10.0.2.15 (T3.5). That connect is *before* our GARP (D-0046): slirp
# has no cached MAC and must ARP. After serial shows `TX ARP reply`, a
# second connect proves slirp proceeds past ARP (IPv4 SYN). Neither
# connect is waited on for a handshake — the guest has no TCP yet.
# Panic/hang images never print DRIVER_OK, so they are not provoked.
# The watcher is killed when QEMU exits; it does not own the timeout
# (timeout still parents QEMU, same as T0.5).
set -u

EXPECT="${EXPECT:-M2 EXECUTION OK}"
TIMEOUT_S="${TIMEOUT_S:-5}"
FEATURE="${1:-}"
TARGET="riscv64gc-unknown-none-elf"
KERNEL="target/${TARGET}/debug/whimbrel"
QEMU="qemu-system-riscv64"
# D-0038 / D-0039 / D-0042 / D-0043: keep in sync with justfile qemu_args
# and .cargo/config.toml.
QEMU_ARGS=(-machine virt -nographic -bios default -global virtio-mmio.force-legacy=false -netdev user,id=net0,hostfwd=tcp::8080-:80 -device virtio-net-device,netdev=net0 -object filter-dump,id=f0,netdev=net0,file=whimbrel.pcap)

feat=()
if [ -n "$FEATURE" ]; then
    feat=(--features "$FEATURE")
fi

cargo build "${feat[@]}"
# Feature builds that never enter U-mode can GC .utext. Userptr selftests
# do enter U, so they still need the check.
case "${FEATURE}" in
    panic-selftest|hang-selftest|stress|frame-exhaust-selftest) ;;
    *) bash scripts/check-utext.sh "$KERNEL" ;;
esac
rm -f serial.log whimbrel.pcap

# Watch serial for DRIVER_OK, then provoke twice (D-0046). Must start
# before QEMU so a fast guest cannot print DRIVER_OK unobserved. grep
# on a missing file is a miss, not a pass.
(
    while ! grep -a -q 'DRIVER_OK' serial.log 2>/dev/null; do
        sleep 0.05
    done
    bash scripts/provoke-hostfwd.sh >/dev/null 2>&1
    while ! grep -a -q 'TX ARP reply' serial.log 2>/dev/null; do
        sleep 0.05
    done
    bash scripts/provoke-hostfwd.sh >/dev/null 2>&1
) &
wpid=$!

set +e
if command -v stdbuf >/dev/null 2>&1; then
    timeout --foreground "$TIMEOUT_S" stdbuf -oL "$QEMU" "${QEMU_ARGS[@]}" -kernel "$KERNEL" > serial.log 2>&1
else
    timeout --foreground "$TIMEOUT_S" "$QEMU" "${QEMU_ARGS[@]}" -kernel "$KERNEL" > serial.log 2>&1
fi
status=$?
kill "$wpid" 2>/dev/null
wait "$wpid" 2>/dev/null
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
