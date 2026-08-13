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

# Boot a build that panics in kmain (`panic!("selftest")`) and parks.
# Compile-time cargo feature so the default image never contains the
# selftest. Re-run in M1 after installing stvec to confirm the handler
# still works when panic arrives from a trap context, not just kmain.
# Parks, so wrap with timeout; exit with Ctrl-a x if running interactively.
panic timeout_s="5":
    cargo build --features panic-selftest
    timeout --foreground {{timeout_s}} {{qemu}} {{qemu_args}} -kernel {{kernel}} || true

# Boot frozen at reset (-S) with the GDB stub on tcp::1234 (-s).
# Then attach from another terminal with `just gdb`, or press F5 in the editor.
debug: build
    {{qemu}} {{qemu_args}} -s -S -kernel {{kernel}}

# Attach gdb-multiarch to a running `just debug` QEMU.
gdb:
    gdb-multiarch {{kernel}} -ex "target remote :1234"

# Headless boot asserting on serial output. `timeout` is a backstop for
# kernels that don't shut themselves down (pre-M0/T0.5 that's expected;
# afterwards a timeout expiry means the clean-exit path regressed).
test expect="OpenSBI" timeout_s="10": build
    @rm -f serial.log
    timeout --foreground {{timeout_s}} {{qemu}} {{qemu_args}} -kernel {{kernel}} > serial.log 2>&1 || true
    @if grep -q '{{expect}}' serial.log; then \
        echo 'TEST PASS: found "{{expect}}"'; \
    else \
        echo 'TEST FAIL: "{{expect}}" not found in serial output:'; \
        echo '----------------------------------------'; \
        cat serial.log; \
        exit 1; \
    fi

# Disassemble the kernel. Extra flags as one quoted arg, e.g. just objdump '-d --source'
objdump flags="-d": build
    cargo objdump -- {{flags}}

# Map an address (e.g. a faulting sepc) to a source line:  just addr2line 0x80200048
addr2line addr: build
    gdb-multiarch -batch -ex "info line *{{addr}}" {{kernel}}
