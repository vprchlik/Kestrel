# Task runner for Whimbrel. `just --list` shows all recipes.

set shell := ["bash", "-uc"]

target    := "riscv64gc-unknown-none-elf"
kernel    := "target/" + target + "/debug/whimbrel"
qemu      := "qemu-system-riscv64"
# D-0038 / D-0039 / D-0042 / D-0043 / D-0055: argv lives in
# scripts/qemu-args.sh so the bench harness is not a fifth copy
# (audit finding 28).
qemu_args := `bash scripts/qemu-args.sh`

# D-0057 / finding 26: one name list for the three HTTP phase greps.
# The harness parses PHASE lines from serial and is not a fourth copy.
phase_names := "_start stamp_a stamp_b stvec frame_init task_init page_build page_verify activate virtq_init DRIVER_OK first_rx serving_ready net_init_done heap_init accounting freeze sret syn_rx established E3g E3g_doorbell"

# Cross-compile the debug kernel for riscv64gc-unknown-none-elf.
# Frame pointers: scripts/cargo-debug.sh (finding 14).
build:
    bash scripts/cargo-debug.sh build

# Boot the default kernel in QEMU (extra flags as one quoted arg).
run qemu_extra="": build
    {{qemu}} {{qemu_args}} {{qemu_extra}} -kernel {{kernel}}

# Boot the persist HTTP image and sit on :8080 until QEMU is killed.
run-http:
    bash scripts/cargo-debug.sh build --features http-persist
    {{qemu}} {{qemu_args}} -kernel {{kernel}}

# Boot a kmain panic image (live serial; prefer test-panic for the verdict).
panic timeout_s="5":
    bash scripts/cargo-debug.sh build --features panic-selftest
    timeout --foreground {{timeout_s}} {{qemu}} {{qemu_args}} -kernel {{kernel}}

# Boot frozen at reset with the GDB stub on tcp::1234.
debug: build
    {{qemu}} {{qemu_args}} -s -S -kernel {{kernel}}

# Attach gdb-multiarch to a running just debug QEMU.
gdb:
    gdb-multiarch {{kernel}} -ex "target remote :1234"

# Read $time at reset via GDB before the first guest instruction (E2).
measure-e2:
    bash scripts/measure-e2.sh

# Every .utext reference must resolve in user sections or a task window.
check-utext: build
    bash scripts/check-utext.sh {{kernel}}

# Planted c.fld must fail check-utext by name (D-0044).
check-utext-planted:
    #!/usr/bin/env bash
    set -euo pipefail
    bash scripts/cargo-debug.sh build --features utext-c-fld-selftest
    set +e
    out=$(bash scripts/check-utext.sh {{kernel}} 2>&1)
    st=$?
    set -e
    echo "$out"
    if [ "$st" -eq 0 ]; then
        echo 'TEST FAIL: planted c.fld was accepted by check-utext'
        exit 1
    fi
    if ! echo "$out" | grep -q 'c.fld'; then
        echo 'TEST FAIL: check-utext failed but not by naming c.fld'
        exit 1
    fi
    echo 'TEST PASS: planted c.fld rejected by name'
    bash scripts/cargo-debug.sh build

