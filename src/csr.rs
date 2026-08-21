//! Supervisor CSRs this kernel reads and writes.
//!
//! Owns the only `csrr`/`csrw`/`csrs`/`csrc` path for `sstatus`, `sie`, `sip`,
//! `stvec`, `sscratch`, `scause`, `sepc`, `stval`, `satp`, and the read-only
//! `time` counter.
//! Trap setup, interrupt enable, paging, and the timer all go through here;
//! without it each of those would grow its own copy of the same four
//! instructions. Accessors are `#[inline(always)]` so a debug build still
//! emits a single CSR instruction.
//!
//! None of the accessors are `pure` or `readonly`. `pure` would let the
//! compiler CSE or hoist a read across a write of the same CSR — LLVM cannot
//! see that `csrw sstatus` aliases `csrr sstatus`. `time` and `sip` also
//! change without any instruction on this hart (the counter ticks; the
//! platform sets pending bits), so a cached read would be a stale value.
//! Hardware trap entry overwrites `scause`/`sepc`/`stval`/`sstatus` the same
//! way. `readonly` is technically true of every CSR *read* (none of them
//! store to memory) but is redundant with `nomem` and would read as a claim
//! that the value is stable, which it is not for `time` and `sip`.
//!
//! `nomem` is used on reads and on writes that do not change how other
//! memory operations behave. It is omitted on writes to `sstatus` (SUM/MXR
//! change load/store/fetch permissions), `satp` (turns translation on or
//! off), and `sie`/`sip` (can make a trap, whose handler accesses memory,
//! reachable). `nostack` is always correct: these instructions do not use
//! the stack.
//!
//! `time` exposes only `read`. A write is an illegal instruction, and that
//! exception is not delegated on this platform (PLAN M1 concept 9), so the
//! failure would land in OpenSBI rather than in our handler.

#![allow(dead_code)]

use core::arch::asm;

macro_rules! rw_csr {
    ($name:ident, write_opts: [$($write_opt:ident),+] $(, { $($extra:tt)* })?) => {
        pub mod $name {
            use super::asm;

            #[inline(always)]
            pub fn read() -> usize {
                let val: usize;
                unsafe {
                    asm!(
                        concat!("csrr {val}, ", stringify!($name)),
                        val = out(reg) val,
                        options(nomem, nostack, preserves_flags),
                    );
                }
                val
            }

            #[inline(always)]
            pub fn write(val: usize) {
                unsafe {
                    asm!(
                        concat!("csrw ", stringify!($name), ", {val}"),
                        val = in(reg) val,
                        options($($write_opt),+),
                    );
                }
            }

            #[inline(always)]
            pub fn set(mask: usize) {
                unsafe {
                    asm!(
                        concat!("csrs ", stringify!($name), ", {mask}"),
                        mask = in(reg) mask,
                        options($($write_opt),+),
                    );
                }
            }

            #[inline(always)]
            pub fn clear(mask: usize) {
                unsafe {
                    asm!(
                        concat!("csrc ", stringify!($name), ", {mask}"),
                        mask = in(reg) mask,
                        options($($write_opt),+),
                    );
                }
            }

            $($($extra)*)?
        }
    };
}

rw_csr!(sstatus, write_opts: [nostack, preserves_flags], {
    /// Supervisor Interrupt Enable. Privileged spec 20211203 §4.1.1.
    pub const SIE: usize = 1 << 1;
    /// Supervisor Previous Interrupt Enable. §4.1.1.
    pub const SPIE: usize = 1 << 5;
    /// Supervisor Previous Privilege (1 = S, 0 = U). §4.1.1.
    pub const SPP: usize = 1 << 8;
    /// Floating-point state (`Off`/`Initial`/`Clean`/`Dirty`), bits 14:13. §4.1.1.
    pub const FS: usize = 0b11 << 13;
    /// Supervisor User Memory access. §4.1.1.
    pub const SUM: usize = 1 << 18;
    /// Make eXecutable Readable. §4.1.1.
    pub const MXR: usize = 1 << 19;
    /// User XLEN, bits 33:32. §4.1.1. Value `2` means UXLEN = 64.
    pub const UXL: usize = 0b11 << 32;
    /// `sstatus.UXL` field value 2: UXLEN = 64. §4.1.1.
    pub const UXL_64: usize = 2 << 32;
    /// Dirty: FS/XS/VS some-dirty summary. §4.1.1, bit 63 on RV64.
    pub const SD: usize = 1 << 63;
});

rw_csr!(sie, write_opts: [nostack, preserves_flags], {
    /// Supervisor software interrupt enable. Privileged spec 20211203 §4.1.3.
    pub const SSIE: usize = 1 << 1;
    /// Supervisor timer interrupt enable. §4.1.3.
    pub const STIE: usize = 1 << 5;
    /// Supervisor external interrupt enable. §4.1.3.
    pub const SEIE: usize = 1 << 9;
});

rw_csr!(sip, write_opts: [nostack, preserves_flags], {
    /// Supervisor software interrupt pending. Privileged spec 20211203 §4.1.3.
    /// Writable from S-mode.
    pub const SSIP: usize = 1 << 1;
    /// Supervisor timer interrupt pending. §4.1.3. Not write-clearable from S-mode.
    pub const STIP: usize = 1 << 5;
    /// Supervisor external interrupt pending. §4.1.3. Not writable from S-mode.
    pub const SEIP: usize = 1 << 9;
});

