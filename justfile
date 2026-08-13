# Task runner for Kestrel. `just --list` shows all recipes.

set shell := ["bash", "-uc"]

target    := "riscv64gc-unknown-none-elf"
kernel    := "target/" + target + "/debug/kestrel"
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

# Invert the script's intentional non-zero so just does not print
# "recipe failed" for a designed FAIL/HANG. `-` would also hide a
# regression (panic-selftest shutting down cleanly would look green).
# `just test` is unchanged: it still fails the recipe on a non-pass.
test-panic:
    bash scripts/boot-test.sh panic-selftest; [ $? -eq 1 ]

test-hang:
    bash scripts/boot-test.sh hang-selftest; [ $? -eq 2 ]

# Disassemble the kernel. Extra flags as one quoted arg, e.g. just objdump '-d --source'
objdump flags="-d": build
    cargo objdump -- {{flags}}

# Map an address (e.g. a faulting sepc) to a source line:  just addr2line 0x80200048
addr2line addr: build
    gdb-multiarch -batch -ex "info line *{{addr}}" {{kernel}}
