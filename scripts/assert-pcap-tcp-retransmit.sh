#!/usr/bin/env bash
# Fail-closed T3.11 retransmit: exactly two copies of the HTTP data
# segment, same sequence number, ~200 ms apart; the second is ACKed.
set -euo pipefail

PCAP="${1:-whimbrel.pcap}"

if [ ! -f "$PCAP" ]; then
    echo "TEST FAIL: ${PCAP} missing"
    exit 1
fi
if [ ! -s "$PCAP" ]; then
    echo "TEST FAIL: ${PCAP} empty"
    exit 1
fi
if ! command -v tshark >/dev/null 2>&1; then
    echo 'TEST FAIL: tshark not installed (see docs/SETUP.md)'
    exit 1
fi

TSHARK_OPTS=(
    -o tcp.relative_sequence_numbers:FALSE
    -o tcp.check_checksum:TRUE
)

out=$(mktemp)
err=$(mktemp)
cleanup() { rm -f "$out" "$err"; }
trap cleanup EXIT

set +e
tshark -r "$PCAP" "${TSHARK_OPTS[@]}" \
    -Y 'ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.len > 0 && tcp.flags.syn == 0' \
    -T fields -e frame.number -e tcp.seq -e tcp.nxtseq -e frame.time_relative -e tcp.checksum.status \
    >"$out" 2>"$err"
ts=$?
set -e
if [ "$ts" -ne 0 ]; then
    echo "TEST FAIL: tshark could not read ${PCAP} (status=${ts})"
    cat "$err"
    exit 1
fi

n=$(awk 'NF>=4 {c++} END {print c+0}' "$out")
if [ "$n" -ne 2 ]; then
    echo "TEST FAIL: want exactly 2 HTTP data segments, got ${n}"
    cat "$out"
    exit 1
fi

mapfile -t lines < <(awk 'NF>=4 {print $1, $2, $3, $4, $5}' "$out")
read -r f1 seq1 nxt1 t1 c1 <<<"${lines[0]}"
read -r f2 seq2 nxt2 t2 c2 <<<"${lines[1]}"

if [ "$seq1" != "$seq2" ]; then
    echo "TEST FAIL: retransmit seq mismatch ${seq1} vs ${seq2}"
    exit 1
fi

case "$c1" in 1|Good|good) ;; *) echo "TEST FAIL: first copy checksum.status '${c1}'"; exit 1 ;; esac
case "$c2" in 1|Good|good) ;; *) echo "TEST FAIL: second copy checksum.status '${c2}'"; exit 1 ;; esac

dt=$(awk -v a="$t1" -v b="$t2" 'BEGIN { d=b-a; printf "%.6f", d }')
if ! awk -v d="$dt" 'BEGIN { exit (d>=0.15 && d<=0.40) ? 0 : 1 }'; then
    echo "TEST FAIL: retransmit delta ${dt}s is not ~200ms (want 0.15–0.40)"
    exit 1
fi

set +e
tshark -r "$PCAP" "${TSHARK_OPTS[@]}" \
    -Y "tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && tcp.flags.ack == 1 && tcp.ack == ${nxt2} && frame.number > ${f2}" \
    -T fields -e frame.number >"$out" 2>"$err"
ts=$?
set -e
if [ "$ts" -ne 0 ]; then
    echo "TEST FAIL: tshark ACK query failed"
    cat "$err"
    exit 1
fi
ack_fn=$(awk '/^[0-9]+$/ {print; exit}' "$out" || true)
if [ -z "$ack_fn" ]; then
    echo "TEST FAIL: no ACK of nxtseq=${nxt2} after second copy (frame ${f2})"
    exit 1
fi

echo "TEST PASS: two copies seq=${seq1} frames ${f1}→${f2} delta=${dt}s; ACK frame ${ack_fn}"
