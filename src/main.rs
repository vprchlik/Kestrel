//! Kestrel — a minimal rv64gc unikernel for QEMU virt, booted via OpenSBI.
//!
//! OpenSBI enters `_start` in S-mode with `a0` = hartid and `a1` = physical
//! address of the device tree blob. `_start` sets `gp` and `sp`, zeros `.bss`,
//! then calls `kmain`, which prints a hello line over the SBI debug console
//! and `M0 BOOT OK`, then asks OpenSBI to shut the machine down. A panic
//! prints `PANIC at file:line: message` and parks.

#![no_std]
#![no_main]

mod console;
mod sbi;

use core::arch::{asm, global_asm};

// Kernel entry. The linker script places `.text.entry` at 0x8020_0000, the
// address OpenSBI jumps to. Firmware gives us registers and nothing else: no
// stack, no zeroed bss.
//
// The `norelax` dance: `gp` itself is loaded with an absolute address; if the
// assembler were allowed to relax that load into a gp-relative one, it would
// compute gp from the garbage gp we're trying to replace.
//
// `a0`/`a1` are saved across the bss loop so OpenSBI's hartid and DTB pointer
// reach `kmain` even if a future edit of the loop uses those registers.
global_asm!(
    r#"
    .section .text.entry, "ax"
    .globl _start
_start:
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop

    la      sp, __boot_stack_top

    mv      s0, a0
    mv      s1, a1

    la      t0, __bss_start
    la      t1, __bss_end
1:
    bgeu    t0, t1, 2f
    sd      zero, 0(t0)
    addi    t0, t0, 8
    j       1b
2:
    mv      a0, s0
    mv      a1, s1
    call    kmain
3:
    wfi
    j       3b
"#
);

/// Rust entry, called from `_start` with OpenSBI's boot arguments in `a0`/`a1`.
/// Prints hello and `M0 BOOT OK`, then shuts down via SRST. The
/// `panic-selftest` feature panics instead, so the handler stays reachable
/// without editing this file.
#[no_mangle]
extern "C" fn kmain(hartid: usize, dtb_pa: usize) -> ! {
    sbi::require_dbcn();
    println!("kestrel: hello from hart {}, dtb at {:#x}", hartid, dtb_pa);
    #[cfg(feature = "panic-selftest")]
    panic!("selftest");
    #[cfg(not(feature = "panic-selftest"))]
    {
        println!("M0 BOOT OK");
        let ret = sbi::shutdown();
        println!(
            "shutdown failed: SRST error={} value={}",
            ret.error, ret.value
        );
        park()
    }
}

/// Set for the duration of `panic`. If we re-enter, `println!` is already on
/// the stack — printing again would recurse until the stack dies. Single hart,
/// not a lock: a `bool` in `.bss`, zeroed by `_start`.
static mut IN_PANIC: bool = false;

/// Required by `no_std`: where `core` lands when an invariant fails.
/// Prints location and message, then parks. Nested panics `ebreak` and park
/// without printing.
#[panic_handler]
fn panic(info: &core::panic::PanicInfo) -> ! {
    let nested = unsafe {
        if IN_PANIC {
            true
        } else {
            IN_PANIC = true;
            false
        }
    };
    if nested {
        // Already printing a panic. Do not call println! or panic! again.
        unsafe {
            asm!(
                "ebreak",
                "1: wfi",
                "j 1b",
                options(noreturn),
            );
        }
    }

    match info.location() {
        Some(loc) => {
            println!("PANIC at {}:{}: {}", loc.file(), loc.line(), info.message())
        }
        None => println!("PANIC at ?:?: {}", info.message()),
    }
    park()
}

fn park() -> ! {
    loop {
        unsafe { asm!("wfi") };
    }
}
