//! Kestrel — a minimal rv64gc unikernel for QEMU virt, booted via OpenSBI.
//!
//! OpenSBI enters `_start` in S-mode with `a0` = hartid and `a1` = physical
//! address of the device tree blob. `_start` sets `gp` and `sp`, zeros `.bss`,
//! then calls `kmain`, which prints a hello line over the SBI debug console,
//! a boot CSR snapshot, installs the trap handler, continues past a
//! deliberate `ebreak`, waits for 30 timer ticks, runs a frame-allocator
//! self-test, and `M0 BOOT OK`, then asks OpenSBI to shut the machine down. A panic prints
//! `PANIC at file:line: message` and parks.

#![no_std]
#![no_main]

mod console;
mod csr;
mod frame;
mod sbi;
mod timer;
mod trap;

use core::arch::{asm, global_asm};

// Kernel entry. The linker script places `.text.entry` at 0x8020_0000, the
// address OpenSBI jumps to. Firmware gives us registers and nothing else: no
// stack, no zeroed bss.
//
// The `norelax` dance: `gp` itself is loaded with an absolute address; if the
// assembler were allowed to relax that load into a gp-relative one, it would
// compute gp from the garbage gp we're trying to replace.
//
// `a0`/`a1` are saved across the bss loop so OpenSBI's hartid and DTB pointer
// reach `kmain` even if a future edit of the loop uses those registers.
global_asm!(
    r#"
    .section .text.entry, "ax"
    .globl _start
_start:
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop

    la      sp, __boot_stack_top

    mv      s0, a0
    mv      s1, a1

    la      t0, __bss_start
    la      t1, __bss_end
1:
    bgeu    t0, t1, 2f
    sd      zero, 0(t0)
    addi    t0, t0, 8
    j       1b
2:
    mv      a0, s0
    mv      a1, s1
    call    kmain
3:
    wfi
    j       3b
"#
);

/// Rust entry, called from `_start` with OpenSBI's boot arguments in `a0`/`a1`.
/// Prints hello, the boot CSR snapshot (`CSR OK`), installs `stvec`, starts
/// 10 ms ticks, continues past an `ebreak` (`TRAP OK`), waits for `tick 30`,
/// checks the DTB then the frame allocator (`FRAME OK`), and `M0 BOOT OK`,
/// then shuts down via SRST. The `panic-selftest` / `hang-selftest` features
/// divert that path so the harness can exercise FAIL and HANG without
/// editing this file.
#[no_mangle]
extern "C" fn kmain(hartid: usize, dtb_pa: usize) -> ! {
    sbi::require_dbcn();
    println!("kestrel: hello from hart {}, dtb at {:#x}", hartid, dtb_pa);
    {
        let sstatus = csr::sstatus::read();
        let sie = csr::sie::read();
        let stvec = csr::stvec::read();
        let satp = csr::satp::read();
        println!("sstatus {:#x}", sstatus);
        println!("sie {:#x}", sie);
        println!("stvec {:#x}", stvec);
        println!("satp {:#x}", satp);
        if satp != 0 {
            panic!("satp={:#x}, expected Bare (0)", satp);
        }
        println!("CSR OK");
    }
    trap::install();
    timer::init();
    #[cfg(feature = "panic-selftest")]
    panic!("selftest");
    #[cfg(feature = "hang-selftest")]
    park();
    #[cfg(not(any(feature = "panic-selftest", feature = "hang-selftest")))]
    {
        let ebreak_pc: usize;
        unsafe {
            asm!(
                "lla {pc}, 1f",
                "1: ebreak",
                pc = out(reg) ebreak_pc,
                options(nostack),
            );
        }
        let half = unsafe { core::ptr::read(ebreak_pc as *const u16) };
        let width = trap::instruction_width(half);
        let sepc = csr::sepc::read();
        if sepc != ebreak_pc + width {
            panic!(
                "ebreak continued at sepc={:#x}, expected {:#x} (ebreak was {:#x})",
                sepc,
                ebreak_pc + width,
                ebreak_pc
            );
        }
        println!("TRAP OK");
        while timer::ticks() < 30 {
            unsafe { asm!("wfi") };
        }
        // D-0023: header check before init. After this the DTB at
        // 0x87e00000 is clobberable — it lies inside the free-list range.
        frame::check_dtb(dtb_pa);
        frame::init();
        frame::self_test();
        println!("M0 BOOT OK");
        let ret = sbi::shutdown();
        println!(
            "shutdown failed: SRST error={} value={}",
            ret.error, ret.value
        );
        park()
    }
}

/// Set for the duration of `panic`. If we re-enter, `println!` is already on
/// the stack — printing again would recurse until the stack dies. Single hart,
/// not a lock: a `bool` in `.bss`, zeroed by `_start`.
static mut IN_PANIC: bool = false;

/// Required by `no_std`: where `core` lands when an invariant fails.
/// Prints location and message, then parks. Nested panics `ebreak` and park
/// without printing.
#[panic_handler]
fn panic(info: &core::panic::PanicInfo) -> ! {
    let nested = unsafe {
        if IN_PANIC {
            true
        } else {
            IN_PANIC = true;
            false
        }
    };
    if nested {
        // Already printing a panic. Do not call println! or panic! again.
        unsafe {
            asm!("ebreak", "1: wfi", "j 1b", options(noreturn),);
        }
    }

    match info.location() {
        Some(loc) => {
            println!("PANIC at {}:{}: {}", loc.file(), loc.line(), info.message())
        }
        None => println!("PANIC at ?:?: {}", info.message()),
    }
    park()
}

fn park() -> ! {
    loop {
        unsafe { asm!("wfi") };
    }
}
