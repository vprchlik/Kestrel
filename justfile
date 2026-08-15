# Task runner for Whimbrel. `just --list` shows all recipes.

set shell := ["bash", "-uc"]

target    := "riscv64gc-unknown-none-elf"
kernel    := "target/" + target + "/debug/whimbrel"
qemu      := "qemu-system-riscv64"
# D-0038 / D-0039: modern virtio-mmio + a net device on every invocation.
# hostfwd and filter-dump land in later tasks; do not add them here.
qemu_args := "-machine virt -nographic -bios default -global virtio-mmio.force-legacy=false -netdev user,id=net0 -device virtio-net-device,netdev=net0"

# Build the kernel (debug profile; target + linker script via .cargo/config.toml).
build:
    cargo build

# Boot in QEMU. Extra QEMU flags as one quoted arg,
# e.g.  just run '-d int,cpu_reset,guest_errors -D qemu.log'
# After T0.5, QEMU exits 0 on its own (no Ctrl-a x).
run qemu_extra="": build
    {{qemu}} {{qemu_args}} {{qemu_extra}} -kernel {{kernel}}

# Boot a build that panics in kmain. Parks after the PANIC line (no SRST).
# Prefer `just test-panic` for a FAIL verdict; this recipe
# is the live serial view. Timeout is a hang-guard; its status is not swallowed.
panic timeout_s="5":
    cargo build --features panic-selftest
    timeout --foreground {{timeout_s}} {{qemu}} {{qemu_args}} -kernel {{kernel}}

# Boot frozen at reset (-S) with the GDB stub on tcp::1234 (-s).
# Then attach from another terminal with `just gdb`, or press F5 in the editor.
debug: build
    {{qemu}} {{qemu_args}} -s -S -kernel {{kernel}}

# Attach gdb-multiarch to a running `just debug` QEMU.
gdb:
    gdb-multiarch {{kernel}} -ex "target remote :1234"

# Every symbol referenced from .utext must resolve in user sections or a
# task's stack/break window — not kernel .text/.rodata. `lui`/`li` values
# are not references; `auipc+addi` to .urodata is legitimate.
check-utext: build
    bash scripts/check-utext.sh {{kernel}}

