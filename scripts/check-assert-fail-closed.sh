#!/usr/bin/env bash
# Exercise pcap-assert failure modes before the live capture is trusted
# (DEBUGGING.md M3 item 7). A happy-path-only assert is a vacuous pass.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail() { echo "TEST FAIL: $1"; exit 1; }

expect_fail() {
    local script="$1"
    local pcap="$2"
    local needle="$3"
    local out
    set +e
    out=$(bash "$script" "$pcap" 2>&1)
    local st=$?
    set -e
    if [ "$st" -eq 0 ]; then
        fail "$script $pcap passed; wanted fail ($needle)"
    fi
    echo "$out" | grep -q "$needle" || fail "$script $pcap status=$st, missing '$needle': $out"
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

expect_fail scripts/assert-pcap-garp.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-icmp.sh "$tmp/no-such.pcap" missing

: >"$tmp/empty.pcap"
expect_fail scripts/assert-pcap-garp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-icmp.sh "$tmp/empty.pcap" empty

python3 - "$tmp" <<'PY'
import struct, sys

def write_pcap(path, frames):
    hdr = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    body = b""
    for f in frames:
        body += struct.pack("<IIII", 0, 0, len(f), len(f)) + f
    open(path, "wb").write(hdr + body)

def pad60(frame):
    if len(frame) > 60:
        raise SystemExit(f"frame {len(frame)} > 60")
    return frame + bytes(60 - len(frame))

slirp_mac = bytes.fromhex("52550a000202")
guest_mac = bytes.fromhex("525400123456")
bcast = bytes.fromhex("ffffffffffff")

garp = pad60(
    bcast
    + guest_mac
    + bytes.fromhex("0806")
    + bytes.fromhex("0001080006040001")
    + guest_mac
    + bytes.fromhex("0a00020f")
    + bytes(6)
    + bytes.fromhex("0a00020f")
)

slirp_req = pad60(
    bcast
    + slirp_mac
    + bytes.fromhex("0806")
    + bytes.fromhex("0001080006040001")
    + slirp_mac
    + bytes.fromhex("0a000202")
    + bytes(6)
    + bytes.fromhex("0a00020f")
)

our_reply = pad60(
    slirp_mac
    + guest_mac
    + bytes.fromhex("0806")
    + bytes.fromhex("0001080006040002")
    + guest_mac
    + bytes.fromhex("0a00020f")
    + slirp_mac
    + bytes.fromhex("0a000202")
)

# Minimal IPv4/TCP SYN 10.0.2.2 -> 10.0.2.15. Checksums are zero;
# tshark still classifies ip.src / ip.dst.
ipv4_syn = pad60(
    guest_mac
    + slirp_mac
    + bytes.fromhex("0800")
    + bytes.fromhex("4500002800004000400600000a0002020a00020f")
    + bytes.fromhex("0000005000000000000000005002000000000000")
)

# ICMP echo request 10.0.2.15 -> 10.0.2.2 (type 8) and reply (type 0).
# 14+20+16 = 50, pad to 60. Checksums zero; tshark classifies icmp.type.
icmp_req = pad60(
    slirp_mac
    + guest_mac
    + bytes.fromhex("0800")
    + bytes.fromhex("4500002400004000400100000a00020f0a000202")
    + bytes.fromhex("08000000000100017768696d6272656c")
)
icmp_rep = pad60(
    guest_mac
    + slirp_mac
    + bytes.fromhex("0800")
    + bytes.fromhex("4500002400004000400100000a0002020a00020f")
    + bytes.fromhex("00000000000100017768696d6272656c")
)

d = sys.argv[1]
write_pcap(d + "/hdr-only.pcap", [])
write_pcap(d + "/garp-only.pcap", [garp])
write_pcap(d + "/req-only.pcap", [slirp_req])
write_pcap(d + "/req-reply.pcap", [slirp_req, our_reply])
write_pcap(d + "/happy.pcap", [slirp_req, our_reply, ipv4_syn])
write_pcap(d + "/reply-then-req.pcap", [our_reply, slirp_req, ipv4_syn])
write_pcap(d + "/icmp-req-only.pcap", [icmp_req])
write_pcap(d + "/icmp-happy.pcap", [icmp_req, icmp_rep])
write_pcap(d + "/icmp-reply-then-req.pcap", [icmp_rep, icmp_req])
PY

expect_fail scripts/assert-pcap-garp.sh "$tmp/hdr-only.pcap" "no gratuitous ARP"
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/hdr-only.pcap" "no slirp ARP"
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/hdr-only.pcap" "no slirp ARP request"
expect_fail scripts/assert-pcap-icmp.sh "$tmp/hdr-only.pcap" "no ICMP echo request"

# Our GARP must not satisfy the slirp filter (spa == tpa == 10.0.2.15).
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/garp-only.pcap" "no slirp ARP"
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/garp-only.pcap" "no slirp ARP request"
expect_fail scripts/assert-pcap-icmp.sh "$tmp/garp-only.pcap" "no ICMP echo request"
# The GARP assert must accept that same file — otherwise the filter is
# vacuously matching anything, or matching nothing and we'd have failed above.
bash scripts/assert-pcap-garp.sh "$tmp/garp-only.pcap"

expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/req-only.pcap" "no ARP reply"
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/req-reply.pcap" "no IPv4 after ARP reply"
# Reply is frame 1, request is frame 2: no reply *after* the request.
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/reply-then-req.pcap" "no ARP reply"

bash scripts/assert-pcap-slirp-arp.sh "$tmp/happy.pcap"
bash scripts/assert-pcap-arp-reply.sh "$tmp/happy.pcap"

expect_fail scripts/assert-pcap-icmp.sh "$tmp/happy.pcap" "no ICMP echo request"
expect_fail scripts/assert-pcap-icmp.sh "$tmp/icmp-req-only.pcap" "no ICMP echo reply"
expect_fail scripts/assert-pcap-icmp.sh "$tmp/icmp-reply-then-req.pcap" "no ICMP echo reply"
bash scripts/assert-pcap-icmp.sh "$tmp/icmp-happy.pcap"

echo "TEST PASS: pcap assert failure modes"
