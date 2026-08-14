# Task runner for Whimbrel. `just --list` shows all recipes.

set shell := ["bash", "-uc"]

target    := "riscv64gc-unknown-none-elf"
kernel    := "target/" + target + "/debug/whimbrel"
qemu      := "qemu-system-riscv64"
qemu_args := "-machine virt -nographic -bios default"

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
test expect="M1 FUNDAMENTALS OK" timeout_s="3":
    #!/usr/bin/env bash
    set -u
    e='{{expect}}'
    t='{{timeout_s}}'
    case "$e" in expect=*) e="${e#expect=}" ;; esac
    case "$t" in timeout_s=*) t="${t#timeout_s=}" ;; esac
    EXPECT="$e" TIMEOUT_S="$t" bash scripts/boot-test.sh
    if [ "$e" = "SCHED OK" ]; then
        log=serial.log
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
    n=$(grep -aE 'frames [0-9]+' serial.log | head -n1 | awk '{print $2}')
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
# cannot hide the other (D-0034). Default boot is T2.8 (SBRK OK).
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

# Disassemble the kernel. Extra flags as one quoted arg, e.g. just objdump '-d --source'
objdump flags="-d": build
    cargo objdump -- {{flags}}

# Map an address (e.g. a faulting sepc) to a source line:  just addr2line 0x80200048
addr2line addr: build
    gdb-multiarch -batch -ex "info line *{{addr}}" {{kernel}}
