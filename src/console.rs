//! Kernel console: `print!` / `println!` over the SBI debug console.
//!
//! Turns `core::fmt` arguments into bytes and hands each one to DBCN
//! `write_byte`. This is the only formatted-output path; without it there is
//! no kernel logging. Single hart, no interrupts yet — no lock.

use crate::sbi;
use core::fmt::{self, Write};

struct Console;

impl Write for Console {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        for byte in s.bytes() {
            sbi::console_write_byte(byte);
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
