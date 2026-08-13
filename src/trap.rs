//! Trap entry, frame, and dispatch.
//!
//! Owns `__trap_entry` (the `stvec` target) and the Rust `trap_handler` it
//! calls. Hardware saves `sepc`/`scause`/`stval` and two `sstatus` bits and
//! nothing else — this module is the only place the other 31 GPRs are
//! preserved. Without it a delegated trap jumps to whatever OpenSBI left in
//! `stvec` (our `_start`) and boot-loops.
//!
//! Frame layout is D-0020: `x[i]` at offset `8 * i`, then `sepc`, `sstatus`,
//! 272 bytes. `sscratch` is left untouched (D-0020 constraint 4).

use crate::csr;
use crate::println;
use core::arch::global_asm;

/// `TrapFrame` size in bytes. 32 GPRs + `sepc` + `sstatus`.
const FRAME_SIZE: usize = 272;

#[repr(C)]
pub struct TrapFrame {
    /// GPRs. `x[0]` is unused (`x0` is hardwired zero). `x[2]` is the
    /// *pre-trap* `sp`, not the frame pointer.
    pub x: [usize; 32],
    pub sepc: usize,
    pub sstatus: usize,
}

const _: () = assert!(core::mem::size_of::<TrapFrame>() == FRAME_SIZE);
const _: () = assert!(FRAME_SIZE % 16 == 0);

#[allow(dead_code)]
impl TrapFrame {
    #[inline]
    pub fn ra(&self) -> usize {
        self.x[1]
    }
    #[inline]
    pub fn sp(&self) -> usize {
        self.x[2]
    }
    #[inline]
    pub fn a0(&self) -> usize {
        self.x[10]
    }
    #[inline]
    pub fn a1(&self) -> usize {
        self.x[11]
    }
    #[inline]
    pub fn a2(&self) -> usize {
        self.x[12]
    }
    #[inline]
    pub fn a3(&self) -> usize {
        self.x[13]
    }
    #[inline]
    pub fn a4(&self) -> usize {
        self.x[14]
    }
    #[inline]
    pub fn a5(&self) -> usize {
        self.x[15]
    }
    #[inline]
    pub fn a6(&self) -> usize {
        self.x[16]
    }
    #[inline]
    pub fn a7(&self) -> usize {
        self.x[17]
    }
}

extern "C" {
    fn __trap_entry();
}

/// Point `stvec` at `__trap_entry` in Direct mode. Panics if the symbol is
/// not 4-byte aligned: writing a misaligned address would silently set MODE
/// to Vectored (or reserved) instead of erroring (D-0020).
pub fn install() {
    let addr = __trap_entry as *const () as usize;
    if addr & csr::stvec::MODE_MASK != 0 {
        panic!(
            "__trap_entry {:#x} is not 4-byte aligned; stvec MODE would become {}",
            addr,
            addr & csr::stvec::MODE_MASK
        );
    }
    csr::stvec::write(addr);
    let got = csr::stvec::read();
    if got != addr || got & csr::stvec::MODE_MASK != csr::stvec::MODE_DIRECT {
        panic!(
            "stvec install failed: wrote {:#x} (Direct), read {:#x}",
            addr, got
        );
    }
}

/// Instruction width in bytes from the trapped instruction's low halfword.
/// Low two bits `0b11` ⇒ 4 bytes, anything else ⇒ 2 (RVC). D-0021: takes
/// the bits as an argument and does not dereference `sepc`.
pub fn instruction_width(halfword: u16) -> usize {
    if halfword & 0b11 == 0b11 {
        4
    } else {
        2
    }
}

/// Called from `__trap_entry` with `a0` = frame pointer on the kernel stack.
#[no_mangle]
extern "C" fn trap_handler(frame: &mut TrapFrame) {
    let scause = csr::scause::read();
    let stval = csr::stval::read();
    let interrupt = scause & csr::scause::INTERRUPT != 0;
    let code = scause & !csr::scause::INTERRUPT;

    if interrupt {
        // No interrupt sources enabled until T1.3. A pending bit that
        // sneaks through is unknown, not a timer tick.
        unknown_trap(scause, code, true, frame, stval);
    }

    match code {
        csr::scause::EXC_BREAKPOINT => {
            println!(
                "trap scause={} (breakpoint) sepc={:#x} stval={:#x} sp={:#x}",
                code,
                frame.sepc,
                stval,
                frame.sp()
            );
            // Read stays here, not inside instruction_width (D-0021).
            let half = unsafe { core::ptr::read(frame.sepc as *const u16) };
            frame.sepc += instruction_width(half);
        }
        _ => unknown_trap(scause, code, false, frame, stval),
    }
}

