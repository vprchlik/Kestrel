//! User-pointer validation and `SUM`-windowed copies.
//!
//! Owns `user_range_ok` and the only two functions that raise
//! `sstatus.SUM`. Without it a syscall would either fault on a `U=1` page
//! (SUM down) or, worse, succeed at reading kernel `.text` (SUM up: S-mode
//! may then access `U=1` pages *and still* access `U=0` pages). Validation
//! is a software obligation; there is no hardware check to enable.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::csr;
use crate::task::{self, Task};

/// Per-call cap (D-0034). A longer request is truncated to this many bytes
/// and returns a short count, not an error. T2.8 `write` uses the same rule.
pub const COPY_MAX: usize = 4096;

/// Kernel bounce buffer for `copy_from_user`. Static so a 4 KiB copy does
/// not sit on the 8 KiB kstack (the trap frame plus debug `println!` frames
/// already consume a large fraction of it).
static mut FROM_USER: [u8; COPY_MAX] = [0; COPY_MAX];

/// Whether the range may include `.urodata` (read-only user source).
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Access {
    Read,
    Write,
}

/// Why `user_range_ok` rejected a range. The two shapes range checks
/// usually get wrong if they only test one of them.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum UserPtrError {
    /// `ptr` is inside an interval, but `ptr + len` is not (or wrapped).
    SpansPastInterval,
    /// `ptr` itself is in no interval for this task and access.
    NotInUserInterval,
}

fn contains(lo: usize, hi: usize, ptr: usize, end: usize) -> bool {
    // Empty interval contains nothing. Half-open `[lo, hi)`.
    lo < hi && ptr >= lo && end <= hi
}

/// True iff `[ptr, ptr+len)` sits entirely in one of `task`'s static
/// intervals. No page-table walk: the live break is a TCB field, and the
/// whole break window is already mapped `U=1`, so a walk would accept a
/// pointer into unallocated break.
///
/// Intervals:
/// - that task's user stack `[ustack_bottom, ustack_top)`
/// - live break `[brk_base, brk)` — not `brk_wall`
/// - `.udata` and `.ubss`, treated as one interval when adjacent
/// - `.urodata`, only for [`Access::Read`]
pub fn user_range_ok(task: &Task, ptr: usize, len: usize, access: Access) -> bool {
    let Some(end) = ptr.checked_add(len) else {
        return false;
    };
    let s = task::slot(task.id);
    if contains(s.ustack_bottom, s.ustack_top, ptr, end) {
        return true;
    }
    if contains(task.brk_base, task.brk, ptr, end) {
        return true;
    }
    let (d0, d1) = task::udata();
    let (b0, b1) = task::ubss();
    if contains(d0, d1, ptr, end) || contains(b0, b1, ptr, end) {
        return true;
    }
    if d1 == b0 && contains(d0, b1, ptr, end) {
        return true;
    }
    if access == Access::Read {
        let (r0, r1) = task::urodata();
        if contains(r0, r1, ptr, end) {
            return true;
        }
    }
    false
}

fn classify(task: &Task, ptr: usize, len: usize, access: Access) -> Result<(), UserPtrError> {
    if user_range_ok(task, ptr, len, access) {
        return Ok(());
    }
    // A one-byte probe distinguishes "starts valid, runs past the end"
    // from "not in any interval". Wrap of `ptr+len` with a valid `ptr`
    // is the first shape.
    if len > 0 && user_range_ok(task, ptr, 1, access) {
        Err(UserPtrError::SpansPastInterval)
    } else {
        Err(UserPtrError::NotInUserInterval)
    }
}

/// Raise SUM, copy `n` bytes, clear SUM. `n` and the user side of the
/// copy are already validated; this function must not decide anything.
///
/// Sequence: caller validates → raise SUM → memcpy → clear SUM.
/// No policy, no formatting, no `println!` inside the window.
///
/// `copy_nonoverlapping` does not panic. If a validation bug lets this
/// memcpy page-fault, the trap arrives with SUM still set; `panic`
/// clears it before printing. SIE is 0 in S-mode, so a timer cannot
/// land in the window either.
#[inline(never)]
unsafe fn copy_with_sum(dst: *mut u8, src: *const u8, n: usize) {
    csr::sstatus::set(csr::sstatus::SUM);
    core::ptr::copy_nonoverlapping(src, dst, n);
    csr::sstatus::clear(csr::sstatus::SUM);
}

/// Copy up to [`COPY_MAX`] bytes from a user source into the bounce
/// buffer. Longer requests return a short count after copying the cap,
/// provided those capped bytes themselves pass `user_range_ok`. A range
/// that is only partially inside an interval is an error, not a short
/// copy of the valid prefix.
pub fn copy_from_user(task: &Task, src: usize, len: usize) -> Result<usize, UserPtrError> {
    let n = core::cmp::min(len, COPY_MAX);
    classify(task, src, n, Access::Read)?;
    if n == 0 {
        return Ok(0);
    }
    unsafe {
        copy_with_sum(core::ptr::addr_of_mut!(FROM_USER) as *mut u8, src as *const u8, n);
        // Bounce buffer is unread until T2.8 prints it. Without a sink,
        // LLVM could DSE the memcpy and the SUM window would never load
        // user memory. Load after SUM is down: the byte is in kernel .bss.
        let _ = core::hint::black_box(core::ptr::read(core::ptr::addr_of!(FROM_USER[0])));
    }
    Ok(n)
}

/// Copy up to [`COPY_MAX`] bytes from a kernel slice into a user
/// destination. First caller is T2.8 `read`-shaped use (there is none
/// yet); `.urodata` is rejected because this is a write.
#[allow(dead_code)]
pub fn copy_to_user(task: &Task, dst: usize, src: &[u8]) -> Result<usize, UserPtrError> {
    let n = core::cmp::min(src.len(), COPY_MAX);
    classify(task, dst, n, Access::Write)?;
    if n == 0 {
        return Ok(0);
    }
    unsafe {
        copy_with_sum(dst as *mut u8, src.as_ptr(), n);
    }
    Ok(n)
}
