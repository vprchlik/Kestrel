//! Trap entry, frame, and dispatch.
//!
//! Owns `__trap_entry` (the `stvec` target), `__trap_return` (the shared
//! epilogue), and the Rust `trap_handler` they call. Hardware saves
//! `sepc`/`scause`/`stval` and two `sstatus` bits and nothing else — this
//! module is the only place the other 31 GPRs are preserved. Without it a
//! delegated trap jumps to whatever OpenSBI left in `stvec` (our `_start`)
//! and boot-loops.
//!
//! Frame layout is D-0020: `x[i]` at offset `8 * i`, then `sepc`, `sstatus`,
//! 272 bytes. `sscratch` is 0 in S-mode and the current task's kernel stack
//! top in U-mode (D-0029). `trap_handler` returns the frame to resume
//! (D-0032); block 4 is `__trap_return`, which T2.5's first `sret` will
//! share.

use crate::csr;
use crate::println;
use crate::timer;
use core::arch::{asm, global_asm};

/// `TrapFrame` size in bytes. 32 GPRs + `sepc` + `sstatus`.
pub const FRAME_SIZE: usize = 272;

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

    /// Syscall return pair (D-0033). `__trap_return` restores `a0`/`a1`
    /// from `x[10]`/`x[11]`; writing the CPU registers here is a no-op.
    pub fn set_retval(&mut self, error: isize, value: usize) {
        self.x[10] = error as usize;
        self.x[11] = value;
    }
}

extern "C" {
    fn __trap_entry();
    fn __trap_return();
}

/// Jump to `__trap_return` with `a0` = frame. Used for the first `sret`
/// into U-mode (T2.5) and later for every resume. Does not return.
#[cfg_attr(
    any(
        feature = "panic-selftest",
        feature = "hang-selftest",
        feature = "stress",
        feature = "frame-exhaust-selftest"
    ),
    allow(dead_code)
)]
pub fn resume(frame: *mut TrapFrame) -> ! {
    unsafe {
        asm!(
            "j __trap_return",
            in("a0") frame,
            options(noreturn),
        );
    }
}

/// Physical/virtual address of `__trap_entry` (identity mapped). Used by
/// `page::activate` to confirm the `stvec` page is mapped X before `satp`.
pub fn entry_pa() -> usize {
    __trap_entry as *const () as usize
}

