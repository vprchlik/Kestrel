//! Supervisor timer: 10 ms ticks via SBI TIME (D-0018).
//!
//! Owns the tick counter and the only site that programs the next deadline
//! (`arm`). Trap dispatch calls `on_interrupt`; without this module there
//! are no timer interrupts and `sip.STIP` can never be acknowledged from
//! S-mode. M4's Sstc comparison replaces the body of `arm`, nothing else.

use crate::csr;
use crate::println;
use crate::sbi;

/// QEMU `virt` timebase is 10 MHz (`aclint-mtimer @ 10000000Hz`). PLAN T1.3 /
/// D-0018. One `rdtime` tick is 100 ns; M4 reuses these constants.
pub const TIMEBASE_HZ: usize = 10_000_000;
/// Nanoseconds per `rdtime` tick at `TIMEBASE_HZ`.
pub const TICK_NS: usize = 100;
/// 100_000 ticks = 10 ms.
pub const PERIOD: usize = 100_000;
const _: () = assert!(TICK_NS == 1_000_000_000 / TIMEBASE_HZ);
const _: () = assert!(PERIOD == TIMEBASE_HZ / 100);
/// 1 ms at the same timebase. Stress widens the interrupt-during-alloc window.
#[cfg(feature = "stress")]
pub const PERIOD_1MS: usize = 10_000;

static mut PERIOD_NOW: usize = PERIOD;

static mut TICKS: usize = 0;

/// Probe TIME, arm the first deadline, then enable `sie.STIE` and
/// `sstatus.SIE`. Arm *before* SIE so a leftover `STIP` cannot fire with
/// no future deadline programmed.
pub fn init() {
    sbi::require_time();
    csr::sie::set(csr::sie::STIE);
    arm();
    csr::sstatus::set(csr::sstatus::SIE);
}

/// The one arm site (D-0018). Next deadline is `rdtime() + PERIOD`, not
/// `last_deadline + PERIOD`: a long handler cannot leave the comparator in
/// the past (`STIP` stuck, interrupt storm). The cost is a few microseconds
/// of drift per tick, which 10 ms ticks do not care about.
///
/// This call is also the STIP acknowledgement. `sip.STIP` is not
/// write-clearable from S-mode; OpenSBI clears it when the new deadline is
/// in the future.
pub fn arm() {
    sbi::set_timer(csr::time::read().wrapping_add(unsafe { PERIOD_NOW }));
}

/// Next `arm` uses this many `rdtime` ticks. Re-arms immediately so the new
/// period takes effect. Stress only; default boot never calls this.
#[cfg(feature = "stress")]
pub fn set_period(ticks: usize) {
    if ticks == 0 {
        panic!("timer period 0");
    }
    unsafe { PERIOD_NOW = ticks };
    arm();
}

/// Tick count. Volatile: the compiler cannot see that `__trap_entry`
/// writes this, and a `wfi` wait loop would otherwise CSE a single load.
#[cfg(any(not(feature = "fast-boot"), feature = "stress"))]
#[cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]
pub fn ticks() -> usize {
    unsafe { core::ptr::read_volatile(&raw const TICKS) }
}

/// Supervisor-timer interrupt. Re-arm (ack STIP) before printing so a slow
/// DBCN `ecall` cannot delay the acknowledgement until after `sret`.
pub fn on_interrupt() {
    let n = unsafe {
        let n = core::ptr::read_volatile(&raw const TICKS).wrapping_add(1);
        core::ptr::write_volatile(&raw mut TICKS, n);
        n
    };
    arm();
    if n <= 3 || n % 10 == 0 {
        println!("tick {}", n);
    }
}
