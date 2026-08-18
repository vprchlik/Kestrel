#!/usr/bin/env bash
# Linux boot gate (D-0062 / T4.8). Analogous to boot-test.sh for Whimbrel.
# Fail-closed if artifacts / MANIFEST are missing — never a skip.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TIMEOUT_S="${TIMEOUT_S:-60}"
IMAGE_NAME="${LINUX_IMAGE:-Image-trimmed}"
APPEND="${LINUX_APPEND:-console=ttyS0 quiet loglevel=0 rdinit=/init}"
ART="$ROOT/bench/linux/artifacts"
IMAGE="$ART/$IMAGE_NAME"
CPIO="$ART/rootfs.cpio"

WORKDIR=$(mktemp -d)
man_out=$(mktemp)
man_err=$(mktemp)
cleanup() { rm -rf "$WORKDIR"; rm -f "$man_out" "$man_err"; }
trap cleanup EXIT

if ! python3 "$ROOT/scripts/bench.py" check-linux-artifacts "$IMAGE_NAME" rootfs.cpio init \
    >"$man_out" 2>"$man_err"; then
    cat "$man_err" "$man_out"
    if ! grep -q 'linux artifact missing\|linux artifact mismatch' "$man_err" "$man_out"; then
        echo "TEST FAIL: linux artifact missing: $ART/$IMAGE_NAME"
    fi
    exit 1
fi
if [ ! -f "$IMAGE" ] || [ ! -s "$IMAGE" ]; then
    echo "TEST FAIL: linux artifact missing: $IMAGE"
    exit 1
fi
if [ ! -f "$CPIO" ] || [ ! -s "$CPIO" ]; then
    echo "TEST FAIL: linux artifact missing: $CPIO"
    exit 1
fi

# shellcheck disable=SC1091
source "$ROOT/scripts/qemu-args.sh"
PCAP="$WORKDIR/linux-boot.pcap"
SERIAL="$WORKDIR/serial.log"
CLIENT_OUT="$WORKDIR/client.json"
READY="$WORKDIR/client.ready"
qemu_args_fill "$PCAP" "${LINUX_BOOT_PORT:-8080}"

python3 "$ROOT/scripts/bench-client.py" \
    --port "${LINUX_BOOT_PORT:-8080}" \
    --timeout-s "$TIMEOUT_S" \
    --ready "$READY" \
    --out "$CLIENT_OUT" &
hpid=$!
t0=$(date +%s)
while [ ! -f "$READY" ]; do
    now=$(date +%s)
    if [ $((now - t0)) -gt 5 ]; then
        echo "TEST FAIL: measurement client never became ready"
        kill "$hpid" 2>/dev/null || true
        wait "$hpid" 2>/dev/null || true
        exit 1
    fi
    sleep 0.01
done

set +e
if command -v stdbuf >/dev/null 2>&1; then
    timeout --foreground "$TIMEOUT_S" stdbuf -oL "$QEMU" "${QEMU_ARGS[@]}" \
        -kernel "$IMAGE" -initrd "$CPIO" -append "$APPEND" >"$SERIAL" 2>&1
else
    timeout --foreground "$TIMEOUT_S" "$QEMU" "${QEMU_ARGS[@]}" \
        -kernel "$IMAGE" -initrd "$CPIO" -append "$APPEND" >"$SERIAL" 2>&1
fi
status=$?
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [ -f "$CLIENT_OUT" ]; then
        break
    fi
    sleep 0.05
done
kill "$hpid" 2>/dev/null
wait "$hpid" 2>/dev/null
set -e

if grep -a -q 'Kernel panic' "$SERIAL"; then
    echo "TEST FAIL: panic"
    grep -a 'Kernel panic' "$SERIAL" || true
    exit 1
fi
if grep -a -q 'INIT FAIL:' "$SERIAL"; then
    echo "TEST FAIL: Linux /init INIT FAIL"
    grep -a 'INIT FAIL:' "$SERIAL" || true
    exit 1
fi
if [ "$status" -eq 124 ]; then
    echo "TEST HANG: timed out after ${TIMEOUT_S}s waiting for LINUX INIT OK"
    cat "$SERIAL"
    exit 2
fi
if ! grep -a -q 'READY' "$SERIAL"; then
    echo "TEST FAIL: Linux READY missing (qemu status=${status})"
    cat "$SERIAL"
    exit 1
fi
if ! grep -a -q 'LINUX INIT OK' "$SERIAL"; then
    echo "TEST FAIL: LINUX INIT OK not found (qemu status=${status})"
    echo '----------------------------------------'
    cat "$SERIAL"
    exit 1
fi
if [ ! -f "$CLIENT_OUT" ]; then
    echo "TEST FAIL: client result JSON missing"
    exit 1
fi
if ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("body_ok") else 1)' \
    "$CLIENT_OUT"; then
    echo "TEST FAIL: client did not receive the 92-byte RESP"
    cat "$CLIENT_OUT"
    exit 1
fi
if ! bash "$ROOT/scripts/assert-pcap-http.sh" "$PCAP"; then
    echo "TEST FAIL: pcap HTTP assertion (RST/FIN/92-byte)"
    exit 1
fi
if ! bash "$ROOT/scripts/assert-pcap-syn-grid.sh" "$PCAP"; then
    echo "TEST FAIL: SYN-grid (confound A)"
    exit 1
fi
echo "TEST PASS: LINUX INIT OK, 92-byte RESP, clean FIN, SYN-grid, no RST"
exit 0