# Headless default boot: M3 UNIKERNEL OK, curl 200, phases, gateway ARP.
test expect="M3 UNIKERNEL OK" timeout_s="12":
    #!/usr/bin/env bash
    set -euo pipefail
    e='{{expect}}'
    t='{{timeout_s}}'
    case "$e" in expect=*) e="${e#expect=}" ;; esac
    case "$t" in timeout_s=*) t="${t#timeout_s=}" ;; esac
    EXPECT="$e" TIMEOUT_S="$t" bash scripts/boot-test.sh
    if [ "$e" = "M3 UNIKERNEL OK" ]; then
        log=serial.log
        if ! grep -a -q 'tick 3' "$log"; then
            echo 'TEST FAIL: missing tick 3'
            exit 1
        fi
        if ! grep -a -q 'frames frozen: free=' "$log"; then
            echo 'TEST FAIL: missing "frames frozen: free=N"'
            exit 1
        fi
        if ! grep -aE -q 'virtio lo[[:space:]]+0x0010001000 -> 0x0010001000  V R W   U=0 A D' "$log"; then
            echo 'TEST FAIL: virtio-mmio window lo not mapped R+W U=0 non-X'
            exit 1
        fi
        if ! grep -aE -q 'virtio hi[[:space:]]+0x0010008fff -> 0x0010008fff  V R W   U=0 A D' "$log"; then
            echo 'TEST FAIL: virtio-mmio window hi not mapped R+W U=0 non-X'
            exit 1
        fi
        if ! grep -a -q 'virtio-mmio 0 0x10001000 magic=0x74726976 version=2' "$log"; then
            echo 'TEST FAIL: virtio-mmio slot 0 missing modern magic/version'
            exit 1
        fi
        if ! grep -a -q 'device=1 (net)' "$log"; then
            echo 'TEST FAIL: no virtio-mmio net device in probe table'
            exit 1
        fi
        if ! grep -a -q 'VIRTQ OK' "$log"; then
            echo 'TEST FAIL: missing VIRTQ OK'
            exit 1
        fi
        if ! grep -a -q 'DRIVER_OK' "$log"; then
            echo 'TEST FAIL: missing DRIVER_OK'
            exit 1
        fi
        if ! grep -a -q 'ARP CACHE WRAP OK' "$log"; then
            echo 'TEST FAIL: missing ARP CACHE WRAP OK'
            exit 1
        fi
        if ! grep -a -q 'CHECKSUM OK' "$log"; then
            echo 'TEST FAIL: missing CHECKSUM OK'
            exit 1
        fi
        if ! grep -a -q 'ICMP REPLY BUILD OK' "$log"; then
            echo 'TEST FAIL: missing ICMP REPLY BUILD OK'
            exit 1
        fi
        if ! grep -a -q 'UDP ECHO BUILD OK' "$log"; then
            echo 'TEST FAIL: missing UDP ECHO BUILD OK'
            exit 1
        fi
        if ! grep -a -q 'TCP SYN/ACK BUILD OK' "$log"; then
            echo 'TEST FAIL: missing TCP SYN/ACK BUILD OK'
            exit 1
        fi
        if ! grep -a -q 'TCP LISTEN' "$log"; then
            echo 'TEST FAIL: missing TCP LISTEN'
            exit 1
        fi
        if ! grep -a -q 'TX ARP request for 10.0.2.2' "$log"; then
            echo 'TEST FAIL: missing TX ARP request for 10.0.2.2'
            exit 1
        fi
        if ! grep -a -q 'gateway 10.0.2.2 MAC learned' "$log"; then
            echo 'TEST FAIL: missing gateway MAC learned'
            exit 1
        fi
        if ! grep -a -q 'TX GARP completed=' "$log"; then
            echo 'TEST FAIL: missing TX GARP completed'
            exit 1
        fi
        if ! grep -a -q 'PING RTT dst=10.0.2.2' "$log"; then
            echo 'TEST FAIL: missing PING RTT'
            exit 1
        fi
        if ! grep -aE -q 'PING RTT dst=10.0.2.2 id=1 seq=1 tx=[0-9]+ rx=[0-9]+ ticks=[0-9]+ ns=[0-9]+' "$log"; then
            echo 'TEST FAIL: PING RTT line is not tx/rx/ticks/ns'
            exit 1
        fi
        if ! grep -a -q 'HTTP READY' "$log"; then
            echo 'TEST FAIL: missing HTTP READY'
            exit 1
        fi
        if ! grep -a -q 'HTTP DONE' "$log"; then
            echo 'TEST FAIL: missing HTTP DONE'
            exit 1
        fi
        if ! grep -a -q 'TX TCP SYN/ACK' "$log"; then
            echo 'TEST FAIL: missing TX TCP SYN/ACK'
            exit 1
        fi
        if ! grep -a -q 'TCP ESTABLISHED' "$log"; then
            echo 'TEST FAIL: missing TCP ESTABLISHED'
            exit 1
        fi
        if ! grep -a -q 'tcp: TX FIN' "$log"; then
            echo 'TEST FAIL: missing TX FIN arithmetic'
            exit 1
        fi
        if ! grep -a -q 'tcp: RX FIN' "$log"; then
            echo 'TEST FAIL: missing RX FIN arithmetic'
            exit 1
        fi
        if ! grep -a -q 'TCP TIME_WAIT (truncated)' "$log"; then
            echo 'TEST FAIL: missing truncated TIME_WAIT'
            exit 1
        fi
        if grep -a -q 'TCP RETRANSMIT' "$log"; then
            echo 'TEST FAIL: unexpected retransmit on the happy path'
            exit 1
        fi
        # Finding 26 / D-0057: one `phase_names` list (test / test-fast /
        # test-fast-release). The harness parses serial, not this list.
        for ph in {{phase_names}}; do
            if ! grep -a -q "PHASE ${ph} " "$log"; then
                echo "TEST FAIL: missing PHASE ${ph}"
                exit 1
            fi
        done
        if grep -a -q 'PHASE .* unset' "$log"; then
            echo 'TEST FAIL: a PHASE stamp was unset'
            grep -a 'PHASE .* unset' "$log" || true
            exit 1
        fi
        python3 scripts/bench.py check-serial "$log"
        if [ ! -f http.status ]; then
            echo 'TEST FAIL: http.status missing (curl never ran or was killed first)'
            exit 1
        fi
        if [ "$(cat http.status)" != "0" ]; then
            echo "TEST FAIL: curl exited $(cat http.status), want 0"
            cat http.hdr 2>/dev/null || true
            exit 1
        fi
        if ! python3 -c 'import sys; sys.exit(0 if open("http.body","rb").read()==b"whimbrel\n" else 1)'; then
            echo 'TEST FAIL: HTTP body is not exactly whimbrel\\n'
            python3 -c 'print(open("http.body","rb").read())' 2>/dev/null || true
            exit 1
        fi
        if ! grep -q 'HTTP/1.0 200' http.hdr; then
            echo 'TEST FAIL: missing HTTP/1.0 200 in curl headers'
            cat http.hdr
            exit 1
        fi
        if ! grep -qi 'Connection: close' http.hdr; then
            echo 'TEST FAIL: missing Connection: close in curl headers'
            cat http.hdr
            exit 1
        fi
        if ! grep -a -q 'ip_drop short=0 ver=0 ihl=0 csum=0 frag=0 dst=0 proto=0' "$log"; then
            echo 'TEST FAIL: IPv4 malformed/proto counters are not 0'
            exit 1
        fi
        if ! grep -a -q 'tcp_drop short=0 doff=0 csum=0 opt=0' "$log"; then
            echo 'TEST FAIL: TCP malformed counters are not 0'
            exit 1
        fi
        if ! grep -a -q 'udp_drop short=0 len=0 csum=0 port=0' "$log"; then
            echo 'TEST FAIL: UDP malformed counters are not 0'
            exit 1
        fi
        if ! grep -a -q 'icmp_drop short=0 csum=0' "$log"; then
            echo 'TEST FAIL: ICMP malformed counters are not 0'
            exit 1
        fi
        if ! bash scripts/assert-pcap-garp.sh whimbrel.pcap; then
            echo 'TEST FAIL: pcap GARP assertion'
            exit 1
        fi
        if ! bash scripts/assert-pcap-gateway-arp.sh whimbrel.pcap; then
            echo 'TEST FAIL: pcap gateway ARP assertion'
            exit 1
        fi
        if ! bash scripts/check-assert-fail-closed.sh; then
            echo 'TEST FAIL: pcap assert failure-mode check'
            exit 1
        fi
        if ! bash scripts/assert-pcap-icmp.sh whimbrel.pcap; then
            echo 'TEST FAIL: pcap ICMP echo assertion'
            exit 1
        fi
        if ! bash scripts/assert-pcap-http.sh whimbrel.pcap; then
            echo 'TEST FAIL: pcap HTTP assertion'
            exit 1
        fi
        echo 'TEST PASS: M3 UNIKERNEL OK, curl 200, phases, gateway ARP, pcap HTTP'
    fi

