#!/usr/bin/env bash
# Fail-closed T3.10: SYN → SYN/ACK → ACK, our SYN/ACK checksum verified
# good (not unverified), ACK number is their_isn+1, our SND.NXT advanced,
# no RST.
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

# Relative sequence numbers would make every SYN look like seq=0 / ack=1
# and the +1 check would be vacuous. Unverified checksums are the
# silently-discarded-SYN/ACK failure mode; force verification.
TSHARK_OPTS=(
    -o tcp.relative_sequence_numbers:FALSE
    -o tcp.check_checksum:TRUE
)

out=$(mktemp)
err=$(mktemp)
cleanup() { rm -f "$out" "$err"; }
trap cleanup EXIT

run_fields() {
    local filter="$1"
    shift
    set +e
    tshark -r "$PCAP" "${TSHARK_OPTS[@]}" -Y "$filter" -T fields "$@" >"$out" 2>"$err"
    local ts=$?
    set -e
    if [ "$ts" -ne 0 ]; then
        echo "TEST FAIL: tshark could not read ${PCAP} (status=${ts})"
        cat "$err"
        exit 1
    fi
    cat "$out"
}

first_after() {
    local min="$1"
    local n rest
    while read -r n rest; do
        if [ -n "${n:-}" ] && [ "$n" -gt "$min" ]; then
            echo "$n $rest"
            return 0
        fi
    done
    return 1
}

field() {
    awk -v i="$2" '{print $i}' <<<"$1"
}

checksum_is_good() {
    local s="$1"
    s="${s%$'\r'}"
    case "$s" in
        1|Good|good) return 0 ;;
        *) return 1 ;;
    esac
}

SYN_FILTER='tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && tcp.flags.syn == 1 && tcp.flags.ack == 0'
SYNACK_FILTER='tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.flags.syn == 1 && tcp.flags.ack == 1'
ACK_FILTER='tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && tcp.flags.syn == 0 && tcp.flags.ack == 1 && tcp.flags.reset == 0'
RST_FILTER='tcp.flags.reset == 1'

rst=$(run_fields "$RST_FILTER" -e frame.number | awk '/^[0-9]+$/ {print}' || true)
if [ -n "$rst" ]; then
    echo "TEST FAIL: RST present in ${PCAP} (wanted none)"
    echo "$rst"
    exit 1
fi

syn_line=$(run_fields "$SYN_FILTER" -e frame.number -e tcp.seq | awk 'NF>=2 {print $1, $2; exit}')
if [ -z "$syn_line" ]; then
    echo "TEST FAIL: no TCP SYN to 10.0.2.15:80 in ${PCAP}"
    tshark -r "$PCAP" "${TSHARK_OPTS[@]}" -T fields -e frame.number -e ip.src -e ip.dst -e tcp.flags -e tcp.seq \
        2>/dev/null | head -n 20 || echo '(none)'
    exit 1
fi
syn_fn=$(field "$syn_line" 1)
their_isn=$(field "$syn_line" 2)

synack_line=$(run_fields "$SYNACK_FILTER" -e frame.number -e tcp.seq -e tcp.ack -e tcp.checksum.status \
    | awk '{print $1, $2, $3, $4}' | first_after "$syn_fn" || true)
if [ -z "$synack_line" ]; then
    echo "TEST FAIL: no TCP SYN/ACK from 10.0.2.15:80 after SYN frame ${syn_fn} in ${PCAP}"
    exit 1
fi
synack_fn=$(field "$synack_line" 1)
our_isn=$(field "$synack_line" 2)
our_ack=$(field "$synack_line" 3)
csum_status=$(field "$synack_line" 4)

if ! checksum_is_good "$csum_status"; then
    echo "TEST FAIL: SYN/ACK tcp.checksum.status is '${csum_status:-empty}', want good (1)"
    echo "frame ${synack_fn} — unverified/bad TX checksum is a silently discarded segment"
    exit 1
fi

want_ack=$(( (their_isn + 1) % 4294967296 ))
if [ "$our_ack" != "$want_ack" ]; then
    echo "TEST FAIL: SYN/ACK ack=${our_ack} is not their_isn+1 (${their_isn}+1=${want_ack})"
    echo "SYN consumes a sequence number (frame ${syn_fn} seq=${their_isn})"
    exit 1
fi

ack_line=$(run_fields "$ACK_FILTER" -e frame.number -e tcp.seq -e tcp.ack \
    | awk '{print $1, $2, $3}' | first_after "$synack_fn" || true)
if [ -z "$ack_line" ]; then
    echo "TEST FAIL: no completing ACK after SYN/ACK frame ${synack_fn} in ${PCAP}"
    exit 1
fi
ack_fn=$(field "$ack_line" 1)
ack_seq=$(field "$ack_line" 2)
ack_ack=$(field "$ack_line" 3)

want_snd_nxt=$(( (our_isn + 1) % 4294967296 ))
if [ "$ack_ack" != "$want_snd_nxt" ]; then
    echo "TEST FAIL: completing ACK ack=${ack_ack} is not our_isn+1 (${our_isn}+1=${want_snd_nxt})"
    echo "SND.NXT must advance past the ISN (SYN consumes a sequence number)"
    exit 1
fi
want_rcv=$(( (their_isn + 1) % 4294967296 ))
if [ "$ack_seq" != "$want_rcv" ]; then
    echo "TEST FAIL: completing ACK seq=${ack_seq} is not their_isn+1 (${want_rcv})"
    exit 1
fi

echo "TEST PASS: TCP handshake SYN ${syn_fn} → SYN/ACK ${synack_fn} (checksum good) → ACK ${ack_fn} in ${PCAP}"
