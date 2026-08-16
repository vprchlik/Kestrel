//! User-mode code, linked into `.utext` / `.urodata` / `.udata` / `.ubss`.
//!
//! Owns every instruction that runs with `sstatus.SPP = U`. Every symbol
//! referenced from `.utext` must resolve inside the user sections or the
//! task's own stack/break window (`just check-utext`). The T2 demo tasks
//! stay hand-written assembly (`.option norvc`). The M3 app is compiled
//! Rust in the `app` / `usys` crates; the linker maps those archives here
//! (D-0044 / D-0051).

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

#[allow(unused_imports)]
use core::arch::global_asm;

/// Pull the app archive into every image, including boots that never
/// `sret` into it. Without this, `--gc-sections` drops `app_main` and
/// `check-utext` would pass on assembly-only `.utext` while the UDP
/// image was the only one that actually linked compiled Rust.
#[used]
static KEEP_APP: extern "C" fn() -> ! = app::app_main;

/// Entry of the compiled app crate (D-0044).
#[allow(dead_code)]
pub fn app() -> usize {
    app::app_main as *const () as usize
}

#[cfg(feature = "utext-c-fld-selftest")]
extern "C" {
    fn plant_c_fld();
}

#[cfg(feature = "utext-c-fld-selftest")]
#[used]
static KEEP_PLANT_C_FLD: unsafe extern "C" fn() = plant_c_fld;

#[cfg(all(feature = "userptr-kernel-selftest", feature = "userptr-span-selftest"))]
compile_error!("pick one of userptr-kernel-selftest, userptr-span-selftest");

#[cfg(all(
    feature = "user-fault-selftest",
    any(feature = "userptr-kernel-selftest", feature = "userptr-span-selftest")
))]
compile_error!("user-fault-selftest is exclusive of the userptr selftests");

#[cfg(any(feature = "userptr-kernel-selftest", feature = "userptr-span-selftest"))]
extern "C" {
    fn user_entry();
}

#[cfg(feature = "user-fault-selftest")]
extern "C" {
    fn task1_entry();
    fn task2_entry();
}

#[cfg(any(feature = "userptr-kernel-selftest", feature = "userptr-span-selftest"))]
pub fn entry() -> usize {
    user_entry as *const () as usize
}

#[cfg(feature = "user-fault-selftest")]
pub fn task1() -> usize {
    task1_entry as *const () as usize
}

#[cfg(feature = "user-fault-selftest")]
pub fn task2() -> usize {
    task2_entry as *const () as usize
}

// T2.10: task 1 is the T2.9 survivor; task 2 takes a load page fault from
// VA 0. Cause 13 is delegated (MEDELEG bit 13). `unimp` is code 2, which
// is *not* delegated — it would dump in OpenSBI and never reach us. The
// trailing `unimp` here is only reached if the kill fails to contain the
// fault.
#[cfg(feature = "user-fault-selftest")]
global_asm!(
    r#"
    .section .utext, "ax"
    .option norelax
    .option norvc
    .balign 4
    .globl task1_entry
task1_entry:
    addi    s1, zero, 1
    j       task_body
    .globl task2_entry
task2_entry:
    ld      a0, 0(zero)
    unimp

task_body:
    addi    a7, zero, 4
    ecall
    mv      s2, a1
    lui     s3, 0xc
    addi    s3, s3, 0x350
    lui     s4, 0x31
    addi    s4, s4, -0x2C0
    lui     s5, 0xc
    addi    s5, s5, 0x350
loop:
    addi    a7, zero, 4
    ecall
    sub     t0, a1, s2
    bltu    t0, s3, loop
    addi    t1, zero, 1
    beq     s1, t1, wr1
    la      a0, msg2
    j       wr
wr1:
    la      a0, msg1
wr:
    addi    a1, zero, 16
    addi    a7, zero, 1
    ecall
    add     s3, s3, s5
    bltu    s4, s3, done
    j       loop
done:
    addi    a0, zero, 0
    addi    a7, zero, 2
    ecall
    unimp

    .section .urodata, "a"
    .balign 4
msg1:
    .ascii  "task 1 progress\n"
msg2:
    .ascii  "task 2 progress\n"
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
    addi    a0, sp, -8
    addi    a1, zero, 16
    addi    a7, zero, 1
    ecall
    unimp
"#
);

// D-0044: compressed FP in `.utext` must fail `check-utext` by name.
// `.option rvc` so this is actually `c.fld`, not a 32-bit `fld`.
#[cfg(feature = "utext-c-fld-selftest")]
global_asm!(
    r#"
    .section .utext, "ax"
    .option rvc
    .balign 2
    .globl plant_c_fld
plant_c_fld:
    c.fld   fs0, 0(a0)
"#
);