# Designed FAIL: panic-selftest parks after PANIC (exit 1).
test-panic:
    bash scripts/boot-test.sh panic-selftest; [ $? -eq 1 ]

# Designed HANG: hang-selftest prints nothing until timeout (exit 2).
test-hang:
    bash scripts/boot-test.sh hang-selftest; [ $? -eq 2 ]

# Allocator storm then frame-exhaust panic with matching total.
test-stress:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="STRESS OK" TIMEOUT_S=30 bash scripts/boot-test.sh stress
    set +e
    TIMEOUT_S=20 bash scripts/boot-test.sh frame-exhaust-selftest
    code=$?
    set -e
    if [ "$code" -ne 1 ]; then
        echo "TEST FAIL: frame exhaust expected panic (exit 1), got ${code}"
        exit 1
    fi
    n=$(grep -aE '^frames [0-9]+ heap_start=' serial.log | head -n1 | awk '{print $2}')
    if [ -z "$n" ]; then
        echo 'TEST FAIL: no FRAME OK frame count in serial.log'
        exit 1
    fi
    if ! grep -a -q "out of frames (total ${n})" serial.log; then
        echo "TEST FAIL: exhaust panic did not report total ${n}"
        grep -a 'PANIC' serial.log || true
        exit 1
    fi
    echo "TEST PASS: frame exhaust total=${n}"

# Both invalid-pointer shapes, each in its own image (D-0034).
test-userptr:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="USERPTR OK" TIMEOUT_S=3 bash scripts/boot-test.sh userptr-kernel-selftest
    if ! grep -a -q 'not in a user interval' serial.log; then
        echo 'TEST FAIL: kernel-address case missing'
        exit 1
    fi
    EXPECT="USERPTR OK" TIMEOUT_S=3 bash scripts/boot-test.sh userptr-span-selftest
    if ! grep -a -q 'spans past interval' serial.log; then
        echo 'TEST FAIL: span case missing'
        exit 1
    fi
    echo 'TEST PASS: both invalid-pointer shapes killed'

# U-mode load page fault kills one task; the other finishes (D-0034).
test-user-fault:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="USERFAULT OK" TIMEOUT_S=5 bash scripts/boot-test.sh user-fault-selftest
    if ! grep -a -q 'task 2 killed: load page fault' serial.log; then
        echo 'TEST FAIL: missing "task 2 killed: load page fault"'
        exit 1
    fi
    if ! grep -aE -q 'task 1 done writes=[0-9]+ yields=0' serial.log; then
        echo 'TEST FAIL: survivor did not run to completion'
        exit 1
    fi
    echo 'TEST PASS: user fault contained, survivor finished'

