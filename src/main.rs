//! Whimbrel — a minimal rv64gc unikernel for QEMU virt, booted via OpenSBI.
//!
//! OpenSBI enters `_start` in S-mode with `a0` = hartid and `a1` = physical
//! address of the device tree blob. `_start` sets `gp` and `sp`, zeros `.bss`,
//! then calls `kmain`, which prints a hello line over the SBI debug console,
//! a boot CSR snapshot, installs the trap handler, continues past a
//! deliberate `ebreak`, waits for 3 timer ticks, runs a frame-allocator
//! self-test, builds Sv39 page tables and walks them in software
//! (`PAGETABLE OK`), activates Sv39 (`PAGING OK`), runs a heap self-test
//! (`HEAP OK`), and `M1 FUNDAMENTALS OK`. M2 then `sret`s into U-mode tasks,
//! prints `M2 EXECUTION OK`, and asks OpenSBI to shut the machine down.

#![no_std]
#![no_main]

extern crate alloc;

mod arp;
mod checksum;
mod console;
mod csr;
mod frame;
mod heap;
mod icmp;
mod ipv4;
mod net;
mod page;
mod phase;
mod sbi;
#[cfg(feature = "stress")]
mod stress;
mod syscall;
mod task;
mod tcp;
mod timer;
mod trap;
mod uaccess;
mod udp;
mod user;
mod virtio;
mod virtq;

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
    rdtime  s2
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
    la      t0, PHASE_STAMPS
    sd      s2, 0(t0)
    mv      a0, s0
    mv      a1, s1
    call    kmain
3:
    wfi
    j       3b
"#
);

#[cfg(all(
    feature = "net-udp-selftest",
    any(
        feature = "net-http-selftest",
        feature = "tcp-drop-first-tx",
        feature = "http-persist"
    )
))]
compile_error!("net-udp-selftest is exclusive of the HTTP images");

