#!/usr/bin/env bash
# D-0072: one ignore_loglevel boot of Image-trimmed to label the 327 ms
# printk hole. Not a campaign arm. Not a runs.csv writer.
#
# Same client-before-QEMU / SYN-grid / RST / 92-byte shape as
# linux-boot-test.sh so /init still walks listen→response. The only
# cmdline delta vs the T4.8 instrumented MANIFEST append is
# ignore_loglevel.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TIMEOUT_S="${TIMEOUT_S:-60}"
PORT="${LINUX_BOOT_PORT:-8091}"
ART="$ROOT/bench/linux/artifacts"
MANIFEST="$ROOT/bench/linux/MANIFEST"
IMAGE="$ART/Image-trimmed"
CPIO="$ART/rootfs.cpio"
INIT="$ART/init"
# Exact D-0072 cmdline: instrumented MANIFEST line + ignore_loglevel.
APPEND='console=ttyS0 loglevel=7 printk.time=1 initcall_debug ignore_loglevel rdinit=/init'
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTDIR="$ROOT/results/serial"
SERIAL_OUT="$OUTDIR/linux-trimmed-ignore-loglevel-${STAMP}.log"
LABEL_OUT="$OUTDIR/linux-trimmed-ignore-loglevel-${STAMP}-initcalls.txt"

die() { echo "TEST FAIL: $*" >&2; exit 1; }

find_system_map() {
    if [[ -s "$ART/System.map-trimmed" ]]; then
        echo "$ART/System.map-trimmed"
        return
    fi
    if [[ -s "$ROOT/bench/linux/build/linux-trimmed/System.map" ]]; then
        echo "$ROOT/bench/linux/build/linux-trimmed/System.map"
        return
    fi
    die "System.map-trimmed missing (do not rebuild Image-trimmed to recover)"
}

WORKDIR=$(mktemp -d)
man_out=$(mktemp)
man_err=$(mktemp)
cleanup() { rm -rf "$WORKDIR"; rm -f "$man_out" "$man_err"; }
trap cleanup EXIT

if ! python3 "$ROOT/scripts/bench.py" check-linux-artifacts \
    Image-trimmed rootfs.cpio init >"$man_out" 2>"$man_err"; then
    cat "$man_err" "$man_out"
    if ! grep -q 'linux artifact missing\|linux artifact mismatch' \
        "$man_err" "$man_out"; then
        echo "TEST FAIL: linux artifact missing: $IMAGE"
    fi
    exit 1
fi
need() { [[ -s "$1" ]] || die "linux artifact missing: $1"; }
need "$IMAGE"
need "$CPIO"
need "$INIT"
[[ -f "$MANIFEST" ]] || die "linux artifact missing: $MANIFEST"

SMAP="$(find_system_map)"
want_hash="$(awk '$1=="artifact" && $2=="Image-trimmed" { print $3; exit }' "$MANIFEST")"
[[ -n "$want_hash" ]] || die "MANIFEST missing artifact Image-trimmed"
got_hash="$(sha256sum "$IMAGE" | awk '{print $1}')"
[[ "$got_hash" == "$want_hash" ]] \
    || die "Image-trimmed sha256=$got_hash want $want_hash (not the T4.8 binary)"

# shellcheck disable=SC1091
source "$ROOT/scripts/qemu-args.sh"
PCAP="$WORKDIR/linux-initcall.pcap"
SERIAL="$WORKDIR/serial.log"
CLIENT_OUT="$WORKDIR/client.json"
CLIENT_READY="$WORKDIR/client.ready"
qemu_args_fill "$PCAP" "$PORT"

python3 "$ROOT/scripts/bench-client.py" \
    --port "$PORT" \
    --timeout-s "$TIMEOUT_S" \
    --ready "$CLIENT_READY" \
    --out "$CLIENT_OUT" &
hpid=$!
t0=$(date +%s)
while [ ! -f "$CLIENT_READY" ]; do
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
kill "$hpid" 2>/dev/null || true
wait "$hpid" 2>/dev/null || true
set -e

mkdir -p "$OUTDIR"
cp -a "$SERIAL" "$SERIAL_OUT"

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
    echo "D-0072: extra KERN_DEBUG UART may need a one-time TIMEOUT_S raise"
    cat "$SERIAL"
    exit 2
fi
if [ "$status" -ne 0 ]; then
    die "QEMU status=$status (want 0 after SBI SRST poweroff)"
fi
if ! grep -aE -q $'^READY\r?$' "$SERIAL"; then
    echo "TEST FAIL: Linux READY missing (qemu status=${status})"
    cat "$SERIAL"
    exit 1
fi
if ! grep -a -q 'LINUX INIT OK' "$SERIAL"; then
    echo "TEST FAIL: LINUX INIT OK not found (qemu status=${status})"
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

PYTHONPATH="$ROOT/scripts" python3 - "$PCAP" <<'PY'
import sys
from pathlib import Path
import pcap_http

pcap = Path(sys.argv[1])
try:
    tshark = pcap_http.require_tshark()
    pcap_http.assert_no_rst(pcap, tshark)
    dt = pcap_http.assert_syn_grid(pcap, tshark)
    rows = pcap_http.tshark_table(
        pcap, tshark, pcap_http.HTTP_FILTER, extra=("tcp.len",)
    )
    if not rows:
        raise pcap_http.PcapExtractError(
            f"TEST FAIL: no HTTP 200 data frame in {pcap}"
        )
    http_len = int(rows[0]["tcp.len"])
    if http_len != pcap_http.HTTP_LEN:
        raise pcap_http.PcapExtractError(
            f"TEST FAIL: HTTP tcp.len is {http_len}, "
            f"want {pcap_http.HTTP_LEN} in {pcap}"
        )
except pcap_http.PcapExtractError as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
print(
    f"TEST PASS: pcap no RST, SYN-grid dt={dt * 1e6:.1f} µs, "
    f"HTTP tcp.len={http_len}"
)
PY

map_sha="$(sha256sum "$SMAP" | awk '{print $1}')"
qemu_ver="$("$QEMU" --version | head -n1)"
body="$(python3 "$ROOT/scripts/label-linux-initcalls.py" "$SERIAL_OUT" "$SMAP")"
{
    echo "# generated by scripts/linux-initcall-label.sh — D-0072"
    echo "# Image-trimmed sha256=$got_hash"
    echo "# System.map sha256=$map_sha path=$SMAP"
    echo "# cmdline=$APPEND"
    echo "# qemu=$qemu_ver"
    echo "# serial=$SERIAL_OUT"
    echo "#"
    echo "$body"
} > "$LABEL_OUT"

echo "TEST PASS: D-0072 diagnostic serial $SERIAL_OUT"
echo "TEST PASS: initcall labels $LABEL_OUT"
exit 0
