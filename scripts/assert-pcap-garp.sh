#!/usr/bin/env bash
# Fail-closed gratuitous-ARP check on a filter-dump pcap (D-0043 / T3.4).
# An empty or missing pcap is a fail, not a vacuous pass — same shape as
# check-utext. tshark must be on PATH (SETUP.md / scripts/install.sh).
set -euo pipefail

PCAP="${1:-whimbrel.pcap}"
# Opcode 1 = request; spa == tpa == 10.0.2.15; Ethernet broadcast.
FILTER='arp && arp.opcode == 1 && arp.src.proto_ipv4 == 10.0.2.15 && arp.dst.proto_ipv4 == 10.0.2.15 && eth.dst == ff:ff:ff:ff:ff:ff'

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

set +e
tshark -r "$PCAP" -Y "$FILTER" -T fields -e frame.number >"$out" 2>"$err"
ts=$?
set -e
if [ "$ts" -ne 0 ]; then
    echo "TEST FAIL: tshark could not read ${PCAP} (status=${ts})"
    cat "$err"
    exit 1
fi

n=$(grep -c . "$out" || true)
if [ "$n" -lt 1 ]; then
    echo "TEST FAIL: no gratuitous ARP in ${PCAP}"
    echo "filter: ${FILTER}"
    echo "frames in file:"
    tshark -r "$PCAP" -T fields -e frame.number -e eth.type -e arp.opcode 2>/dev/null \
        | head -n 20 || echo '(none)'
    exit 1
fi
echo "TEST PASS: gratuitous ARP x${n} in ${PCAP}"