rw_csr!(stvec, write_opts: [nomem, nostack, preserves_flags], {
    /// MODE field, bits 1:0. Privileged spec 20211203 §4.1.2.
    pub const MODE_MASK: usize = 0b11;
    /// Direct: all traps jump to BASE. §4.1.2.
    pub const MODE_DIRECT: usize = 0;
    /// Vectored: interrupts jump to BASE + 4 × cause. §4.1.2.
    pub const MODE_VECTORED: usize = 1;
});

// sscratch: trap-handler scratch. Privileged spec 20211203 §4.1.1.
// D-0029: kernel stack top in U-mode, 0 in S-mode.
rw_csr!(sscratch, write_opts: [nomem, nostack, preserves_flags]);

rw_csr!(scause, write_opts: [nomem, nostack, preserves_flags], {
    /// Interrupt vs exception. Privileged spec 20211203 §4.1.8, bit SXLEN-1.
    pub const INTERRUPT: usize = 1 << 63;

    /// Interrupt code 1. §4.1.8 Table 4.2.
    pub const INT_S_SOFTWARE: usize = 1;
    /// Interrupt code 5. Table 4.2.
    pub const INT_S_TIMER: usize = 5;
    /// Interrupt code 9. Table 4.2.
    pub const INT_S_EXTERNAL: usize = 9;

    /// Exception code 0. Table 4.2.
    pub const EXC_INST_ADDR_MISALIGNED: usize = 0;
    /// Exception code 1. Table 4.2. Not delegated (MEDELEG); OpenSBI absorbs it.
    pub const EXC_INST_ACCESS_FAULT: usize = 1;
    /// Exception code 2. Table 4.2. Not delegated.
    pub const EXC_ILLEGAL_INST: usize = 2;
    /// Exception code 3. Table 4.2.
    pub const EXC_BREAKPOINT: usize = 3;
    /// Exception code 4. Table 4.2. Not delegated.
    pub const EXC_LOAD_ADDR_MISALIGNED: usize = 4;
    /// Exception code 5. Table 4.2. Not delegated.
    pub const EXC_LOAD_ACCESS_FAULT: usize = 5;
    /// Exception code 6. Table 4.2. Not delegated.
    pub const EXC_STORE_ADDR_MISALIGNED: usize = 6;
    /// Exception code 7. Table 4.2. Not delegated.
    pub const EXC_STORE_ACCESS_FAULT: usize = 7;
    /// Exception code 8. Table 4.2.
    pub const EXC_ECALL_U: usize = 8;
    /// Exception code 9. Table 4.2. Not delegated — this is how SBI `ecall`s reach M-mode.
    pub const EXC_ECALL_S: usize = 9;
    /// Exception code 12. Table 4.2.
    pub const EXC_INST_PAGE_FAULT: usize = 12;
    /// Exception code 13. Table 4.2.
    pub const EXC_LOAD_PAGE_FAULT: usize = 13;
    /// Exception code 15. Table 4.2.
    pub const EXC_STORE_PAGE_FAULT: usize = 15;
});

rw_csr!(sepc, write_opts: [nomem, nostack, preserves_flags]);
rw_csr!(stval, write_opts: [nomem, nostack, preserves_flags]);

rw_csr!(satp, write_opts: [nostack, preserves_flags], {
    /// MODE field, bits 63:60. Privileged spec 20211203 §4.1.11 (RV64).
    pub const MODE_SHIFT: usize = 60;
    /// Bare: no translation. §4.1.11.
    pub const MODE_BARE: usize = 0;
    /// Sv39. §4.1.11.
    pub const MODE_SV39: usize = 8;
    /// ASID field, bits 59:44. §4.1.11.
    pub const ASID_SHIFT: usize = 44;
    /// ASID width on RV64. §4.1.11.
    pub const ASID_MASK: usize = 0xFFFF;
    /// PPN field, bits 43:0. §4.1.11.
    pub const PPN_MASK: usize = (1 << 44) - 1;
});

/// `time` CSR: read-only shadow of `mtime`.
///
/// Unprivileged spec 20211203, Zicntr; privileged spec 20211203 §3.1.10.
/// QEMU `virt` ticks this at 10 MHz. Use `rdtime` rather than `csrr time` so
/// a write cannot be introduced by accident.
/// Sstc supervisor timer comparator (D-0079 seam, bios-none lane only).
/// CSR 0x14D per the Sstc extension spec; written by number because the
/// assembler need not know the extension for a raw csrw. `sip.STIP` is
/// architecturally `stimecmp <= time` — writing a future deadline both
/// arms the next tick and acknowledges the current one.
#[cfg(feature = "bios-none")]
pub mod stimecmp {
    use super::asm;

    #[inline(always)]
    pub fn write(val: usize) {
        unsafe {
            asm!(
                "csrw 0x14D, {val}",
                val = in(reg) val,
                options(nomem, nostack, preserves_flags),
            );
        }
    }
}

pub mod time {
    use super::asm;

    #[inline(always)]
    pub fn read() -> usize {
        let val: usize;
        unsafe {
            asm!(
                "rdtime {val}",
                val = out(reg) val,
                options(nomem, nostack, preserves_flags),
            );
        }
        val
    }
}
