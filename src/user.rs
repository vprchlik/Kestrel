//! User-mode code, linked into `.utext` / `.urodata` / `.udata` / `.ubss`.
//!
//! Owns every instruction that runs with `sstatus.SPP = U`. Every symbol
//! referenced from `.utext` must resolve inside the user sections or the
//! task's own stack/break window (`just check-utext`). S-mode cannot fetch
//! `U=1` pages, so a helper left in kernel `.text` is an instruction page
//! fault at the call target, and a string left in kernel `.rodata` is a
//! load page fault. Written in assembly so the objdump of `.utext` is the
//! acceptance check, not a hope about LLVM.

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
// (D-0033). `write(ptr, len)` is a0/a1 (D-0033: no fd).
//
// Four calls: valid stack source (SUM window), cap → short count, span
// past the stack top, then a kernel address. The `lui` of 0x80200000 is
// a *value*, not a symbol reference — check-utext must allow it.
// `unimp` is only reached if the kill fails to stop the task.
global_asm!(
    r#"
    .section .utext, "ax"
    .option norelax
    .option norvc
    .balign 4
    .globl user_entry
user_entry:
    # write(sp-16, 8): in the user stack. Exercises SUM.
    addi    a0, sp, -16
    addi    a1, zero, 8
    addi    a7, zero, 1
    ecall

    # write(sp-4096, 5000): cap is 4 KiB → short count 4096, not an error.
    addi    t0, zero, 1
    slli    t0, t0, 12
    sub     a0, sp, t0
    addi    a1, t0, 904
    addi    a7, zero, 1
    ecall

    # write(sp-8, 16): starts on the stack, runs 8 bytes past ustack_top
    # into the (empty) live break. Error return; resume so the kernel-
    # address kill can run in the same boot (no scheduler yet).
    addi    a0, sp, -8
    addi    a1, zero, 16
    addi    a7, zero, 1
    ecall

    # write(0x80200000, 8): kernel .text, in no user interval. Error
    # return and kill. lui immediate is a value, not a relocation.
    lui     a0, 0x80200
    addi    a1, zero, 8
    addi    a7, zero, 1
    ecall
    unimp
"#
);
