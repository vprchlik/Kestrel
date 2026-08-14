//! User-mode code, linked into `.utext` / `.urodata` / `.udata` / `.ubss`.
//!
//! Owns every instruction that runs with `sstatus.SPP = U`. Every symbol
//! referenced from `.utext` must resolve inside the user sections or the
//! task's own stack/break window (`just check-utext`). Written in assembly
//! so the objdump of `.utext` is the acceptance check, not a hope about LLVM.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use core::arch::global_asm;

#[cfg(all(feature = "userptr-kernel-selftest", feature = "userptr-span-selftest"))]
compile_error!("pick one of userptr-kernel-selftest, userptr-span-selftest");

extern "C" {
    fn user_entry();
}

/// Address of the task-0 body. Goes into the fabricated frame's `sepc`.
pub fn entry() -> usize {
    user_entry as *const () as usize
}

// `norelax` / `norvc`: no gp-relative rewrite, no compressed `c.j`.
// `write(ptr, len)` is a0/a1 (D-0033: no fd).

#[cfg(not(any(
    feature = "userptr-kernel-selftest",
    feature = "userptr-span-selftest"
)))]
global_asm!(
    r#"
    .section .utext, "ax"
    .option norelax
    .option norvc
    .balign 4
    .globl user_entry
user_entry:
    # gettime, write from .urodata, gettime. Delta must be > 0 and < 10 ms
    # (PERIOD = 100_000 at 10 MHz, D-0018).
    addi    a7, zero, 4
    ecall
    mv      s2, a1
    la      a0, msg_hello
    addi    a1, zero, 20
    addi    a7, zero, 1
    ecall
    addi    a7, zero, 4
    ecall
    sub     t0, a1, s2
    beqz    t0, fail
    lui     t1, 0x18
    addi    t1, t1, 0x6A0
    bgeu    t0, t1, fail

    # sbrk(0) query, then grow 4 KiB. Use the new memory (store + write).
    addi    a0, zero, 0
    addi    a7, zero, 3
    ecall
    mv      s0, a1
    addi    t0, zero, 1
    slli    t0, t0, 12
    mv      a0, t0
    addi    a7, zero, 3
    ecall
    bne     a0, zero, fail
    bne     a1, s0, fail
    add     s3, s0, t0
    addi    t1, zero, 0x41
    sb      t1, 0(s0)
    addi    t1, zero, 0x0a
    sb      t1, 1(s0)
    mv      a0, s0
    addi    a1, zero, 2
    addi    a7, zero, 1
    ecall

    # Past the wall: +64 KiB from here exceeds brk_wall. NO_MEM, unchanged.
    slli    a0, t0, 4
    addi    a7, zero, 3
    ecall
    addi    t1, zero, -3
    bne     a0, t1, fail
    bne     a1, s3, fail
    addi    a0, zero, 0
    addi    a7, zero, 3
    ecall
    bne     a1, s3, fail

    # Below brk_base: -4097 from base+4096. Same error, break unchanged.
    addi    t1, t0, 1
    sub     a0, zero, t1
    addi    a7, zero, 3
    ecall
    addi    t1, zero, -3
    bne     a0, t1, fail
    bne     a1, s3, fail
    addi    a0, zero, 0
    addi    a7, zero, 3
    ecall
    bne     a1, s3, fail

    # Negative shrink that stays in the window: back to brk_base.
    sub     a0, zero, t0
    addi    a7, zero, 3
    ecall
    bne     a0, zero, fail
    bne     a1, s3, fail

    # yield: no-op until T2.9. Must not be a marker.
    addi    a7, zero, 5
    ecall
    bne     a0, zero, fail

    la      a0, msg_sbrk_ok
    addi    a1, zero, 8
    addi    a7, zero, 1
    ecall
    addi    a0, zero, 0
    addi    a7, zero, 2
    ecall
fail:
    addi    a7, zero, 99
    ecall
    unimp

    .section .urodata, "a"
    .balign 4
msg_hello:
    .ascii  "hello from .urodata\n"
msg_sbrk_ok:
    .ascii  "SBRK OK\n"
"#
);

#[cfg(feature = "userptr-kernel-selftest")]
global_asm!(
    r#"
    .section .utext, "ax"
    .option norelax
    .option norvc
    .balign 4
    .globl user_entry
user_entry:
    # write(0x80200000, 8): kernel .text, in no user interval.
    # RV64 `lui rd, 0x80200` sign-extends bit 31 — see GLOSSARY.
    lui     a0, 0x80
    addi    a0, a0, 0x200
    slli    a0, a0, 12
    addi    a1, zero, 8
    addi    a7, zero, 1
    ecall
    unimp
"#
);

#[cfg(feature = "userptr-span-selftest")]
global_asm!(
    r#"
    .section .utext, "ax"
    .option norelax
    .option norvc
    .balign 4
    .globl user_entry
user_entry:
    # write(sp-8, 16): starts on the stack, runs past ustack_top.
    addi    a0, sp, -8
    addi    a1, zero, 16
    addi    a7, zero, 1
    ecall
    unimp
"#
);
