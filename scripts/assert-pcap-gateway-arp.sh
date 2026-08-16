#!/usr/bin/env bash
# Fail-closed T3.12: our ARP request for 10.0.2.2, then slirp's reply,
# then IPv4. Distinct from GARP (spa == tpa == 10.0.2.15) and from the
# T3.5 "slirp ARPs us first" chain (D-0054). An empty or missing pcap
# is a fail, not a vacuous pass.
set -euo pipefail

PCAP="${1:-whimbrel.pcap}"
REQ_FILTER='arp && arp.opcode == 1 && arp.src.proto_ipv4 == 10.0.2.15 && arp.dst.proto_ipv4 == 10.0.2.2'
REP_FILTER='arp && arp.opcode == 2 && arp.src.proto_ipv4 == 10.0.2.2 && arp.dst.proto_ipv4 == 10.0.2.15'
IP_FILTER='ip'

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
    echo "TEST FAIL: no ARP request for 10.0.2.2 from 10.0.2.15 in ${PCAP}"
    echo "filter: ${REQ_FILTER}"
    echo "frames in file:"
    tshark -r "$PCAP" -T fields -e frame.number -e eth.type -e arp.opcode \
        -e arp.src.proto_ipv4 -e arp.dst.proto_ipv4 2>/dev/null \
        | head -n 20 || echo '(none)'
    exit 1
fi

rep=$(run_fields "$REP_FILTER" | first_after "$req" || true)
if [ -z "$rep" ]; then
    echo "TEST FAIL: no ARP reply from 10.0.2.2 after request (frame ${req}) in ${PCAP}"
    echo "filter: ${REP_FILTER}"
    echo "frames in file:"
    tshark -r "$PCAP" -T fields -e frame.number -e eth.type -e arp.opcode \
        -e arp.src.proto_ipv4 -e arp.dst.proto_ipv4 2>/dev/null \
        | head -n 20 || echo '(none)'
    exit 1
fi

ip=$(run_fields "$IP_FILTER" | first_after "$rep" || true)
if [ -z "$ip" ]; then
    echo "TEST FAIL: no IPv4 after gateway ARP reply (frame ${rep}) in ${PCAP}"
    echo "filter: ${IP_FILTER}"
    echo "frames in file:"
    tshark -r "$PCAP" -T fields -e frame.number -e eth.type -e ip.src -e ip.dst \
        2>/dev/null | head -n 20 || echo '(none)'
    exit 1
fi

echo "TEST PASS: gateway ARP request frame ${req} → reply ${rep} → IPv4 ${ip} in ${PCAP}"
