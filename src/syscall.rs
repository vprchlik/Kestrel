//! Syscall numbers, error codes, and `ecall`-from-U dispatch.
//!
//! Owns the ABI (D-0033): number in `a7`, arguments in `a0`–`a5`, return
//! pair written into the trap frame. Bodies for the five calls live here.
//! `yield` and `exit` return the next Ready frame (D-0032); an unknown
//! number or invalid user pointer kills the task (D-0034).

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::csr;
use crate::println;
use crate::sbi;
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
/// Pointer failed `user_range_ok`.
pub const ERR_INVALID_ADDRESS: isize = -2;
/// `sbrk` past the wall or below `brk_base`.
pub const ERR_NO_MEM: isize = -3;

const _: () = assert!(SYS_RESERVED == 0);
const _: () = assert!(SYS_WRITE + 4 == SYS_YIELD);

static mut USER_OK: bool = false;

/// `ecall` has no compressed encoding: RVC has `c.ebreak` but not
/// `c.ecall` (unprivileged spec 20211203 §16.8, Table 16.5; D-0021,
/// D-0033). Constant 4, no load from `sepc`.
fn advance_ecall(frame: &mut TrapFrame) {
    frame.sepc += 4;
}

/// Dispatch one `ecall` from U. Returns the frame to resume. An unknown
/// number kills the task and reschedules (D-0034).
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
        SYS_EXIT => sys_exit(frame),
        SYS_SBRK => sys_sbrk(frame),
        SYS_GETTIME => sys_gettime(frame),
        SYS_YIELD => sys_yield(frame),
        _ => {
            let id = task::kill_unknown_syscall(num, frame.sepc, 0);
            task::after_exit(id)
        }
    }
}

/// Every invalid pointer kills (D-0034). The two failure shapes are
/// separate feature boots so the kill of one cannot hide the other.
fn kill_invalid_ptr(frame: &mut TrapFrame, ptr: usize, len: usize, why: &str) -> ! {
    frame.set_retval(ERR_INVALID_ADDRESS, 0);
    println!(
        "write: ptr={:#x} len={} {} err={} val=0",
        ptr, len, why, ERR_INVALID_ADDRESS
    );
    task::kill_invalid_user_ptr(frame.sepc, ptr);
    println!("USERPTR OK");
    task::stop_until_scheduler();
}

/// `write(ptr, len) -> count`. Cap is 4 KiB, short count, same as T2.7.
/// Bytes go to DBCN after SUM is down. Invalid pointer: error in the
/// frame, then kill — both shapes, no resume.
fn sys_write(frame: &mut TrapFrame) -> &mut TrapFrame {
    let ptr = frame.a0();
    let len = frame.a1();
    let task = task::current();
    match uaccess::copy_from_user(task, ptr, len) {
        Ok(bytes) => {
            task.writes += 1;
            for &b in bytes {
                sbi::console_write_byte(b);
            }
            advance_ecall(frame);
            frame.set_retval(OK, bytes.len());
            frame
        }
        Err(UserPtrError::SpansPastInterval) => {
            kill_invalid_ptr(frame, ptr, len, "spans past interval")
        }
        Err(UserPtrError::NotInUserInterval) => {
            kill_invalid_ptr(frame, ptr, len, "not in a user interval")
        }
    }
}

fn sys_exit(frame: &mut TrapFrame) -> &mut TrapFrame {
    let id = task::current().id;
    let code = frame.a0();
    task::exit_current(code);
    task::after_exit(id)
}

/// `sbrk(delta) -> old_break`. Moves `task.brk` inside `[brk_base, brk_wall)`.
/// Does not call the frame allocator or the heap (D-0036): the window is
/// already mapped. `delta == 0` queries. Negative shrinks. Past the wall
/// *or* below `brk_base` returns `NO_MEM` with the break unchanged.
fn sys_sbrk(frame: &mut TrapFrame) -> &mut TrapFrame {
    let delta = frame.a0() as isize;
    let task = task::current();
    let old = task.brk;
    let new = match old.checked_add_signed(delta) {
        Some(n) => n,
        None => {
            advance_ecall(frame);
            frame.set_retval(ERR_NO_MEM, old);
            println!("sbrk delta={} err={} brk={:#x} (unchanged)", delta, ERR_NO_MEM, old);
            return frame;
        }
    };
    if new > task.brk_wall || new < task.brk_base {
        advance_ecall(frame);
        frame.set_retval(ERR_NO_MEM, old);
        println!("sbrk delta={} err={} brk={:#x} (unchanged)", delta, ERR_NO_MEM, old);
        return frame;
    }
    task.brk = new;
    advance_ecall(frame);
    frame.set_retval(OK, old);
    println!("sbrk {:#x} -> {:#x}", old, new);
    frame
}

fn sys_gettime(frame: &mut TrapFrame) -> &mut TrapFrame {
    let t = csr::time::read();
    advance_ecall(frame);
    frame.set_retval(OK, t);
    frame
}

/// Advance `sepc` first, then pick the next Ready (D-0032 / PLAN T2.9).
/// Returning without the advance would re-execute `ecall` forever.
fn sys_yield(frame: &mut TrapFrame) -> &mut TrapFrame {
    advance_ecall(frame);
    frame.set_retval(OK, 0);
    task::yield_cpu(frame)
}