# Freeze then a deliberate alloc_frame must panic.
test-freeze:
    #!/usr/bin/env bash
    set -euo pipefail
    set +e
    TIMEOUT_S=5 bash scripts/boot-test.sh freeze-selftest
    code=$?
    set -e
    if [ "$code" -ne 1 ]; then
        echo "TEST FAIL: freeze-selftest expected panic (exit 1), got ${code}"
        exit 1
    fi
    if ! grep -a -q 'frames frozen: free=' serial.log; then
        echo 'TEST FAIL: missing "frames frozen: free=N"'
        exit 1
    fi
    if ! grep -a -q 'alloc_frame after freeze' serial.log; then
        echo 'TEST FAIL: missing "alloc_frame after freeze" panic'
        grep -a 'PANIC' serial.log || true
        exit 1
    fi
    echo 'TEST PASS: freeze then alloc_frame panicked'

# Handshake sibling: DRIVER_OK, gateway ARP, ping, TCP handshake (no U-mode).
test-net-init:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="NET INIT OK" TIMEOUT_S=8 bash scripts/boot-test.sh net-init-selftest
    if ! grep -a -q 'virtio-net: FEATURES_OK status=' serial.log; then
        echo 'TEST FAIL: missing FEATURES_OK readback'
        exit 1
    fi
    if ! grep -a -q 'DRIVER_OK' serial.log; then
        echo 'TEST FAIL: missing DRIVER_OK'
        exit 1
    fi
    if ! grep -aE -q 'virtio-net: mac [0-9a-f]{2}(:[0-9a-f]{2}){5}' serial.log; then
        echo 'TEST FAIL: missing MAC'
        exit 1
    fi
    if ! grep -a -q 'net: dump status=' serial.log; then
        echo 'TEST FAIL: missing net::dump'
        exit 1
    fi
    if ! grep -a -q 'ARP CACHE WRAP OK' serial.log; then
        echo 'TEST FAIL: missing ARP CACHE WRAP OK'
        exit 1
    fi
    if ! grep -a -q 'CHECKSUM OK' serial.log; then
        echo 'TEST FAIL: missing CHECKSUM OK'
        exit 1
    fi
    if ! grep -a -q 'ICMP REPLY BUILD OK' serial.log; then
        echo 'TEST FAIL: missing ICMP REPLY BUILD OK'
        exit 1
    fi
    if ! grep -a -q 'UDP ECHO BUILD OK' serial.log; then
        echo 'TEST FAIL: missing UDP ECHO BUILD OK'
        exit 1
    fi
    if ! grep -a -q 'TCP SYN/ACK BUILD OK' serial.log; then
        echo 'TEST FAIL: missing TCP SYN/ACK BUILD OK'
        exit 1
    fi
    if ! grep -a -q 'TX ARP request for 10.0.2.2' serial.log; then
        echo 'TEST FAIL: missing TX ARP request for 10.0.2.2'
        exit 1
    fi
    if ! grep -a -q 'TX GARP completed=' serial.log; then
        echo 'TEST FAIL: missing TX GARP completed'
        exit 1
    fi
    if ! grep -a -q 'gateway 10.0.2.2 MAC learned' serial.log; then
        echo 'TEST FAIL: missing gateway MAC learned'
        exit 1
    fi
    if ! grep -a -q 'TX TCP SYN/ACK' serial.log; then
        echo 'TEST FAIL: missing TX TCP SYN/ACK'
        exit 1
    fi
    if ! grep -a -q 'TCP ESTABLISHED' serial.log; then
        echo 'TEST FAIL: missing TCP ESTABLISHED'
        exit 1
    fi
    if ! bash scripts/assert-pcap-garp.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap GARP assertion'
        exit 1
    fi
    if ! grep -a -q 'virtio-net: RX arp' serial.log; then
        echo 'TEST FAIL: missing RX arp classification'
        exit 1
    fi
    if ! grep -aE -q 'rx avail=[0-9]+ used=[1-9][0-9]* posted=[0-9]+ completed=[1-9]' serial.log; then
        echo 'TEST FAIL: RX completed did not increment'
        exit 1
    fi
    if ! bash scripts/assert-pcap-gateway-arp.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap gateway ARP assertion'
        exit 1
    fi
    if ! grep -a -q 'PING RTT dst=10.0.2.2' serial.log; then
        echo 'TEST FAIL: missing PING RTT'
        exit 1
    fi
    if ! grep -aE -q 'PING RTT dst=10.0.2.2 id=1 seq=1 tx=[0-9]+ rx=[0-9]+ ticks=[0-9]+ ns=[0-9]+' serial.log; then
        echo 'TEST FAIL: PING RTT line is not tx/rx/ticks/ns'
        exit 1
    fi
    if ! grep -a -q 'ip_drop short=0 ver=0 ihl=0 csum=0 frag=0 dst=0 proto=0' serial.log; then
        echo 'TEST FAIL: IPv4 malformed/proto counters are not 0'
        exit 1
    fi
    if ! grep -a -q 'tcp_drop short=0 doff=0 csum=0 opt=0' serial.log; then
        echo 'TEST FAIL: TCP malformed counters are not 0'
        exit 1
    fi
    if ! grep -a -q 'udp_drop short=0 len=0 csum=0 port=0' serial.log; then
        echo 'TEST FAIL: UDP malformed counters are not 0'
        exit 1
    fi
    if ! grep -a -q 'icmp_drop short=0 csum=0' serial.log; then
        echo 'TEST FAIL: ICMP malformed counters are not 0'
        exit 1
    fi
    if ! bash scripts/assert-pcap-icmp.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap ICMP echo assertion'
        exit 1
    fi
    if ! bash scripts/assert-pcap-tcp-handshake.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap TCP handshake assertion'
        exit 1
    fi
    echo 'TEST PASS: DRIVER_OK, MAC, dump, gateway ARP, GARP, RX ARP, PING RTT, TCP handshake'

