#!/usr/bin/env bash
# Linux boot gate (D-0062 / T4.8). Fail-closed if artifacts / MANIFEST
# are missing — never a skip.
#
# curl starts before QEMU so slirp has a queued hostfwd SYN when
# /init's first wire TX flushes it (SYN-grid / confound A). Waiting
# for serial READY and then spawning curl is D-0043 harness wait:
# t(SYN) then trails t(TX) by UART+grep+spawn and the 1 ms gate fails.
# ttyS0 emits READY\r\n; the serial pin is CRLF-tolerant. curl -o is
# the 9-byte entity (Content-Length: 9); the 92-byte pin is the on-wire
# RESP / pcap HTTP_FILTER (pcap_http.HTTP_LEN).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TIMEOUT_S="${TIMEOUT_S:-60}"
PORT="${LINUX_BOOT_PORT:-8080}"
WHICH="${1:-trimmed}"
ART="$ROOT/bench/linux/artifacts"
MANIFEST="$ROOT/bench/linux/MANIFEST"

die() { echo "TEST FAIL: $*" >&2; exit 1; }

case "$WHICH" in
    trimmed) IMAGE_NAME=Image-trimmed ;;
    stock)   IMAGE_NAME=Image-stock ;;
    *) die "usage: linux-boot-test.sh [trimmed|stock]" ;;
esac

IMAGE="$ART/$IMAGE_NAME"
CPIO="$ART/rootfs.cpio"
INIT="$ART/init"

WORKDIR=$(mktemp -d)
man_out=$(mktemp)
man_err=$(mktemp)
cleanup() { rm -rf "$WORKDIR"; rm -f "$man_out" "$man_err"; }
trap cleanup EXIT

if ! python3 "$ROOT/scripts/bench.py" check-linux-artifacts \
    "$IMAGE_NAME" rootfs.cpio init >"$man_out" 2>"$man_err"; then
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

append_quiet="$(awk '$1=="append_quiet" { $1=""; sub(/^ /,""); print; exit }' "$MANIFEST")"
[[ -n "$append_quiet" ]] || die "MANIFEST missing append_quiet"

# shellcheck disable=SC1091
source "$ROOT/scripts/qemu-args.sh"
PCAP="$WORKDIR/linux-boot.pcap"
SERIAL="$WORKDIR/serial.log"
HTTP_HDR="$WORKDIR/http.hdr"
HTTP_BODY="$WORKDIR/http.body"
HTTP_STATUS="$WORKDIR/http.status"
qemu_args_fill "$PCAP" "$PORT"

# One successful connect, no --retry after that: extra SYNs after
# serve-once would RST. Retry only curl's "failed to connect" (7)
# while QEMU has not bound hostfwd yet. Once slirp accepts, this
# curl blocks until /init's first TX flushes the queued SYN.
(
    while true; do
        curl -sS --max-time "$TIMEOUT_S" \
            -D "$HTTP_HDR" -o "$HTTP_BODY" \
            "http://127.0.0.1:${PORT}/"
        rc=$?
        if [ "$rc" -eq 0 ]; then
            echo 0 >"$HTTP_STATUS"
            break
        fi
        if [ "$rc" -ne 7 ]; then
            echo "$rc" >"$HTTP_STATUS"
            break
        fi
        sleep 0.001
    done
) &
hpid=$!
sleep 0.05

set +e
if command -v stdbuf >/dev/null 2>&1; then
    timeout --foreground "$TIMEOUT_S" stdbuf -oL "$QEMU" "${QEMU_ARGS[@]}" \
        -kernel "$IMAGE" -initrd "$CPIO" -append "$append_quiet" >"$SERIAL" 2>&1
else
    timeout --foreground "$TIMEOUT_S" "$QEMU" "${QEMU_ARGS[@]}" \
        -kernel "$IMAGE" -initrd "$CPIO" -append "$append_quiet" >"$SERIAL" 2>&1
fi
status=$?
for _ in $(seq 1 20); do
    [[ -f "$HTTP_STATUS" ]] && break
    sleep 0.05
done
kill "$hpid" 2>/dev/null || true
wait "$hpid" 2>/dev/null || true
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
if [ "$status" -ne 0 ]; then
    die "QEMU status=$status (want 0 after SBI SRST poweroff)"
fi
# ttyS0 is a terminal: /init writes READY\n, the UART emits READY\r\n.
if ! grep -aE -q $'^READY\r?$' "$SERIAL"; then
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
[[ -f "$HTTP_STATUS" ]] || die "curl watcher left no http.status"
[[ "$(cat "$HTTP_STATUS")" == "0" ]] || die "curl failed (status $(cat "$HTTP_STATUS"))"
[[ -f "$HTTP_HDR" ]] || die "no http.hdr"
grep -q '^HTTP/1.0 200' "$HTTP_HDR" || die "not HTTP 200"
[[ -f "$HTTP_BODY" ]] || die "no http.body"
# 92-byte pin is the on-wire HTTP message (pcap). curl -o is the 9-byte body.
# $(cat) strips a trailing newline, so compare with cmp.
printf 'whimbrel\n' | cmp -s - "$HTTP_BODY" || die "HTTP body is not whimbrel\\n"

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

echo "TEST PASS: linux $WHICH READY, HTTP 200, 92-byte pcap payload, SYN-grid, no RST, QEMU exit 0"
exit 0
