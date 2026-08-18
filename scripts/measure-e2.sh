#!/usr/bin/env bash
# T3.12(a) / D-0043: freeze at reset, read time via GDB before the first
# guest instruction. rdtime at _start minus that offset is the OpenSBI
# phase. Fail closed if the stub is unreachable or $time is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TARGET="riscv64gc-unknown-none-elf"
KERNEL="target/${TARGET}/debug/whimbrel"
QEMU="qemu-system-riscv64"
PORT="${E2_GDB_PORT:-1234}"

# Debug image: keep frame pointers for GDB (finding 14).
bash scripts/cargo-debug.sh build

if [ ! -f "$KERNEL" ]; then
    echo "TEST FAIL: no kernel at ${KERNEL}"
    exit 1
fi
if ! command -v gdb-multiarch >/dev/null 2>&1; then
    echo 'TEST FAIL: gdb-multiarch not installed (see docs/SETUP.md)'
    exit 1
fi

# Finding 28: argv lives in qemu-args.sh (csum=off / TSO-family off
# included). This freeze-at-reset path adds -S -gdb on top.
# shellcheck disable=SC1091
source "$ROOT/scripts/qemu-args.sh"

log=$(mktemp)
gdbout=$(mktemp)
pcap=$(mktemp --suffix=.pcap)
qpid=""
cleanup() {
    if [ -n "$qpid" ]; then
        kill "$qpid" 2>/dev/null || true
        wait "$qpid" 2>/dev/null || true
    fi
    rm -f "$log" "$gdbout" "$pcap"
}
trap cleanup EXIT

qemu_args_fill "$pcap" "${E2_TCP_PORT:-18080}"

"$QEMU" "${QEMU_ARGS[@]}" -S -gdb "tcp::${PORT}" \
    -kernel "$KERNEL" >"$log" 2>&1 &
qpid=$!

gst=1
for _ in $(seq 1 50); do
    set +e
    gdb-multiarch -batch \
        -ex "set pagination off" \
        -ex "set confirm off" \
        -ex "target remote :${PORT}" \
        -ex "p/x \$pc" \
        -ex "p/x \$time" \
        -ex "detach" \
        -ex "quit" >"$gdbout" 2>&1
    gst=$?
    set -e
    if [ "$gst" -eq 0 ] && grep -q '^\$1 = ' "$gdbout" && grep -q '^\$2 = ' "$gdbout"; then
        break
    fi
    sleep 0.1
done
if [ "$gst" -ne 0 ] || ! grep -q '^\$1 = ' "$gdbout"; then
    echo "TEST FAIL: gdb-multiarch could not read \$pc/\$time on :${PORT}"
    cat "$gdbout"
    cat "$log" || true
    exit 1
fi

pc=$(awk '/^\$1 = / {print $3; exit}' "$gdbout")
time=$(awk '/^\$2 = / {print $3; exit}' "$gdbout")
if [ -z "$pc" ] || [ -z "$time" ]; then
    echo "TEST FAIL: could not parse \$pc / \$time from gdb"
    cat "$gdbout"
    exit 1
fi

echo "E2 reset pc=${pc} time=${time}"
if [ "$pc" != "0x1000" ]; then
    echo "TEST FAIL: reset pc is ${pc}, want 0x1000 (OpenSBI)"
    cat "$gdbout"
    exit 1
fi
if [ "$time" != "0x0" ] && [ "$time" != "0" ]; then
    echo "TEST FAIL: reset \$time is ${time}, want 0 (D-0043 E2 offset)"
    cat "$gdbout"
    exit 1
fi
echo 'TEST PASS: E2 offset 0 (rdtime at _start is the OpenSBI phase)'
