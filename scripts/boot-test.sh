#!/usr/bin/env bash
# Headless boot gate (D-0017). Verdict from serial + QEMU status together.
# Usage: scripts/boot-test.sh [cargo-feature]
#   no arg              → default image, expect PASS
#   panic-selftest      → FAIL (panic line echoed)
#   hang-selftest       → HANG
#
# After DRIVER_OK, the net-init-selftest sibling still fires a hostfwd
# TCP connect so slirp ARPs 10.0.2.15 (D-0046). Default / HTTP / UDP /
# fast-boot images ARP for the gateway themselves (D-0054) and must not
# wait to be asked. Panic/hang images never print DRIVER_OK.
set -u

EXPECT="${EXPECT:-M3 UNIKERNEL OK}"
TIMEOUT_S="${TIMEOUT_S:-5}"
FEATURE="${1:-}"
TARGET="riscv64gc-unknown-none-elf"
KERNEL="target/${TARGET}/debug/whimbrel"
QEMU="qemu-system-riscv64"
# D-0038 / D-0039 / D-0042 / D-0043: keep in sync with justfile qemu_args
# and .cargo/config.toml.
QEMU_ARGS=(-machine virt -nographic -bios default -global virtio-mmio.force-legacy=false -netdev user,id=net0,hostfwd=tcp::8080-:80,hostfwd=udp::7777-:7 -device virtio-net-device,netdev=net0 -object filter-dump,id=f0,netdev=net0,file=whimbrel.pcap)

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
rm -f serial.log whimbrel.pcap udp-echo.got udp-echo.status http.body http.hdr http.status

# D-0046 watcher: net-init-selftest handshake sibling only (D-0054).
wpid=""
if [ "$FEATURE" = "net-init-selftest" ]; then
    (
        while ! grep -a -q 'DRIVER_OK' serial.log 2>/dev/null; do
            sleep 0.05
        done
        bash scripts/provoke-hostfwd.sh 0.3 >/dev/null 2>&1
        while ! grep -a -q 'TX ARP reply' serial.log 2>/dev/null; do
            sleep 0.05
        done
        bash scripts/provoke-hostfwd.sh 2 >/dev/null 2>&1
    ) &
    wpid=$!
fi

# T3.8: do not send UDP until the guest is polling for it. Recv timeout
# is inside provoke-udp-echo.py (2s) so a silent guest fails closed.
upid=""
if [ "$FEATURE" = "net-udp-selftest" ]; then
    (
        while ! grep -a -q 'UDP ECHO READY' serial.log 2>/dev/null; do
            sleep 0.05
        done
        python3 scripts/provoke-udp-echo.py udp-echo.got
        echo $? >udp-echo.status
    ) &
    upid=$!
fi

hpid=""
if [ -z "$FEATURE" ] || [ "$FEATURE" = "net-http-selftest" ] \
    || [ "$FEATURE" = "tcp-drop-first-tx" ] || [ "$FEATURE" = "fast-boot" ]; then
    (
        while ! grep -a -q 'HTTP READY' serial.log 2>/dev/null; do
            sleep 0.05
        done
        curl -sS --max-time 5 -D http.hdr -o http.body http://127.0.0.1:8080/
        echo $? >http.status
    ) &
    hpid=$!
fi

set +e
if command -v stdbuf >/dev/null 2>&1; then
    timeout --foreground "$TIMEOUT_S" stdbuf -oL "$QEMU" "${QEMU_ARGS[@]}" -kernel "$KERNEL" > serial.log 2>&1
else
    timeout --foreground "$TIMEOUT_S" "$QEMU" "${QEMU_ARGS[@]}" -kernel "$KERNEL" > serial.log 2>&1
fi
status=$?
if [ -n "$wpid" ]; then
    kill "$wpid" 2>/dev/null
    wait "$wpid" 2>/dev/null
fi
if [ -n "$upid" ]; then
    kill "$upid" 2>/dev/null
    wait "$upid" 2>/dev/null
fi
if [ -n "$hpid" ]; then
    # Guest shutdown races the host `echo $? >http.status` after curl's
    # TCP close. Wait briefly so a clean 200 is not a missing file.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if [ -f http.status ]; then
            break
        fi
        sleep 0.05
    done
    kill "$hpid" 2>/dev/null
    wait "$hpid" 2>/dev/null
fi
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
