#!/usr/bin/env bash
# Fail-closed T3.7 chain: our ICMP echo request to 10.0.2.2, then slirp's
# echo reply. An empty or missing pcap is a fail, not a vacuous pass.
set -euo pipefail

PCAP="${1:-whimbrel.pcap}"
REQ_FILTER='icmp && icmp.type == 8 && ip.src == 10.0.2.15 && ip.dst == 10.0.2.2'
REP_FILTER='icmp && icmp.type == 0 && ip.src == 10.0.2.2 && ip.dst == 10.0.2.15'

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

out=$(mktemp)
err=$(mktemp)
cleanup() { rm -f "$out" "$err"; }
trap cleanup EXIT

run_fields() {
    local filter="$1"
    set +e
    tshark -r "$PCAP" -Y "$filter" -T fields -e frame.number >"$out" 2>"$err"
    local ts=$?
    set -e
    if [ "$ts" -ne 0 ]; then
        echo "TEST FAIL: tshark could not read ${PCAP} (status=${ts})"
        cat "$err"
        exit 1
    fi
    grep -E '^[0-9]+$' "$out" || true
}

first_after() {
    local min="$1"
    local n
    while read -r n; do
        if [ -n "$n" ] && [ "$n" -gt "$min" ]; then
            echo "$n"
            return 0
        fi
    done
    return 1
}

req=$(run_fields "$REQ_FILTER" | head -n1 || true)
if [ -z "$req" ]; then
    echo "TEST FAIL: no ICMP echo request 10.0.2.15→10.0.2.2 in ${PCAP}"
    echo "filter: ${REQ_FILTER}"
    echo "frames in file:"
    tshark -r "$PCAP" -T fields -e frame.number -e ip.src -e ip.dst -e icmp.type \
        2>/dev/null | head -n 20 || echo '(none)'
    exit 1
fi

rep=$(run_fields "$REP_FILTER" | first_after "$req" || true)
if [ -z "$rep" ]; then
    echo "TEST FAIL: no ICMP echo reply after request (frame ${req}) in ${PCAP}"
    echo "filter: ${REP_FILTER}"
    echo "frames in file:"
    tshark -r "$PCAP" -T fields -e frame.number -e ip.src -e ip.dst -e icmp.type \
        2>/dev/null | head -n 20 || echo '(none)'
    exit 1
fi

echo "TEST PASS: ICMP echo request frame ${req} → reply ${rep} in ${PCAP}"
