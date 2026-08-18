#!/usr/bin/env bash
# Fail-closed T3.11: HTTP data + FIN close. Checksums verified good on
# the response (and FIN, which may share that segment). No RST.
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

rst=$(run_fields 'tcp.flags.reset == 1' -e frame.number | awk '/^[0-9]+$/ {print}' || true)
if [ -n "$rst" ]; then
    echo "TEST FAIL: RST present in ${PCAP} (wanted none on the happy path)"
    echo "$rst"
    exit 1
fi

syn_line=$(run_fields 'tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && tcp.flags.syn == 1 && tcp.flags.ack == 0' \
    -e frame.number -e tcp.seq | awk 'NF>=2 {print $1, $2; exit}')
if [ -z "$syn_line" ]; then
    echo "TEST FAIL: no TCP SYN to 10.0.2.15:80 in ${PCAP}"
    exit 1
fi
syn_fn=$(field "$syn_line" 1)

synack_line=$(run_fields 'tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.flags.syn == 1 && tcp.flags.ack == 1' \
    -e frame.number | awk '{print $1}' | first_after "$syn_fn" || true)
if [ -z "$synack_line" ]; then
    echo "TEST FAIL: no TCP SYN/ACK after SYN frame ${syn_fn}"
    exit 1
fi
synack_fn=$(field "$synack_line" 1)

data_line=$(run_fields 'tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.len > 0 && tcp.flags.syn == 0' \
    -e frame.number -e tcp.seq -e tcp.len -e tcp.nxtseq -e tcp.flags.fin -e tcp.checksum.status \
    | awk '{print $1, $2, $3, $4, $5, $6}' | first_after "$synack_fn" || true)
if [ -z "$data_line" ]; then
    echo "TEST FAIL: no TCP data from 10.0.2.15:80 after SYN/ACK in ${PCAP}"
    exit 1
fi
data_fn=$(field "$data_line" 1)
data_seq=$(field "$data_line" 2)
data_len=$(field "$data_line" 3)
data_nxt=$(field "$data_line" 4)
data_fin=$(field "$data_line" 5)
csum=$(field "$data_line" 6)

if ! checksum_is_good "$csum"; then
    echo "TEST FAIL: HTTP data tcp.checksum.status is '${csum:-empty}', want good (1)"
    echo "frame ${data_fn}"
    exit 1
fi

http_hit=$(run_fields "frame.number == ${data_fn} && frame contains \"HTTP/1.0 200 OK\"" -e frame.number | awk '{print $1}' || true)
if [ -z "$http_hit" ]; then
    echo "TEST FAIL: data frame ${data_fn} does not contain HTTP/1.0 200 OK"
    exit 1
fi
close_hit=$(run_fields "frame.number == ${data_fn} && frame contains \"Connection: close\"" -e frame.number | awk '{print $1}' || true)
if [ -z "$close_hit" ]; then
    echo "TEST FAIL: data frame ${data_fn} does not contain Connection: close"
    exit 1
fi
if [ "$data_len" != "92" ]; then
    echo "TEST FAIL: HTTP tcp.len is ${data_len}, want 92"
    echo "frame ${data_fn}"
    exit 1
fi

fin_line=$(run_fields 'tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.flags.fin == 1' \
    -e frame.number -e tcp.seq -e tcp.checksum.status | awk '{print $1, $2, $3}' | first_after "$((data_fn - 1))" || true)
if [ -z "$fin_line" ]; then
    echo "TEST FAIL: no FIN from 10.0.2.15:80 on or after HTTP data frame ${data_fn}"
    exit 1
fi
fin_fn=$(field "$fin_line" 1)
fin_csum=$(field "$fin_line" 3)
if ! checksum_is_good "$fin_csum"; then
    echo "TEST FAIL: our FIN tcp.checksum.status is '${fin_csum:-empty}', want good (1)"
    echo "frame ${fin_fn}"
    exit 1
fi

# FIN consumes 1: nxtseq on a data+FIN segment is seq+len+1.
if [ "$data_fin" = "1" ] || [ "$data_fin" = "True" ] || [ "$data_fn" = "$fin_fn" ]; then
    want_ack=$data_nxt
else
    want_ack=$(( (data_seq + data_len + 1) % 4294967296 ))
fi

peer_fin=$(run_fields 'tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && tcp.flags.fin == 1' \
    -e frame.number | awk '{print $1}' | first_after "$data_fn" || true)
if [ -z "$peer_fin" ]; then
    echo "TEST FAIL: no peer FIN after our response in ${PCAP}"
    exit 1
fi

echo "TEST PASS: HTTP data frame ${data_fn} (checksum good, Connection: close) FIN ${fin_fn} (checksum good) peer FIN $(field "$peer_fin" 1) nxt=${want_ack} in ${PCAP}"
