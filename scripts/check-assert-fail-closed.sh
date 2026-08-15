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

: >"$tmp/empty.pcap"
expect_fail scripts/assert-pcap-garp.sh "$tmp/empty.pcap" empty
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/empty.pcap" empty

python3 - "$tmp/hdr-only.pcap" "$tmp/garp-only.pcap" <<'PY'
import struct, sys

def write_pcap(path, frames):
    hdr = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    body = b""
    for f in frames:
        body += struct.pack("<IIII", 0, 0, len(f), len(f)) + f
    open(path, "wb").write(hdr + body)

write_pcap(sys.argv[1], [])

garp = bytes.fromhex(
    "ffffffffffff5254001234560806"
    "0001080006040001"
    "5254001234560a00020f"
    "0000000000000a00020f"
) + bytes(18)
assert len(garp) == 60
write_pcap(sys.argv[2], [garp])
PY

expect_fail scripts/assert-pcap-garp.sh "$tmp/hdr-only.pcap" "no gratuitous ARP"
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/hdr-only.pcap" "no slirp ARP"
# Our GARP must not satisfy the slirp filter (spa == tpa == 10.0.2.15).
expect_fail scripts/assert-pcap-slirp-arp.sh "$tmp/garp-only.pcap" "no slirp ARP"
# The GARP assert must accept that same file — otherwise the filter is
# vacuously matching anything, or matching nothing and we'd have failed above.
bash scripts/assert-pcap-garp.sh "$tmp/garp-only.pcap"

echo "TEST PASS: pcap assert failure modes"