# TCP handshake sibling of test-net-init (no HTTP); connect after gateway MAC.
test-net-tcp:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="NET INIT OK" TIMEOUT_S=12 bash scripts/boot-test.sh net-init-selftest
    if ! grep -a -q 'TCP SYN/ACK BUILD OK' serial.log; then
        echo 'TEST FAIL: missing TCP SYN/ACK BUILD OK'
        exit 1
    fi
    if ! grep -a -q 'TCP LISTEN' serial.log; then
        echo 'TEST FAIL: missing TCP LISTEN'
        exit 1
    fi
    if ! grep -a -q 'gateway 10.0.2.2 MAC learned' serial.log; then
        echo 'TEST FAIL: missing gateway MAC learned'
        exit 1
    fi
    if ! grep -a -q 'TX TCP SYN/ACK' serial.log; then
        echo 'TEST FAIL: missing TX TCP SYN/ACK'
        exit 1
    fi
    if ! grep -a -q 'TCP ESTABLISHED' serial.log; then
        echo 'TEST FAIL: missing TCP ESTABLISHED'
        exit 1
    fi
    if ! grep -a -q 'ip_drop short=0 ver=0 ihl=0 csum=0 frag=0 dst=0 proto=0' serial.log; then
        echo 'TEST FAIL: IPv4 malformed/proto counters are not 0'
        exit 1
    fi
    if ! grep -a -q 'tcp_drop short=0 doff=0 csum=0 opt=0' serial.log; then
        echo 'TEST FAIL: TCP malformed counters are not 0'
        exit 1
    fi
    if ! bash scripts/assert-pcap-tcp-handshake.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap TCP handshake assertion'
        exit 1
    fi
    echo 'TEST PASS: TCP LISTEN → SYN_RCVD → ESTABLISHED, pcap SYN→SYN/ACK→ACK, checksum good, no RST'

# UDP echo in the app over recv/send; SOCK_DGRAM client, silence is FAIL.
test-net-udp:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="NET UDP OK" TIMEOUT_S=8 bash scripts/boot-test.sh net-udp-selftest
    if [ ! -f udp-echo.got ]; then
        echo 'TEST FAIL: udp-echo.got missing (client never ran or timed out)'
        cat udp-echo.status 2>/dev/null || true
        exit 1
    fi
    if [ "$(cat udp-echo.status 2>/dev/null || echo 1)" != "0" ]; then
        echo 'TEST FAIL: UDP echo client exited nonzero'
        cat udp-echo.status || true
        exit 1
    fi
    if [ "$(cat udp-echo.got)" != "whimbrel-udp-echo" ]; then
        echo "TEST FAIL: UDP payload mismatch: $(cat udp-echo.got | sed 's/[^[:print:]]/?/g')"
        exit 1
    fi
    if ! grep -a -q 'UDP ECHO READY' serial.log; then
        echo 'TEST FAIL: missing UDP ECHO READY'
        exit 1
    fi
    if ! grep -a -q 'TX UDP echo' serial.log; then
        echo 'TEST FAIL: missing TX UDP echo'
        exit 1
    fi
    if ! grep -a -q 'udp_drop short=0 len=0 csum=0 port=0' serial.log; then
        echo 'TEST FAIL: UDP malformed counters are not 0'
        exit 1
    fi
    if ! grep -a -q 'ip_drop short=0 ver=0 ihl=0 csum=0 frag=0 dst=0 proto=0' serial.log; then
        echo 'TEST FAIL: IPv4 malformed/proto counters are not 0'
        exit 1
    fi
    if ! bash scripts/assert-pcap-udp-echo.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap UDP echo assertion'
        exit 1
    fi
    echo 'TEST PASS: UDP echo verbatim, pcap request→reply'

