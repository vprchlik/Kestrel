#!/usr/bin/env bash
# SYN-grid gate (D-0062 confound A). Per-pcap; one miss fails the batch.
# Guest first TX is the first frame not sourced from slirp, not ARP
# specifically. SYN into guest is the first SYN to :80 at or after that TX.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PCAP="${1:-}"
if [ -z "$PCAP" ]; then
    echo "TEST FAIL: assert-pcap-syn-grid.sh requires a pcap path" >&2
    exit 1
fi
exec python3 "$ROOT/scripts/pcap_http.py" syn-grid "$PCAP"
