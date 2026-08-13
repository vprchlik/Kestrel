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
# Override the marker / hang-guard, e.g. `just test expect="CSR OK"`.
test expect="M0 BOOT OK" timeout_s="3":
    EXPECT="{{expect}}" TIMEOUT_S="{{timeout_s}}" bash scripts/boot-test.sh

test-panic:
    bash scripts/boot-test.sh panic-selftest

test-hang:
    bash scripts/boot-test.sh hang-selftest

# Disassemble the kernel. Extra flags as one quoted arg, e.g. just objdump '-d --source'
objdump flags="-d": build
    cargo objdump -- {{flags}}

# Map an address (e.g. a faulting sepc) to a source line:  just addr2line 0x80200048
addr2line addr: build
    gdb-multiarch -batch -ex "info line *{{addr}}" {{kernel}}