# One GET: HTTP/1.0 200, Connection: close, FIN close, no RST.
test-net-http:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="HTTP OK" TIMEOUT_S=12 bash scripts/boot-test.sh net-http-selftest
    if [ ! -f http.status ]; then
        echo 'TEST FAIL: http.status missing (curl never ran or was killed first)'
        exit 1
    fi
    if [ "$(cat http.status)" != "0" ]; then
        echo "TEST FAIL: curl exited $(cat http.status), want 0"
        cat http.hdr 2>/dev/null || true
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if open("http.body","rb").read()==b"whimbrel\n" else 1)'; then
        echo 'TEST FAIL: HTTP body is not exactly whimbrel\\n'
        python3 -c 'print(open("http.body","rb").read())' 2>/dev/null || true
        exit 1
    fi
    if ! grep -q 'HTTP/1.0 200' http.hdr; then
        echo 'TEST FAIL: missing HTTP/1.0 200 in curl headers'
        cat http.hdr
        exit 1
    fi
    if ! grep -qi 'Connection: close' http.hdr; then
        echo 'TEST FAIL: missing Connection: close in curl headers'
        cat http.hdr
        exit 1
    fi
    if ! grep -a -q 'HTTP READY' serial.log; then
        echo 'TEST FAIL: missing HTTP READY'
        exit 1
    fi
    if ! grep -a -q 'HTTP DONE' serial.log; then
        echo 'TEST FAIL: missing HTTP DONE'
        exit 1
    fi
    if ! grep -a -q 'tcp: TX FIN' serial.log; then
        echo 'TEST FAIL: missing TX FIN arithmetic'
        exit 1
    fi
    if ! grep -a -q 'tcp: RX FIN' serial.log; then
        echo 'TEST FAIL: missing RX FIN arithmetic'
        exit 1
    fi
    if ! grep -a -q 'TCP TIME_WAIT (truncated)' serial.log; then
        echo 'TEST FAIL: missing truncated TIME_WAIT'
        exit 1
    fi
    if grep -a -q 'TCP RETRANSMIT' serial.log; then
        echo 'TEST FAIL: unexpected retransmit on the happy path'
        exit 1
    fi
    if ! grep -a -q 'ip_drop short=0 ver=0 ihl=0 csum=0 frag=0 dst=0 proto=0' serial.log; then
        echo 'TEST FAIL: IPv4 malformed/proto counters are not 0'
        exit 1
    fi
    if ! grep -a -q 'tcp_drop short=0 doff=0 csum=0 opt=0' serial.log; then
        echo 'TEST FAIL: TCP malformed counters are not 0'
        exit 1
    fi
    if ! bash scripts/assert-pcap-http.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap HTTP assertion'
        exit 1
    fi
    echo 'TEST PASS: curl 200 whimbrel, Connection: close, FIN close, checksums good, no RST'

# Drop-first-tx: one RTO retransmit ~200ms, two copies, second ACKed.
test-net-rto:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="HTTP RETRANSMIT OK" TIMEOUT_S=12 bash scripts/boot-test.sh tcp-drop-first-tx
    if [ ! -f http.status ]; then
        echo 'TEST FAIL: http.status missing (curl never ran or was killed first)'
        exit 1
    fi
    if [ "$(cat http.status)" != "0" ]; then
        echo "TEST FAIL: curl exited $(cat http.status), want 0"
        cat http.hdr 2>/dev/null || true
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if open("http.body","rb").read()==b"whimbrel\n" else 1)'; then
        echo 'TEST FAIL: HTTP body is not exactly whimbrel\\n'
        python3 -c 'print(open("http.body","rb").read())' 2>/dev/null || true
        exit 1
    fi
    if ! grep -a -q 'TCP RETRANSMIT' serial.log; then
        echo 'TEST FAIL: missing TCP RETRANSMIT (timer never fired)'
        exit 1
    fi
    if ! grep -aE -q 'rexmit=1($|[^0-9])' serial.log; then
        echo 'TEST FAIL: dump rexmit is not 1'
        grep -a 'rexmit=' serial.log || true
        exit 1
    fi
    if ! grep -a -q 'ip_drop short=0 ver=0 ihl=0 csum=0 frag=0 dst=0 proto=0' serial.log; then
        echo 'TEST FAIL: IPv4 malformed/proto counters are not 0'
        exit 1
    fi
    if ! bash scripts/assert-pcap-tcp-retransmit.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap retransmit assertion'
        exit 1
    fi
    echo 'TEST PASS: one RTO retransmit ~200ms, two copies same seq, second ACKed'

