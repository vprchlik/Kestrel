//! User-mode code, linked into `.utext` / `.urodata` / `.udata` / `.ubss`.
//!
//! Owns every instruction that runs with `sstatus.SPP = U`. The compiler is
//! not allowed to emit a call, a `gp`/`tp`-relative access, or a load from
//! kernel `.rodata` here: S-mode cannot fetch `U=1` pages, so a helper left
//! in kernel `.text` is an instruction page fault at the call target, and a
//! string left in kernel `.rodata` is a load page fault. Written in
//! assembly so the objdump of `.utext` is the acceptance check, not a hope
//! about LLVM.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use core::arch::global_asm;

extern "C" {
    fn user_entry();
}

/// Address of the task-0 body. Goes into the fabricated frame's `sepc`.
pub fn entry() -> usize {
    user_entry as *const () as usize
}

// `norelax` / `norvc`: no gp-relative rewrite, no compressed `c.j` that
// would make the objdump harder to audit. `ecall` has no compressed form
// (D-0033). Several numbered calls then 99, which the kernel kills.
// `unimp` is only reached if the kill fails to stop the task.
global_asm!(
    r#"
    .section .utext, "ax"
    .option norelax
    .option norvc
    .balign 4
    .globl user_entry
user_entry:
    # write(1, 8): a0 is nonzero so a missed frame-write of the return
    # pair would bounce the argument back into U-mode a0.
    addi    a0, zero, 1
    addi    a1, zero, 8
    addi    a7, zero, 1
    ecall
    addi    a7, zero, 4
    ecall
    addi    a7, zero, 99
    ecall
    unimp
"#
);
