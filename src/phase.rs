//! Boot-to-first-HTTP-byte phase timestamps (D-0043).
//!
//! Owns the static `rdtime` array stamped at `_start`, `stvec`, paging,
//! freeze, first `sret`, `DRIVER_OK`, first RX, listen-ready, and
//! response-TX (E3g). Printed once, after the response is on the wire —
//! DBCN is one `ecall` per byte, so printing on the measured path would
//! move E3g by milliseconds. Without this module M4 has no floor number.

use crate::csr;
use crate::println_always;

pub const N: usize = 9;
pub const START: usize = 0;
pub const STVEC: usize = 1;
pub const PAGING: usize = 2;
#[cfg(not(feature = "no-sret"))]
pub const FREEZE: usize = 3;
#[cfg(not(feature = "no-sret"))]
pub const SRET: usize = 4;
pub const DRIVER_OK: usize = 5;
pub const FIRST_RX: usize = 6;
pub const LISTEN: usize = 7;
pub const E3G: usize = 8;

const NAMES: [&str; N] = [
    "_start",
    "stvec",
    "paging",
    "freeze",
    "sret",
    "DRIVER_OK",
    "first_rx",
    "listen",
    "E3g",
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
    // after DRIVER_OK). Sort by rdtime so delta is the previous event.
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