# fast-boot profile: no tick wait, no self-tests; curl and PHASE still required.
test-fast:
    #!/usr/bin/env bash
    set -euo pipefail
    EXPECT="M3 UNIKERNEL OK" TIMEOUT_S=12 bash scripts/boot-test.sh fast-boot
    log=serial.log
    for ph in {{phase_names}}; do
        if ! grep -a -q "PHASE ${ph} " "$log"; then
            echo "TEST FAIL: missing PHASE ${ph}"
            exit 1
        fi
    done
    if grep -a -q 'PHASE .* unset' "$log"; then
        echo 'TEST FAIL: a PHASE stamp was unset'
        grep -a 'PHASE .* unset' "$log" || true
        exit 1
    fi
    python3 scripts/bench.py check-serial "$log"
    if [ ! -f http.status ] || [ "$(cat http.status)" != "0" ]; then
        echo "TEST FAIL: curl status $(cat http.status 2>/dev/null || echo missing), want 0"
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if open("http.body","rb").read()==b"whimbrel\n" else 1)'; then
        echo 'TEST FAIL: HTTP body is not exactly whimbrel\\n'
        exit 1
    fi
    if ! bash scripts/assert-pcap-gateway-arp.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap gateway ARP assertion'
        exit 1
    fi
    if ! bash scripts/assert-pcap-http.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap HTTP assertion'
        exit 1
    fi
    echo 'TEST PASS: fast-boot M3 UNIKERNEL OK, curl 200, phases'

# Release+fast-boot phases with a client retrying before E0 (D-0043).
test-fast-release:
    #!/usr/bin/env bash
    set -euo pipefail
    PROFILE=release CLIENT_EARLY=1 EXPECT="M3 UNIKERNEL OK" TIMEOUT_S=12 \
        bash scripts/boot-test.sh fast-boot
    log=serial.log
    for ph in {{phase_names}}; do
        if ! grep -a -q "PHASE ${ph} " "$log"; then
            echo "TEST FAIL: missing PHASE ${ph}"
            exit 1
        fi
    done
    if grep -a -q 'PHASE .* unset' "$log"; then
        echo 'TEST FAIL: a PHASE stamp was unset'
        grep -a 'PHASE .* unset' "$log" || true
        exit 1
    fi
    python3 scripts/bench.py check-serial "$log"
    if [ ! -f http.status ] || [ "$(cat http.status)" != "0" ]; then
        echo "TEST FAIL: curl status $(cat http.status 2>/dev/null || echo missing), want 0"
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if open("http.body","rb").read()==b"whimbrel\n" else 1)'; then
        echo 'TEST FAIL: HTTP body is not exactly whimbrel\\n'
        exit 1
    fi
    if ! bash scripts/assert-pcap-gateway-arp.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap gateway ARP assertion'
        exit 1
    fi
    if ! bash scripts/assert-pcap-http.sh whimbrel.pcap; then
        echo 'TEST FAIL: pcap HTTP assertion'
        exit 1
    fi
    echo 'TEST PASS: release fast-boot M3 UNIKERNEL OK, curl 200, phases'

# D-0061 / D-0079: the no-firmware lane's gate subset (boot, net, HTTP,
# fast-release). Builds the donor, extracts the shim blob into QEMU's
# -bios slot, then runs the release default image (boot markers, tick
# wait = taken-interrupt proof, selftests, curl 200) and the measured
# fast-boot profile (early client, PHASE presence, pcap asserts).
# boot-test PASS requires QEMU exit 0, which is the sifive_test
# shutdown proof: a wrong store value parks the guest into a 124 HANG.
# The full 16-gate list stays on -bios default.
test-m:
    #!/usr/bin/env bash
    set -euo pipefail
    cargo build --release --features mshim
    blob=target/riscv64gc-unknown-none-elf/release/mshim.bin
    bash scripts/mshim-blob.sh \
        target/riscv64gc-unknown-none-elf/release/whimbrel "$blob"
    QEMU_BIOS="$blob" PROFILE=release EXPECT="M3 UNIKERNEL OK" \
        TIMEOUT_S=12 bash scripts/boot-test.sh bios-none
    if [ ! -f http.status ] || [ "$(cat http.status)" != "0" ]; then
        echo "TEST FAIL: lane curl status $(cat http.status 2>/dev/null || echo missing), want 0"
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if open("http.body","rb").read()==b"whimbrel\n" else 1)'; then
        echo 'TEST FAIL: lane HTTP body is not exactly whimbrel\n'
        exit 1
    fi
    QEMU_BIOS="$blob" PROFILE=release CLIENT_EARLY=1 EXPECT="M3 UNIKERNEL OK" \
        TIMEOUT_S=12 bash scripts/boot-test.sh bios-none,fast-boot
    log=serial.log
    for ph in {{phase_names}}; do
        if ! grep -a -q "PHASE ${ph} " "$log"; then
            echo "TEST FAIL: lane missing PHASE ${ph}"
            exit 1
        fi
    done
    if grep -a -q 'PHASE .* unset' "$log"; then
        echo 'TEST FAIL: a lane PHASE stamp was unset'
        exit 1
    fi
    python3 scripts/bench.py check-serial "$log"
    if [ ! -f http.status ] || [ "$(cat http.status)" != "0" ]; then
        echo "TEST FAIL: lane fast curl status $(cat http.status 2>/dev/null || echo missing), want 0"
        exit 1
    fi
    if ! bash scripts/assert-pcap-gateway-arp.sh whimbrel.pcap; then
        echo 'TEST FAIL: lane pcap gateway ARP assertion'
        exit 1
    fi
    if ! bash scripts/assert-pcap-http.sh whimbrel.pcap; then
        echo 'TEST FAIL: lane pcap HTTP assertion'
        exit 1
    fi
    echo 'TEST PASS: test-m — boot, net, HTTP, fast-release under the M-mode shim'

