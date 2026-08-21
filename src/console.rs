//! Kernel console: `print!` / `println!` over the SBI debug console.
//!
//! Turns `core::fmt` arguments into bytes and hands each one to DBCN
//! `write_byte`. This is the only formatted-output path; without it there is
//! no kernel logging. Single hart, no interrupts yet — no lock.

use crate::sbi;
use core::fmt::{self, Write};

struct Console;

/// D-0079 seam (bios-none lane): polled NS16550A TX in S-mode, the
/// D-0004 decision revisited for this variant only. Works pre-paging
/// under Bare translation and post-paging through the D-0079 UART
/// mapping — and the mapping is software-verified *before* `satp` is
/// written, because a missing mapping here is the one failure that
/// cannot print its own panic (the panic path is this function).
/// "Ran" = bytes appear; "worked" = every marker greps exactly — a
/// dropped byte breaks a `TEST PASS` grep, which is why the gates
/// compare content, not presence.
#[cfg(feature = "bios-none")]
pub(crate) fn put_byte(byte: u8) {
    const UART: usize = 0x1000_0000; // NS16550A on QEMU virt
    const LSR: usize = 5;
    const LSR_THRE: u8 = 0x20;
    unsafe {
        while core::ptr::read_volatile((UART + LSR) as *const u8) & LSR_THRE == 0 {}
        core::ptr::write_volatile(UART as *mut u8, byte);
    }
}

#[cfg(not(feature = "bios-none"))]
pub(crate) fn put_byte(byte: u8) {
    sbi::console_write_byte(byte);
}

impl Write for Console {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        for byte in s.bytes() {
            put_byte(byte);
        }
        Ok(())
    }
}

pub fn print_fmt(args: fmt::Arguments) {
    let mut console = Console;
    if console.write_fmt(args).is_err() {
        sbi::abort_sbi(sbi::SBI_ERR_NOT_SUPPORTED);
    }
}

/// Always reaches DBCN. Panic, the phase block, and `M3 UNIKERNEL OK`
/// use this so `fast-boot` can compile out ordinary `println!`.
#[macro_export]
macro_rules! println_always {
    () => {
        $crate::console::print_fmt(core::format_args!("\n"))
    };
    ($($arg:tt)*) => {
        $crate::console::print_fmt(core::format_args!("{}\n", format_args!($($arg)*)))
    };
}

#[macro_export]
macro_rules! print {
    ($($arg:tt)*) => {
        #[cfg(not(feature = "fast-boot"))]
        {
            $crate::console::print_fmt(core::format_args!($($arg)*))
        }
    };
}

#[macro_export]
macro_rules! println {
    () => {
        $crate::print!("\n")
    };
    ($($arg:tt)*) => {
        $crate::print!("{}\n", format_args!($($arg)*))
    };
}
