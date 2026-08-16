#!/usr/bin/env bash
# Fail-closed check: slirp's ARP request for 10.0.2.15 is in the pcap.
# Distinct from our GARP (spa == tpa == 10.0.2.15). An empty or missing
# pcap is a fail, not a vacuous pass.
set -euo pipefail

PCAP="${1:-whimbrel.pcap}"
# Opcode 1 = request; spa = gateway 10.0.2.2; tpa = guest 10.0.2.15.
FILTER='arp && arp.opcode == 1 && arp.src.proto_ipv4 == 10.0.2.2 && arp.dst.proto_ipv4 == 10.0.2.15'

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
    echo "TEST FAIL: no slirp ARP request for 10.0.2.15 in ${PCAP}"
    echo "filter: ${FILTER}"
    echo "frames in file:"
    tshark -r "$PCAP" -T fields -e frame.number -e eth.type -e arp.src.proto_ipv4 -e arp.dst.proto_ipv4 2>/dev/null \
        | head -n 20 || echo '(none)'
    exit 1
fi
echo "TEST PASS: slirp ARP request x${n} in ${PCAP}"