/// Rust entry, called from `_start` with OpenSBI's boot arguments in `a0`/`a1`.
/// Prints hello, the boot CSR snapshot (`CSR OK`), installs `stvec`, starts
/// 10 ms ticks, continues past an `ebreak` (`TRAP OK`), waits for `tick 3`,
/// checks the DTB then the frame allocator (`FRAME OK`), builds page tables
/// without writing `satp` (`PAGETABLE OK`), activates Sv39 (`PAGING OK`),
/// runs the heap self-test (`HEAP OK`), and `M1 FUNDAMENTALS OK`, then
/// `sret`s into U-mode (D-0035: does not return). The `panic-selftest` /
/// `hang-selftest` features divert that path so the harness can exercise
/// FAIL and HANG without editing this file.
#[no_mangle]
extern "C" fn kmain(_hartid: usize, dtb_pa: usize) -> ! {
    crate::phase::stamp(crate::phase::STAMP_A);
    crate::phase::stamp(crate::phase::STAMP_B);
    // D-0079: the bios-none lane has no SBI to probe; its console is
    // the polled UART, proven by the very next println reaching the
    // gate's grep. An SBI ecall here would land in the shim's M
    // diagnostic — which is exactly what the skeleton milestone did.
    #[cfg(not(feature = "bios-none"))]
    sbi::require_dbcn();
    println!("whimbrel: hello from hart {}, dtb at {:#x}", _hartid, dtb_pa);
    {
        let _sstatus = csr::sstatus::read();
        let _sie = csr::sie::read();
        let _stvec = csr::stvec::read();
        let _sscratch = csr::sscratch::read();
        let satp = csr::satp::read();
        println!("sstatus {:#x}", _sstatus);
        println!("sie {:#x}", _sie);
        println!("stvec {:#x}", _stvec);
        println!("sscratch {:#x}", _sscratch);
        println!("satp {:#x}", satp);
        if satp != 0 {
            panic!("satp={:#x}, expected Bare (0)", satp);
        }
        println!("CSR OK");
    }
    trap::install();
    println!("sscratch {:#x}", csr::sscratch::read());
    timer::init();
    #[cfg(feature = "panic-selftest")]
    panic!("selftest");
    #[cfg(feature = "hang-selftest")]
    park();
    #[cfg(not(any(feature = "panic-selftest", feature = "hang-selftest")))]
    {
        #[cfg(not(feature = "fast-boot"))]
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
            while timer::ticks() < 3 {
                unsafe { asm!("wfi") };
            }
        }
        // D-0023: header check before init. After this the DTB at
        // 0x87e00000 is clobberable — it lies in the bump range (D-0065);
        // init does not write through it.
        frame::check_dtb(dtb_pa);
        frame::init();
        crate::phase::stamp(crate::phase::FRAME_INIT);
        #[cfg(not(feature = "fast-boot"))]
        frame::self_test();
        task::check_layout();
        task::init();
        crate::phase::stamp(crate::phase::TASK_INIT);
        page::init();
        page::activate();
        virtio::probe();
        virtq::init();
        net::init();
        heap::init();
        crate::phase::stamp(crate::phase::HEAP_INIT);
        #[cfg(not(feature = "fast-boot"))]
        heap::self_test();
        println!("M1 FUNDAMENTALS OK");
        #[cfg(feature = "frame-exhaust-selftest")]
        loop {
            let _ = frame::alloc_frame();
        }
        #[cfg(feature = "stress")]
        {
            crate::stress::run();
            println!("STRESS OK");
        }
        #[cfg(not(any(
            feature = "frame-exhaust-selftest",
            feature = "stress",
            feature = "freeze-selftest",
            feature = "net-init-selftest",
        )))]
        {
            // D-0035: kmain does not return after the first sret to U.
            #[cfg(any(feature = "userptr-kernel-selftest", feature = "userptr-span-selftest"))]
            task::enter(0);
            #[cfg(feature = "user-fault-selftest")]
            task::enter(2);
            #[cfg(not(any(
                feature = "userptr-kernel-selftest",
                feature = "userptr-span-selftest",
                feature = "user-fault-selftest",
            )))]
            task::enter(3);
        }
        #[cfg(feature = "net-init-selftest")]
        {
            println!("NET INIT OK");
            let ret = sbi::shutdown();
            println!(
                "shutdown failed: SRST error={} value={}",
                ret.error, ret.value
            );
            park()
        }
        #[cfg(feature = "freeze-selftest")]
        {
            frame::freeze();
            let _ = frame::alloc_frame();
            panic!("alloc_frame after freeze returned");
        }
        #[cfg(feature = "stress")]
        {
            let ret = sbi::shutdown();
            println!(
                "shutdown failed: SRST error={} value={}",
                ret.error, ret.value
            );
            park()
        }
    }
}

/// Set for the duration of `panic`. If we re-enter, `println!` is already on
/// the stack — printing again would recurse until the stack dies. Single hart,
/// not a lock: a `bool` in `.bss`, zeroed by `_start`.
static mut IN_PANIC: bool = false;

/// Required by `no_std`: where `core` lands when an invariant fails.
/// Prints location and message, then parks. Nested panics `ebreak` and park
/// without printing. Clears `sstatus.SUM` first: a page fault inside a
/// copy window arrives here with SUM still set, and formatting must not
/// run with ambient user-memory authority.
#[panic_handler]
fn panic(info: &core::panic::PanicInfo) -> ! {
    csr::sstatus::clear(csr::sstatus::SUM);
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
            println_always!("PANIC at {}:{}: {}", loc.file(), loc.line(), info.message())
        }
        None => println_always!("PANIC at ?:?: {}", info.message()),
    }
    csr::sie::clear(csr::sie::STIE);
    park()
}

fn park() -> ! {
    loop {
        unsafe { asm!("wfi") };
    }
}

// D-0079: the M-mode shim donor. Declared last so the cfg'd module
// shifts no line numbers above it — panic-location strings are
// file:line, and moving them would change the default kernel's hash
// (DEBUGGING.md has the full lesson).
#[cfg(feature = "mshim")]
mod mshim;