# T4.1 / D-0055: N=30 recorded + 3 warmup per config, two interleaved
# batches (configs mixed, recorded trial order shuffled). Writes
# results/runs.csv, results/phases.csv, results/summary.txt.
bench-whimbrel:
    bash scripts/bench.sh whimbrel

# D-0079 / T4.7: the with/without-firmware pair, four whimbrel arms
# interleaved in one campaign (shared canary, shared controls) so the
# exhibit's same-campaign gate can hold. Dedicated bench host only.
bench-t47:
    bash scripts/bench.sh t47

# T4.8 five-arm campaign. Requires bench/linux/artifacts + MANIFEST.
# This pod does not run it (D-0055). D-0073 / T4.8b uses the same
# recipe after linux-build produces a new Image-trimmed.
bench-t48:
    bash scripts/bench.sh t48

# Fail-closed checks (missing tshark, malformed PHASE, zero-trial CSV,
# QEMU/git mismatch, dirty tree, host controls, origin sync, D-0071
# schema / S / first-connect / pcap intervals, T4.8 argv / PHASE skip).
bench-selftest:
    bash scripts/bench.sh selftest

# Finding 14: release+fast-boot with vs without force-frame-pointers.
bench-fp-ab:
    bash scripts/bench.sh fp-ab

# Recompute n/median/IQR/min/max from existing CSVs.
bench-summary:
    bash scripts/bench.sh summarize --stability

# T4.3 / T4.4 / T4.6 / T4.8: regenerate report exhibits from git objects.
# Baseline: tag baseline-t4.3. After-ladder / Δ: T4.6 CSV commit.
# Cross-system: T4.8 CSV commit ffb7ac7 (frozen pre-FTRACE; D-0073
# does not retarget this). T4.8b: CSV commit a0c53e2 (D-0073 after
# + D-0075 /init), cross-system-t48b.md, with T4.8 as the before.
# Linux decomposition: serial pin d705ecb plus D-0072 labels
# 93ab617. Working-tree CSVs / serials are not read (D-0067).
report-exhibits:
    python3 scripts/report-exhibits.py

# D-0072: label the 327 ms printk hole. Same Image-trimmed, cmdline
# = instrumented MANIFEST append + ignore_loglevel, System.map
# offline. One boot, not a campaign arm, never runs.csv. Bench host.
# This pod fail-closes without artifacts. Selftest does not boot.
linux-initcall-label:
    python3 scripts/label-linux-initcalls.py selftest
    bash scripts/linux-initcall-label.sh

label-linux-initcalls-selftest:
    python3 scripts/label-linux-initcalls.py selftest

# D-0070 read-only tshark pass over recorded T4.6 / D-0068 pcaps.
# git show of those CSV objects; results/trials/ must already exist.
# extract_pcap is scripts/pcap_http.py (shared with the T4.8 harness).
d0070-pcap-pass:
    python3 scripts/d0070-pcap-pass.py

d0070-pcap-pass-selftest:
    python3 scripts/d0070-pcap-pass.py selftest

# T4.8 / D-0062 / D-0073: pin, fetch, and build Linux baseline
# artifacts on the dedicated host. Prints five verification blocks
# plus D-0073 3b and D-0062 keeps 3c. Reuse of Image-trimmed is fragment-stamp gated.
# Stock hash must stay the T4.8 pin; trimmed must move. Never
# inside a batch. T4.8b runs this before just bench-t48.
# merge_config "redefined by fragment" is informational.
# "not in final .config": three cases (survival / vanished /
# dependent drop). Keeps are asserted on the final .config.
linux-build:
    python3 scripts/linux-merge-warnings.py selftest
    bash scripts/linux-build.sh

linux-merge-warnings-selftest:
    python3 scripts/linux-merge-warnings.py selftest

# Linux boot gate: MANIFEST hashes, READY (CRLF-tolerant), 92-byte
# on-wire RESP / pcap HTTP, SYN-grid, no RST, QEMU exit 0.
# Fail-closed if artifacts are missing. HTTP client is bench-client
# (queued SYN / confound A); never curl-after-READY.
test-linux image="trimmed" timeout_s="60":
    TIMEOUT_S={{timeout_s}} bash scripts/linux-boot-test.sh {{image}}

# Disassemble the kernel (extra flags as one quoted arg).
objdump flags="-d": build
    cargo objdump -- {{flags}}

# Map a guest address to a source line.
addr2line addr: build
    gdb-multiarch -batch -ex "info line *{{addr}}" {{kernel}}