/// OpenSBI on this platform sets `MEDELEG = 0xf0b509`, so the exceptions
/// that can actually arrive in S-mode are:
///   0  instruction address misaligned
///   3  breakpoint
///   8  ecall from U-mode (M2)
///   10 ecall from VS-mode (H-extension; we have no guest)
///   12/13/15 instruction/load/store page fault (T1.7+)
///   20–23 guest-page / virtual-instruction faults (H-extension)
/// Codes 1, 2, 4, 5, 6, 7, 9 are **not** delegated: they never reach this
/// function; they produce an OpenSBI dump. `MIDELEG = 0x1666` includes
/// supervisor timer (bit 5), so a timer interrupt can arrive once T1.3
/// enables it — not before, and not as an "unknown exception".
fn unknown_trap(scause: usize, code: usize, interrupt: bool, frame: &TrapFrame, stval: usize) -> ! {
    panic!(
        "unknown trap scause={:#x} ({} code {}) sepc={:#x} stval={:#x} sp={:#x}",
        scause,
        if interrupt { "interrupt" } else { "exception" },
        code,
        frame.sepc,
        stval,
        frame.sp()
    );
}

// Four blocks (D-0020): (1) stack switch — empty in M1, (2) save frame,
// (3) call Rust, (4) restore and `sret`. `.balign 4` is the assembler-side
// guarantee that bits [1:0] of the symbol are 00, so a `csrw stvec` of this
// address selects Direct mode. `install()` double-checks at runtime.
global_asm!(
    r#"
    .section .text
    .balign 4
    .globl __trap_entry
__trap_entry:
    # Block 1: establish the kernel stack pointer.
    # Empty in M1 — we are already on the kernel stack. M2 fills this with
    # `csrrw sp, sscratch, sp` and nothing else in the entry changes.
    # (D-0020 constraint 1). sscratch is left meaningless until then.

    # Block 2: save the frame (272 bytes). x[0] unused; x[2] filled below.
    addi    sp, sp, -272
    sd      ra,   8(sp)
    sd      gp,  24(sp)
    sd      tp,  32(sp)
    sd      t0,  40(sp)
    sd      t1,  48(sp)
    sd      t2,  56(sp)
    sd      s0,  64(sp)
    sd      s1,  72(sp)
    sd      a0,  80(sp)
    sd      a1,  88(sp)
    sd      a2,  96(sp)
    sd      a3, 104(sp)
    sd      a4, 112(sp)
    sd      a5, 120(sp)
    sd      a6, 128(sp)
    sd      a7, 136(sp)
    sd      s2, 144(sp)
    sd      s3, 152(sp)
    sd      s4, 160(sp)
    sd      s5, 168(sp)
    sd      s6, 176(sp)
    sd      s7, 184(sp)
    sd      s8, 192(sp)
    sd      s9, 200(sp)
    sd      s10, 208(sp)
    sd      s11, 216(sp)
    sd      t3, 224(sp)
    sd      t4, 232(sp)
    sd      t5, 240(sp)
    sd      t6, 248(sp)
    # Pre-trap sp = current sp + 272. Extra addi (D-0020); t0 already saved.
    addi    t0, sp, 272
    sd      t0,  16(sp)
    csrr    t0, sepc
    sd      t0, 256(sp)
    csrr    t0, sstatus
    sd      t0, 264(sp)

    # Block 3: call Rust. a0 = &TrapFrame.
    mv      a0, sp
    call    trap_handler

    # Block 4: restore and sret. CSRs first so t0 can be scratch, then GPRs,
    # then sp from the saved pre-trap value (the extra-addi slot).
    ld      t0, 256(sp)
    csrw    sepc, t0
    ld      t0, 264(sp)
    csrw    sstatus, t0
    ld      ra,   8(sp)
    ld      gp,  24(sp)
    ld      tp,  32(sp)
    ld      t0,  40(sp)
    ld      t1,  48(sp)
    ld      t2,  56(sp)
    ld      s0,  64(sp)
    ld      s1,  72(sp)
    ld      a0,  80(sp)
    ld      a1,  88(sp)
    ld      a2,  96(sp)
    ld      a3, 104(sp)
    ld      a4, 112(sp)
    ld      a5, 120(sp)
    ld      a6, 128(sp)
    ld      a7, 136(sp)
    ld      s2, 144(sp)
    ld      s3, 152(sp)
    ld      s4, 160(sp)
    ld      s5, 168(sp)
    ld      s6, 176(sp)
    ld      s7, 184(sp)
    ld      s8, 192(sp)
    ld      s9, 200(sp)
    ld      s10, 208(sp)
    ld      s11, 216(sp)
    ld      t3, 224(sp)
    ld      t4, 232(sp)
    ld      t5, 240(sp)
    ld      t6, 248(sp)
    ld      sp,  16(sp)
    sret
"#
);