# Headless boot. Verdict from serial + QEMU status together (D-0017):
#   PANIC in serial          → TEST FAIL (exit 1), panic line echoed
#   timeout (status 124)     → TEST HANG (exit 2)
#   marker + QEMU exit 0     → TEST PASS (exit 0)
#   anything else            → TEST FAIL (exit 1)
# Check PANIC before timeout: a panicking kernel parks, so timeout also fires.
# just 1.58 has no kwargs: `just test expect="CSR OK"` passes the literal
# string `expect=CSR OK` as the first positional. Strip a matching `name=`
# prefix so that form, `just test "CSR OK"`, and the defaults all work.
test expect="M2 EXECUTION OK" timeout_s="5":
    #!/usr/bin/env bash
    set -u
    e='{{expect}}'
    t='{{timeout_s}}'
    case "$e" in expect=*) e="${e#expect=}" ;; esac
    case "$t" in timeout_s=*) t="${t#timeout_s=}" ;; esac
    EXPECT="$e" TIMEOUT_S="$t" bash scripts/boot-test.sh
    if [ "$e" = "SCHED OK" ] || [ "$e" = "M2 EXECUTION OK" ]; then
        log=serial.log
        if ! grep -a -q 'frames frozen: free=' "$log"; then
            echo 'TEST FAIL: missing "frames frozen: free=N"'
            exit 1
        fi
        if ! grep -a -q 'USER OK' "$log"; then
            echo 'TEST FAIL: missing USER OK'
            exit 1
        fi
        if ! grep -a -q 'SYSCALL OK' "$log"; then
            echo 'TEST FAIL: missing SYSCALL OK'
            exit 1
        fi
        if ! grep -a -q 'SBRK OK' "$log"; then
            echo 'TEST FAIL: missing SBRK OK'
            exit 1
        fi
        if ! grep -a -q 'task 1 exit 0' "$log"; then
            echo 'TEST FAIL: missing "task 1 exit 0"'
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
        if ! grep -a -q 'task 2 exit 0' "$log"; then
            echo 'TEST FAIL: missing "task 2 exit 0"'
            exit 1
        fi
        if ! grep -aE -q 'task 1 done writes=[0-9]* yields=0' "$log"; then
            echo 'TEST FAIL: missing "task 1 done writes=… yields=0"'
            exit 1
        fi
        if ! grep -aE -q 'task 2 done writes=[0-9]* yields=0' "$log"; then
            echo 'TEST FAIL: missing "task 2 done writes=… yields=0"'
            exit 1
        fi
        if ! grep -aE -q 'sched switches 1->2=[1-9]' "$log"; then
            echo 'TEST FAIL: missing sched switches 1->2=[1-9]'
            exit 1
        fi
        if ! grep -aE -q '2->1=[1-9]' "$log"; then
            echo 'TEST FAIL: missing 2->1=[1-9]'
            exit 1
        fi
        if ! grep -aE '^task ' "$log" | awk '
            /^task 2 / { if (!t2) t2 = NR }
            /^task 1 done/ { d1 = NR }
            END { if (!t2 || !d1 || t2 >= d1) exit 1 }
        '; then
            echo 'TEST FAIL: first ^task 2  line must precede ^task 1 done'
            exit 1
        fi
        echo 'TEST PASS: sched greps and awk order'
    fi
    if [ "$e" = "M2 EXECUTION OK" ]; then
        log=serial.log
        if ! awk '
            /frames frozen: free=/ { f=NR }
            /USER OK/ { u=NR }
            /SYSCALL OK/ { s=NR }
            /SBRK OK/ { b=NR }
            /SCHED OK/ { c=NR }
            /M2 EXECUTION OK/ { m=NR }
            END { if (!(f && u && s && b && c && m) || !(f<u && u<s && s<b && b<c && c<m)) exit 1 }
        ' "$log"; then
            echo 'TEST FAIL: marker order (frozen, USER, SYSCALL, SBRK, SCHED, M2)'
            exit 1
        fi
        echo 'TEST PASS: M2 marker order'
    fi

# Invert the script's intentional non-zero so just does not print
# "recipe failed" for a designed FAIL/HANG. `-` would also hide a
# regression (panic-selftest shutting down cleanly would look green).
# `just test` is unchanged: it still fails the recipe on a non-pass.
test-panic:
    bash scripts/boot-test.sh panic-selftest; [ $? -eq 1 ]

test-hang:
    bash scripts/boot-test.sh hang-selftest; [ $? -eq 2 ]

# Allocator storm at 10 ms then 1 ms ticks, then frame-exhaust panic.
# Exhaust is a designed PANIC (same harness shape as test-panic).
# The `frames N` total is taken from **this exhaust boot's** FRAME OK line,
# not from `just run`. Feature images shift `__heap_end`, so the default
# kernel can print 31866 while exhaust prints 31867 — that is not a mismatch.
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

# Both invalid-pointer shapes, each in its own image so the kill of one
# cannot hide the other (D-0034).
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

# T2.10: one task takes a U-mode load page fault; the other continues and
# the kernel shuts down cleanly (exit 0, not the inverted panic recipes).
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

# T2.11: freeze then a deliberate alloc_frame. Designed PANIC (same harness
# shape as test-panic).
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

# Disassemble the kernel. Extra flags as one quoted arg, e.g. just objdump '-d --source'
objdump flags="-d": build
    cargo objdump -- {{flags}}

# Map an address (e.g. a faulting sepc) to a source line:  just addr2line 0x80200048
addr2line addr: build
    gdb-multiarch -batch -ex "info line *{{addr}}" {{kernel}}
