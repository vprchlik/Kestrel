//! U-mode syscall wrappers (D-0044, D-0051).
//!
//! Owns the numbers and the `ecall` sequences the app is allowed to
//! execute. Bodies are `#[link_section = ".utext"]` so they land in the
//! user text even if the archive match in `linker.ld` is picky; they
//! must not reference kernel symbols. Without this crate the app would
//! inline raw `a7` numbers and the ABI would have two sources of truth.

#![no_std]
#![no_builtins]

use core::arch::asm;

/// 0 is reserved and invalid (D-0033).
pub const SYS_RESERVED: usize = 0;
pub const SYS_WRITE: usize = 1;
pub const SYS_EXIT: usize = 2;
pub const SYS_SBRK: usize = 3;
pub const SYS_GETTIME: usize = 4;
pub const SYS_YIELD: usize = 5;
pub const SYS_RECV: usize = 6;
pub const SYS_SEND: usize = 7;

pub const OK: isize = 0;
pub const ERR_INVALID_PARAM: isize = -1;
pub const ERR_INVALID_ADDRESS: isize = -2;
pub const ERR_NO_MEM: isize = -3;
/// `recv` had nothing waiting after polling the NIC (D-0040).
pub const ERR_AGAIN: isize = -4;

/// `send` flags: bit 0 is FIN. UDP ignores it (D-0051); TCP honors it
/// at T3.10.
pub const SEND_FIN: usize = 1;

const _: () = assert!(SYS_RESERVED == 0);
const _: () = assert!(SYS_WRITE + 6 == SYS_SEND);

/// One `ecall`. Number in `a7`, args in `a0`–`a2`, pair back in `a0`/`a1`
/// (D-0033). `nostack`: we do not touch memory the kernel would have to
/// map; the trap frame is the kernel's problem.
#[inline(never)]
#[link_section = ".utext"]
unsafe fn ecall3(num: usize, a0: usize, a1: usize, a2: usize) -> (isize, usize) {
    let err: isize;
    let val: usize;
    asm!(
        "ecall",
        inlateout("a0") a0 => err,
        inlateout("a1") a1 => val,
        in("a2") a2,
        in("a7") num,
        options(nostack),
    );
    (err, val)
}

#[inline(never)]
#[link_section = ".utext"]
pub unsafe fn write(ptr: *const u8, len: usize) -> (isize, usize) {
    ecall3(SYS_WRITE, ptr as usize, len, 0)
}

#[inline(never)]
#[link_section = ".utext"]
pub fn exit(code: usize) -> ! {
    unsafe {
        let _ = ecall3(SYS_EXIT, code, 0, 0);
    }
    // Kernel `exit` does not resume. If it did, stop without calling
    // anything in kernel `.text`.
    loop {
        unsafe { asm!("unimp", options(nomem, nostack)) };
    }
}

#[inline(never)]
#[link_section = ".utext"]
pub unsafe fn recv(ptr: *mut u8, len: usize) -> (isize, usize) {
    ecall3(SYS_RECV, ptr as usize, len, 0)
}

#[inline(never)]
#[link_section = ".utext"]
pub unsafe fn send(ptr: *const u8, len: usize, flags: usize) -> (isize, usize) {
    ecall3(SYS_SEND, ptr as usize, len, flags)
}
