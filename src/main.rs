//! Kestrel — a minimal rv64gc unikernel for QEMU virt, booted via OpenSBI.
//!
//! Pre-M0 scaffold: this image does nothing but park the hart. OpenSBI enters
//! `_start` in S-mode with `a0` = hartid and `a1` = physical address of the
//! device tree blob; we set up a stack and sleep in `wfi`. Console output,
//! bss clearing, and clean shutdown are M0 tasks (docs/PLAN.md) — per project
//! rules, nothing beyond the current milestone is implemented.

#![no_std]
#![no_main]

use core::arch::{asm, global_asm};

// Kernel entry. The linker script places `.text.entry` at 0x8020_0000, the
// address OpenSBI jumps to. Firmware gives us registers and nothing else: no
// stack, no zeroed bss. Here we only set `gp` and `sp`, then enter Rust.
//
// The `norelax` dance: `gp` itself is loaded with an absolute address; if the
// assembler were allowed to relax that load into a gp-relative one, it would
// compute gp from the garbage gp we're trying to replace.
//
// `.bss` zeroing is deliberately absent — that is task M0/T0.2.
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
    call    kmain
"#
);

/// Rust entry, called from `_start` with OpenSBI's boot arguments passed
/// through untouched in `a0`/`a1` (unused until M0 prints them).
/// Parks forever: `wfi` sleeps the hart until an interrupt would fire, and
/// none is ever enabled — an idle loop that costs no host CPU.
#[no_mangle]
extern "C" fn kmain(_hartid: usize, _dtb_pa: usize) -> ! {
    park()
}

/// Required by `no_std`: where `core` lands when an invariant fails.
/// Pre-M0 there is no console, so parking is the only honest option;
/// M0/T0.4 replaces this with a loud print of location and message.
#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    park()
}

fn park() -> ! {
    loop {
        unsafe { asm!("wfi") };
    }
}
