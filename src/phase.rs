//! Boot-to-first-HTTP-byte phase timestamps (D-0043 / D-0057).
//!
//! Owns the static `rdtime` array stamped along the attributed boot
//! path. Printed once, after the response is on the wire — DBCN is one
//! `ecall` per byte, so printing on the measured path would move E3g by
//! milliseconds. Without this module M4 has no floor number.
//!
//! `NAMES` is the kernel's list. The three justfile HTTP gates share
//! one `phase_names` variable (finding 26). The T4.1 harness parses
//! whatever serial prints and is not a fourth copy.

use crate::csr;
use crate::println_always;

pub const N: usize = 22;
pub const START: usize = 0;
pub const STAMP_A: usize = 1;
pub const STAMP_B: usize = 2;
pub const STVEC: usize = 3;
#[cfg(not(any(feature = "panic-selftest", feature = "hang-selftest")))]
pub const FRAME_INIT: usize = 4;
#[cfg(not(any(feature = "panic-selftest", feature = "hang-selftest")))]
pub const TASK_INIT: usize = 5;
pub const PAGE_BUILD: usize = 6;
pub const PAGE_VERIFY: usize = 7;
pub const ACTIVATE: usize = 8;
pub const VIRTQ_INIT: usize = 9;
pub const DRIVER_OK: usize = 10;
pub const FIRST_RX: usize = 11;
pub const SERVING_READY: usize = 12;
pub const NET_INIT_DONE: usize = 13;
#[cfg(not(any(feature = "panic-selftest", feature = "hang-selftest")))]
pub const HEAP_INIT: usize = 14;
#[cfg(not(feature = "no-sret"))]
pub const ACCOUNTING: usize = 15;
#[cfg(not(feature = "no-sret"))]
pub const FREEZE: usize = 16;
#[cfg(not(feature = "no-sret"))]
pub const SRET: usize = 17;
pub const SYN_RX: usize = 18;
pub const ESTABLISHED: usize = 19;
pub const E3G: usize = 20;
pub const E3G_DOORBELL: usize = 21;

const NAMES: [&str; N] = [
    "_start",
    "stamp_a",
    "stamp_b",
    "stvec",
    "frame_init",
    "task_init",
    "page_build",
    "page_verify",
    "activate",
    "virtq_init",
    "DRIVER_OK",
    "first_rx",
    "serving_ready",
    "net_init_done",
    "heap_init",
    "accounting",
    "freeze",
    "sret",
    "syn_rx",
    "established",
    "E3g",
    "E3g_doorbell",
];

/// Filled from `_start` (index 0) after `.bss` is zeroed, from the
/// `rdtime` taken as the first kernel instruction. Other slots via
/// [`stamp`]. `#[no_mangle]` so the asm store can name it.
#[no_mangle]
pub static mut PHASE_STAMPS: [usize; N] = [0; N];

static mut PRINTED: bool = false;

pub fn stamp(i: usize) {
    if i >= N {
        panic!("phase: index {i} >= {N}");
    }
    unsafe {
        if PHASE_STAMPS[i] == 0 {
            PHASE_STAMPS[i] = csr::time::read();
        }
    }
}

pub fn get(i: usize) -> usize {
    unsafe { PHASE_STAMPS[i] }
}

/// Once, after the HTTP response TX completes. Always prints (fast-boot
/// compiles out ordinary `println!`).
pub fn print_after_response() {
    if unsafe { PRINTED } {
        return;
    }
    if get(E3G) == 0 {
        return;
    }
    unsafe { PRINTED = true };
    println_always!("PHASE ticks (10 MHz, 100 ns/tick); ns = ticks * 100");
    let t0 = get(START);
    // Stamp order in the array is not wall-clock order (freeze/sret are
    // after DRIVER_OK; syn_rx can land during the ping wait). Sort by
    // rdtime so delta is the previous event.
    let mut order = [0usize; N];
    let mut n = 0usize;
    for i in 0..N {
        let t = get(i);
        if t == 0 {
            println_always!("PHASE {} unset", NAMES[i]);
            continue;
        }
        order[n] = i;
        n += 1;
    }
    let mut i = 1;
    while i < n {
        let mut j = i;
        while j > 0 && get(order[j]) < get(order[j - 1]) {
            order.swap(j, j - 1);
            j -= 1;
        }
        i += 1;
    }
    let mut last = t0;
    for k in 0..n {
        let i = order[k];
        let t = get(i);
        let since0 = t.wrapping_sub(t0);
        let delta = t.wrapping_sub(last);
        println_always!(
            "PHASE {} ticks={} ns={} since_start={} ns={} delta={} ns={}",
            NAMES[i],
            t,
            t.wrapping_mul(crate::timer::TICK_NS),
            since0,
            since0.wrapping_mul(crate::timer::TICK_NS),
            delta,
            delta.wrapping_mul(crate::timer::TICK_NS)
        );
        last = t;
    }
}
