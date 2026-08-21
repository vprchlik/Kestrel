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
expect_fail scripts/assert-pcap-gateway-arp.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-icmp.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-http.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/no-such.pcap" missing
expect_fail scripts/assert-pcap-syn-grid.sh "$tmp/no-such.pcap" missing

: >"$tmp/empty.pcap"
expect_fail scripts/assert-pcap-garp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-gateway-arp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-icmp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-http.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-syn-grid.sh "$tmp/empty.pcap" empty

python3 - "$tmp" <<'PY'
import struct, sys

def write_pcap(path, frames):
    hdr = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    body = b""
    for item in frames:
        if isinstance(item, tuple):
            f, usec = item
        else:
            f, usec = item, 0
        body += struct.pack(
            "<IIII", usec // 1_000_000, usec % 1_000_000, len(f), len(f)
        ) + f
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

our_gw_req = pad60(
    bcast
    + guest_mac
    + bytes.fromhex("0806")
    + bytes.fromhex("0001080006040001")
    + guest_mac
    + bytes.fromhex("0a00020f")
    + bytes(6)
    + bytes.fromhex("0a000202")
)
gw_reply = pad60(
    guest_mac
    + slirp_mac
    + bytes.fromhex("0806")
    + bytes.fromhex("0001080006040002")
    + slirp_mac
    + bytes.fromhex("0a000202")
    + guest_mac
    + bytes.fromhex("0a00020f")
)
write_pcap(d + "/gw-req-only.pcap", [our_gw_req])
write_pcap(d + "/gw-happy.pcap", [our_gw_req, gw_reply, ipv4_syn])
write_pcap(d + "/gw-reply-then-req.pcap", [gw_reply, our_gw_req, ipv4_syn])

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

def tcp_hdr(sport, dport, seq, ack, flags, src, dst, payload=b""):
    h = bytearray(20)
    h[0:2] = sport.to_bytes(2, "big")
    h[2:4] = dport.to_bytes(2, "big")
    h[4:8] = seq.to_bytes(4, "big")
    h[8:12] = ack.to_bytes(4, "big")
    h[12] = 5 << 4
    h[13] = flags
    h[14:16] = (8192).to_bytes(2, "big")
    tot = 20 + len(payload)
    pseudo = src + dst + bytes([0, 6]) + tot.to_bytes(2, "big")
    c = inet_checksum(pseudo + bytes(h) + payload)
    h[16:18] = c.to_bytes(2, "big")
    return bytes(h) + payload

def tcp_frame(eth_dst, eth_src, ip_src, ip_dst, tcp):
    ip = ipv4_hdr(20 + len(tcp), 6, ip_src, ip_dst)
    frame = eth_dst + eth_src + bytes.fromhex("0800") + ip + tcp
    if len(frame) < 60:
        frame += bytes(60 - len(frame))
    return frame

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

PSH, FIN = 0x08, 0x01
http_body = (
    b"HTTP/1.0 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Connection: close\r\n"
    b"Content-Length: 9\r\n"
    b"\r\n"
    b"whimbrel\n"
)
assert len(http_body) == 92
http_keep = http_body.replace(b"Connection: close", b"Connection: keep-alive")
http_404 = http_body.replace(b"HTTP/1.0 200 OK", b"HTTP/1.0 404 NO")
http_data = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2001, 1001, ACK | PSH | FIN, ip_guest, ip_gw, http_body),
)
http_keep_f = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2001, 1001, ACK | PSH | FIN, ip_guest, ip_gw, http_keep),
)
http_404_f = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2001, 1001, ACK | PSH | FIN, ip_guest, ip_gw, http_404),
)
http_nofin = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2001, 1001, ACK | PSH, ip_guest, ip_gw, http_body),
)
bad_http = bytearray(http_data)
bad_http[14 + 20 + 16] ^= 0xFF
http_bad_csum = bytes(bad_http)
peer_fin = tcp_frame(
    guest_mac, slirp_mac, ip_gw, ip_guest,
    tcp_hdr(12345, 80, 1001, 2094, ACK | FIN, ip_gw, ip_guest),
)
peer_ack_data = tcp_frame(
    guest_mac, slirp_mac, ip_gw, ip_guest,
    tcp_hdr(12345, 80, 1001, 2094, ACK, ip_gw, ip_guest),
)
http_data2 = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2002, 1001, ACK | PSH | FIN, ip_guest, ip_gw, http_body),
)
handshake = [tcp_syn, tcp_synack, tcp_ack]
write_pcap(d + "/http-happy.pcap", handshake + [http_data, peer_fin])
write_pcap(d + "/http-no-close.pcap", handshake + [http_keep_f, peer_fin])
write_pcap(d + "/http-no-200.pcap", handshake + [http_404_f, peer_fin])
write_pcap(d + "/http-no-fin.pcap", handshake + [http_nofin, peer_fin])
write_pcap(d + "/http-no-peer-fin.pcap", handshake + [http_data])
write_pcap(d + "/http-bad-csum.pcap", handshake + [http_bad_csum, peer_fin])
write_pcap(d + "/http-rst.pcap", handshake + [http_data, peer_fin, tcp_rst])
http_short_body = (
    b"HTTP/1.0 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Connection: close\r\n"
    b"Content-Length: 1\r\n"
    b"\r\n"
    b"x"
)
http_short = tcp_frame(
    slirp_mac, guest_mac, ip_guest, ip_gw,
    tcp_hdr(80, 12345, 2001, 1001, ACK | PSH | FIN, ip_guest, ip_gw, http_short_body),
)
write_pcap(d + "/http-short.pcap", handshake + [http_short, peer_fin])
write_pcap(d + "/syn-grid-happy.pcap", [(garp, 0), (tcp_syn, 500)])
write_pcap(d + "/syn-grid-rto.pcap", [(garp, 0), (tcp_syn, 1_000_000)])
write_pcap(d + "/syn-grid-no-tx.pcap", [tcp_syn])
write_pcap(d + "/syn-grid-no-syn.pcap", [garp])
write_pcap(
    d + "/rto-happy.pcap",
    [
        (tcp_syn, 0),
        (tcp_synack, 1000),
        (tcp_ack, 2000),
        (http_data, 1_000_000),
        (http_data, 1_200_000),
        (peer_ack_data, 1_250_000),
    ],
)
write_pcap(
    d + "/rto-one-copy.pcap",
    [(tcp_syn, 0), (http_data, 1_000_000)],
)
write_pcap(
    d + "/rto-diff-seq.pcap",
    [
        (http_data, 1_000_000),
        (http_data2, 1_200_000),
        (peer_ack_data, 1_250_000),
    ],
)
write_pcap(
    d + "/rto-too-close.pcap",
    [
        (http_data, 1_000_000),
        (http_data, 1_010_000),
        (peer_ack_data, 1_020_000),
    ],
)
write_pcap(
    d + "/rto-too-far.pcap",
    [
        (http_data, 1_000_000),
        (http_data, 2_000_000),
        (peer_ack_data, 2_050_000),
    ],
)
write_pcap(
    d + "/rto-no-ack.pcap",
    [
        (http_data, 1_000_000),
        (http_data, 1_200_000),
    ],
)
PY