/// Point `stvec` at `__trap_entry` in Direct mode. Panics if the symbol is
/// not 4-byte aligned: writing a misaligned address would silently set MODE
/// to Vectored (or reserved) instead of erroring (D-0020).
///
/// D-0029: `sscratch` is 0 in S-mode. Zero it **before** writing `stvec`: a
/// trap taken with firmware garbage in `sscratch` would be misread as a trap
/// from U-mode and the entry would push a frame at that address.
pub fn install() {
    csr::sscratch::write(0);
    let scratch = csr::sscratch::read();
    if scratch != 0 {
        panic!("sscratch clear failed: {:#x}", scratch);
    }
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
/// Returns the frame `__trap_return` should resume (D-0032). M1 traps and
/// the timer still return the same `frame` they were given. An `ecall` from
/// U is dispatched by `syscall::from_ecall`; unknown numbers kill the task
/// and do not return.
#[no_mangle]
extern "C" fn trap_handler(frame: &mut TrapFrame) -> &mut TrapFrame {
    // D-0029: Rust runs in S-mode, so sscratch must be 0. A nonzero value
    // here means the S-path undo failed or the U path forgot to clear it.
    let scratch = csr::sscratch::read();
    if scratch != 0 {
        panic!("sscratch {:#x} in handler, want 0", scratch);
    }

    let scause = csr::scause::read();
    let stval = csr::stval::read();
    let interrupt = scause & csr::scause::INTERRUPT != 0;
    let code = scause & !csr::scause::INTERRUPT;

    if interrupt {
        if code == csr::scause::INT_S_TIMER {
            timer::on_interrupt();
            return frame;
        }
        unknown_trap(scause, code, true, frame, stval);
    }

    match code {
        csr::scause::EXC_ECALL_U => {
            return crate::syscall::from_ecall(frame);
        }
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
        csr::scause::EXC_INST_PAGE_FAULT
        | csr::scause::EXC_LOAD_PAGE_FAULT
        | csr::scause::EXC_STORE_PAGE_FAULT => {
            panic!(
                "trap scause={} ({}) sepc={:#x} stval={:#x} sp={:#x}",
                code,
                page_fault_name(code),
                frame.sepc,
                stval,
                frame.sp()
            );
        }
        _ => unknown_trap(scause, code, false, frame, stval),
    }
    frame
}

fn page_fault_name(code: usize) -> &'static str {
    match code {
        csr::scause::EXC_INST_PAGE_FAULT => "instruction page fault",
        csr::scause::EXC_LOAD_PAGE_FAULT => "load page fault",
        csr::scause::EXC_STORE_PAGE_FAULT => "store page fault",
        _ => "page fault",
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
/// enables it. Other interrupt codes still panic here.
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

// Four blocks (D-0020 / D-0029 / D-0032):
//   (1) sscratch swap + branch-on-zero; S path undoes with a second csrrw
//   (2) save frame (offsets unchanged)
//   (3) call Rust; a0 in = frame, a0 out = frame to resume
//   (4) __trap_return: mv sp, a0, restore, sret
// `.balign 4` is the assembler-side guarantee that bits [1:0] of
// `__trap_entry` are 00, so a `csrw stvec` of this address selects Direct
// mode. `install()` double-checks at runtime.
global_asm!(
    r#"
    .section .text
    .balign 4
    .globl __trap_entry
__trap_entry:
    # Block 1 (D-0029): establish the kernel stack pointer.
    # U: sscratch was kstack_top (nonzero) → sp = kstack_top, user sp in
    #    sscratch. S: sscratch was 0 → sp = 0, kernel sp in sscratch; the
    #    second csrrw undoes the first. Self-restoring: a nested trap from
    #    the handler sees sscratch == 0 and keeps the faulting kernel sp.
    csrrw   sp, sscratch, sp
    bnez    sp, 1f
    csrrw   sp, sscratch, sp
1:
    # Block 2: save the frame (272 bytes). x[0] unused; x[2] filled below.
    # Offsets are D-0020 and do not change.
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
    # On the S path this is the real pre-trap sp. On the U path it is
    # kstack_top; the next block overwrites x[2] with the user sp.
    addi    t0, sp, 272
    sd      t0,  16(sp)

    # U path only: sscratch still holds the trapped user sp. Copy it into
    # x[2], then zero sscratch so the handler (S-mode) keeps the invariant.
    # Reload kernel gp with norelax — user gp is already in the frame, and
    # relaxed kernel accesses would follow the user's gp into the wrong
    # addresses with no fault (D-0029). Save happened first so we don't
    # clobber the user's gp before storing it.
    csrr    t0, sscratch
    beqz    t0, 2f
    sd      t0,  16(sp)
    csrw    sscratch, zero
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop
2:
    csrr    t0, sepc
    sd      t0, 256(sp)
    csrr    t0, sstatus
    sd      t0, 264(sp)

    # Block 3: call Rust. a0 = &TrapFrame in; a0 = frame to resume out.
    mv      a0, sp
    call    trap_handler

    # Block 4 (D-0032). Do not insert padding between the call and this
    # symbol: the call returns here. T2.5 jumps here with a0 = fabricated
    # frame.
    .globl __trap_return
__trap_return:
    mv      sp, a0

    # CSRs first so t0 can be scratch.
    ld      t0, 256(sp)
    csrw    sepc, t0
    ld      t0, 264(sp)
    csrw    sstatus, t0

    # Restore GPRs except t0 (still scratch) and sp.
    ld      ra,   8(sp)
    ld      gp,  24(sp)
    ld      tp,  32(sp)
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

    # Isolate SPP while the frame is still at sp, then pop. t0 holds 0 (U)
    # or 0x100 (S) across the addi. Both paths: S reconstructs the pre-trap
    # sp (we pushed exactly 272); U yields kstack_top.
    ld      t0, 264(sp)
    andi    t0, t0, {spp}
    addi    sp, sp, 272

    # U/S branch covers the two sscratch instructions (park + swap). S
    # restores t0 and sret with sscratch still 0. U must restore t0 from
    # the frame *before* the swap, so one kernel-stack load sits between
    # park and csrrw — see the T2.3 notes.
    bnez    t0, 3f
    ld      t0, -256(sp)
    csrw    sscratch, t0
    ld      t0, -232(sp)
    csrrw   sp, sscratch, sp
    sret
3:
    ld      t0, -232(sp)
    sret
"#,
    spp = const csr::sstatus::SPP,
);
