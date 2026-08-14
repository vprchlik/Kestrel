//! Syscall numbers, error codes, and `ecall`-from-U dispatch.
//!
//! Owns the ABI (D-0033): number in `a7`, arguments in `a0`–`a5`, return
//! pair written into the trap frame. `write` validates and copies through
//! the SUM window (T2.7); bodies for the other four arrive at T2.8. An
//! unknown number kills the task (D-0034) rather than panicking the kernel.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::csr;
use crate::println;
use crate::task;
use crate::trap::TrapFrame;
use crate::uaccess::{self, UserPtrError};

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
    // A leaked SUM window would make every subsequent kernel load of a
    // U=1 page succeed. The copy path drops SUM before returning here.
    if csr::sstatus::read() & csr::sstatus::SUM != 0 {
        panic!("SUM set at syscall dispatcher entry");
    }

    if !unsafe { USER_OK } {
        println!("USER OK");
        unsafe { USER_OK = true };
    }

    let num = frame.a7();
    match num {
        SYS_WRITE => sys_write(frame),
        SYS_EXIT | SYS_SBRK | SYS_GETTIME | SYS_YIELD => {
            // `ecall` has no compressed encoding: RVC has `c.ebreak` but
            // not `c.ecall` (unprivileged spec 20211203 §16.8, Table 16.5;
            // D-0021, D-0033). Constant 4, no load from `sepc`, so this
            // path never reads user memory and never needs `sstatus.SUM`.
            frame.sepc += 4;
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

/// T2.7: validate + copy. T2.8 prints the bytes to the console. The cap
/// and the two failure shapes are the contract T2.8 `write` must keep:
/// oversize → short count (`min(len, COPY_MAX)`), not an error; a range
/// that is only partly in an interval → `ERR_INVALID_ADDRESS`, not a
/// short copy of the valid prefix.
fn sys_write(frame: &mut TrapFrame) -> &mut TrapFrame {
    let ptr = frame.a0();
    let len = frame.a1();
    let task = task::current();
    match uaccess::copy_from_user(task, ptr, len) {
        Ok(n) => {
            frame.sepc += 4;
            frame.set_retval(OK, n);
            println!("syscall 1 write err=0 val={}", n);
            frame
        }
        Err(UserPtrError::SpansPastInterval) => {
            // D-0034 kills on any invalid pointer. With one task and no
            // scheduler, killing here would skip the kernel-address case
            // in the same boot. Resume with the error so both shapes run;
            // T2.9's reschedule site is where the span kill belongs.
            frame.sepc += 4;
            frame.set_retval(ERR_INVALID_ADDRESS, 0);
            println!(
                "write: ptr={:#x} len={} spans past interval err={} val=0",
                ptr, len, ERR_INVALID_ADDRESS
            );
            frame
        }
        Err(UserPtrError::NotInUserInterval) => {
            frame.set_retval(ERR_INVALID_ADDRESS, 0);
            println!(
                "write: ptr={:#x} len={} not in a user interval err={} val=0",
                ptr, len, ERR_INVALID_ADDRESS
            );
            task::kill_invalid_user_ptr(frame.sepc, ptr);
            println!("USERPTR OK");
            task::stop_until_scheduler();
        }
    }
}