expect_fail scripts/assert-pcap-garp.sh "$tmp/hdr-only.pcap" "no gratuitous ARP"
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/hdr-only.pcap" "no slirp ARP"
expect_fail scripts/assert-pcap-gateway-arp.sh "$tmp/hdr-only.pcap" "no ARP request for 10.0.2.2"
expect_fail scripts/assert-pcap-arp-reply.sh "$tmp/hdr-only.pcap" "no slirp ARP request"
expect_fail scripts/assert-pcap-icmp.sh "$tmp/hdr-only.pcap" "no ICMP echo request"
expect_fail scripts/assert-pcap-udp-echo.sh "$tmp/hdr-only.pcap" "no UDP echo request"
expect_fail scripts/assert-pcap-tcp-handshake.sh "$tmp/hdr-only.pcap" "no TCP SYN"
expect_fail scripts/assert-pcap-http.sh "$tmp/hdr-only.pcap" "no TCP SYN"
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/hdr-only.pcap" "want exactly 2 HTTP data segments"

# Our GARP must not satisfy the slirp filter (spa == tpa == 10.0.2.15).
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/garp-only.pcap" "no slirp ARP"
expect_fail scripts/assert-pcap-gateway-arp.sh "$tmp/garp-only.pcap" "no ARP request for 10.0.2.2"
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

expect_fail scripts/assert-pcap-gateway-arp.sh "$tmp/req-only.pcap" "no ARP request for 10.0.2.2"
expect_fail scripts/assert-pcap-gateway-arp.sh "$tmp/gw-req-only.pcap" "no ARP reply from 10.0.2.2"
expect_fail scripts/assert-pcap-gateway-arp.sh "$tmp/gw-reply-then-req.pcap" "no ARP reply"
bash scripts/assert-pcap-gateway-arp.sh "$tmp/gw-happy.pcap"

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

expect_fail scripts/assert-pcap-http.sh "$tmp/tcp-happy.pcap" "no TCP data"
expect_fail scripts/assert-pcap-http.sh "$tmp/http-no-200.pcap" "HTTP/1.0 200 OK"
expect_fail scripts/assert-pcap-http.sh "$tmp/http-no-close.pcap" "Connection: close"
expect_fail scripts/assert-pcap-http.sh "$tmp/http-no-fin.pcap" "no FIN from 10.0.2.15:80"
expect_fail scripts/assert-pcap-http.sh "$tmp/http-no-peer-fin.pcap" "no peer FIN"
expect_fail scripts/assert-pcap-http.sh "$tmp/http-bad-csum.pcap" "checksum.status"
expect_fail scripts/assert-pcap-http.sh "$tmp/http-rst.pcap" "RST present"
expect_fail scripts/assert-pcap-http.sh "$tmp/http-short.pcap" "tcp.len"
bash scripts/assert-pcap-http.sh "$tmp/http-happy.pcap"

expect_fail scripts/assert-pcap-syn-grid.sh "$tmp/hdr-only.pcap" "no guest first TX"
expect_fail scripts/assert-pcap-syn-grid.sh "$tmp/syn-grid-no-tx.pcap" "no guest first TX"
expect_fail scripts/assert-pcap-syn-grid.sh "$tmp/syn-grid-no-syn.pcap" "no TCP SYN"
expect_fail scripts/assert-pcap-syn-grid.sh "$tmp/syn-grid-rto.pcap" "slirp's RTO"
bash scripts/assert-pcap-syn-grid.sh "$tmp/syn-grid-happy.pcap"

expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/http-happy.pcap" "want exactly 2 HTTP data segments"
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/rto-one-copy.pcap" "want exactly 2 HTTP data segments"
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/rto-diff-seq.pcap" "seq mismatch"
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/rto-too-close.pcap" "not ~200ms"
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/rto-too-far.pcap" "not ~200ms"
expect_fail scripts/assert-pcap-tcp-retransmit.sh "$tmp/rto-no-ack.pcap" "no ACK of nxtseq"
bash scripts/assert-pcap-tcp-retransmit.sh "$tmp/rto-happy.pcap"

echo "TEST PASS: pcap assert failure modes"
