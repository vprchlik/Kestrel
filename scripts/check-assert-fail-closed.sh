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
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/no-such.pcap" missing

: >"$tmp/empty.pcap"
expect_fail scripts/assert-pcap-garp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-icmp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/empty.pcap" empty

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

payload = b"whimbrel-udp-echo"
# 14 + 20 + 8 + 17 = 59, pad to 60. UDP length 25, IP tot 45.
udp_req = pad60(
    guest_mac
    + slirp_mac
    + bytes.fromhex("0800")
    + bytes.fromhex("4500002d00004000401100000a0002020a00020f")
    + bytes.fromhex("3039000700190000")
    + payload
)
udp_rep = pad60(
    slirp_mac
    + guest_mac
    + bytes.fromhex("0800")
    + bytes.fromhex("4500002d00004000401100000a00020f0a000202")
    + bytes.fromhex("0007303900190000")
    + payload
)
write_pcap(d + "/udp-req-only.pcap", [udp_req])
write_pcap(d + "/udp-happy.pcap", [udp_req, udp_rep])
write_pcap(d + "/udp-reply-then-req.pcap", [udp_rep, udp_req])

def inet_checksum(data):
    if len(data) % 2:
        data = data + b"\x00"
    s = 0
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF

def ipv4_hdr(total, proto, src, dst):
    h = bytearray(20)
    h[0] = 0x45
    h[2:4] = total.to_bytes(2, "big")
    h[6:8] = (0x4000).to_bytes(2, "big")
    h[8] = 64
    h[9] = proto
    h[12:16] = src
    h[16:20] = dst
    c = inet_checksum(bytes(h))
    h[10:12] = c.to_bytes(2, "big")
    return bytes(h)

def tcp_hdr(sport, dport, seq, ack, flags, src, dst):
    h = bytearray(20)
    h[0:2] = sport.to_bytes(2, "big")
    h[2:4] = dport.to_bytes(2, "big")
    h[4:8] = seq.to_bytes(4, "big")
    h[8:12] = ack.to_bytes(4, "big")
    h[12] = 5 << 4
    h[13] = flags
    h[14:16] = (8192).to_bytes(2, "big")
    pseudo = src + dst + bytes([0, 6]) + (20).to_bytes(2, "big")
    c = inet_checksum(pseudo + bytes(h))
    h[16:18] = c.to_bytes(2, "big")
    return bytes(h)

def tcp_frame(eth_dst, eth_src, ip_src, ip_dst, tcp):
    ip = ipv4_hdr(20 + len(tcp), 6, ip_src, ip_dst)
    return pad60(eth_dst + eth_src + bytes.fromhex("0800") + ip + tcp)

ip_guest = bytes.fromhex("0a00020f")
ip_gw = bytes.fromhex("0a000202")
SYN, ACK, RST = 0x02, 0x10, 0x04
tcp_syn = tcp_frame(
    guest_mac, slirp_mac, ip_gw, ip_guest,
    tcp_hdr(12345, 80, 1000, 0, SYN, ip_gw, ip_guest),
)
tcp_synack = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2000, 1001, SYN | ACK, ip_guest, ip_gw),
)
tcp_ack = tcp_frame(
    guest_mac, slirp_mac, ip_gw, ip_guest,
    tcp_hdr(12345, 80, 1001, 2001, ACK, ip_gw, ip_guest),
)
bad = bytearray(tcp_synack)
# Flip a TCP checksum byte (offset 14+20+16).
bad[14 + 20 + 16] ^= 0xFF
tcp_synack_bad = bytes(bad)
tcp_synack_wrong_ack = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2000, 1000, SYN | ACK, ip_guest, ip_gw),
)
tcp_rst = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2000, 1001, RST, ip_guest, ip_gw),
)

write_pcap(d + "/tcp-syn-only.pcap", [tcp_syn])
write_pcap(d + "/tcp-syn-synack.pcap", [tcp_syn, tcp_synack])
write_pcap(d + "/tcp-happy.pcap", [tcp_syn, tcp_synack, tcp_ack])
write_pcap(d + "/tcp-ack-then-synack.pcap", [tcp_syn, tcp_ack, tcp_synack])
write_pcap(d + "/tcp-bad-csum.pcap", [tcp_syn, tcp_synack_bad, tcp_ack])
write_pcap(d + "/tcp-wrong-ack.pcap", [tcp_syn, tcp_synack_wrong_ack, tcp_ack])
write_pcap(d + "/tcp-rst.pcap", [tcp_syn, tcp_synack, tcp_ack, tcp_rst])
PY

expect_fail scripts/assert-pcap-garp.sh "$tmp/hdr-only.pcap" "no gratuitous ARP"
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/hdr-only.pcap" "no slirp ARP"
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/hdr-only.pcap" "no slirp ARP request"
expect_fail scripts/assert-pcap-icmp.sh "$tmp/hdr-only.pcap" "no ICMP echo request"
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/hdr-only.pcap" "no UDP echo request"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/hdr-only.pcap" "no TCP SYN"

# Our GARP must not satisfy the slirp filter (spa == tpa == 10.0.2.15).
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/garp-only.pcap" "no slirp ARP"
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/garp-only.pcap" "no slirp ARP request"
expect_fail scripts/assert-pcap-icmp.sh "$tmp/garp-only.pcap" "no ICMP echo request"
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/garp-only.pcap" "no UDP echo request"
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

expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/happy.pcap" "no UDP echo request"
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/udp-req-only.pcap" "no UDP echo reply"
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/udp-reply-then-req.pcap" "no UDP echo reply"
bash scripts/assert-pcap-udp-echo.sh "$tmp/udp-happy.pcap"

expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/happy.pcap" "no TCP SYN/ACK"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/tcp-syn-only.pcap" "no TCP SYN/ACK"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/tcp-syn-synack.pcap" "no completing ACK"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/tcp-ack-then-synack.pcap" "no completing ACK"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/tcp-bad-csum.pcap" "checksum.status"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/tcp-wrong-ack.pcap" "their_isn+1"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/tcp-rst.pcap" "RST present"
bash scripts/assert-pcap-tcp-handshake.sh "$tmp/tcp-happy.pcap"

echo "TEST PASS: pcap assert failure modes"
