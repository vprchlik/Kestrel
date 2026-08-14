//! Syscall numbers, error codes, and `ecall`-from-U dispatch.
//!
//! Owns the ABI (D-0033): number in `a7`, arguments in `a0`–`a5`, return
//! pair written into the trap frame. Bodies for 1–5 arrive at T2.8; this
//! module is the dispatch and the unknown-number kill (D-0034). Without it
//! an `ecall` from U is an unknown trap and panics the kernel.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::println;
use crate::task;
use crate::trap::TrapFrame;

/// 0 is reserved and invalid (D-0033): a zeroed `a7` is not `write`.
pub const SYS_RESERVED: usize = 0;
pub const SYS_WRITE: usize = 1;
pub const SYS_EXIT: usize = 2;
pub const SYS_SBRK: usize = 3;
pub const SYS_GETTIME: usize = 4;
pub const SYS_YIELD: usize = 5;

/// Success. Same *shape* as SBI (`a0 == 0`), not SBI's numeric list (D-0033).
pub const OK: isize = 0;
/// T2.8: bad argument (e.g. `write` length).
#[allow(dead_code)]
pub const ERR_INVALID_PARAM: isize = -1;
/// T2.7 / T2.8: pointer failed `user_range_ok`.
#[allow(dead_code)]
pub const ERR_INVALID_ADDRESS: isize = -2;
/// T2.8: `sbrk` past the wall.
#[allow(dead_code)]
pub const ERR_NO_MEM: isize = -3;

const _: () = assert!(SYS_RESERVED == 0);
const _: () = assert!(SYS_WRITE + 4 == SYS_YIELD);

static mut USER_OK: bool = false;

/// Dispatch one `ecall` from U. Returns the frame to resume, or never
/// returns after killing the task and shutting down (no scheduler yet).
pub fn from_ecall(frame: &mut TrapFrame) -> &mut TrapFrame {
    if !unsafe { USER_OK } {
        println!("USER OK");
        unsafe { USER_OK = true };
    }

    let num = frame.a7();
    match num {
        SYS_WRITE | SYS_EXIT | SYS_SBRK | SYS_GETTIME | SYS_YIELD => {
            // `ecall` has no compressed encoding: RVC has `c.ebreak` but
            // not `c.ecall` (unprivileged spec 20211203 §16.8, Table 16.5;
            // D-0021, D-0033). Constant 4, no load from `sepc`, so this
            // path never reads user memory and never needs `sstatus.SUM`.
            frame.sepc += 4;
            // T2.8 fills these in. Stub so the ABI is testable: the
            // epilogue restores a0/a1 from the frame, so these stores are
            // the only ones that reach U-mode.
            frame.set_retval(OK, num);
            println!("syscall {} stub err=0 val={}", num, num);
            frame
        }
        _ => {
            task::kill_unknown_syscall(num, frame.sepc, 0);
            println!("SYSCALL OK");
            task::stop_until_scheduler();
        }
    }
}
