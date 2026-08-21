//! M-mode shim for the `-bios none` lane (D-0079, executing D-0061).
//!
//! Owns everything OpenSBI did that the kernel still needs: a PMP
//! catch-all (without it, S-mode faults on its first instruction
//! fetch), the delegation masks (transcribed from OpenSBI's banner —
//! the values the kernel was validated under), `mcounteren` (the
//! kernel's first instruction is `rdtime`), `menvcfg.STCE` (the
//! D-0018 Sstc seam), and both trap diagnostics: `mtvec` for M traps
//! and — the D-0079 amendment to D-0061 — a preloaded `stvec`,
//! because with full delegation the two worst bring-up failures (PMP,
//! `mcounteren`) route to `stvec`, not `mtvec`, and before the kernel
//! installs its own vector that means a silent loop through address 0.
//!
//! Invariants: straight-line M-mode code, no stack, no resident
//! services — after `mret` the only way back here is a bug, and both
//! diagnostics say so on the raw UART (polled NS16550A at
//! 0x1000_0000, which QEMU's 16550 accepts from reset). `a0`
//! (hartid) and `a1` (FDT, verified at 0x87e0_0000) pass through
//! untouched. What breaks without it: `-bios none` executes zeroed
//! RAM at 0x8000_0000 and hangs with no output channel at all.
//!
//! Checkpoint letters on the way to `mret`: Z (shim entered),
//! P (PMP), D (delegation), C (counters/envcfg), T (mtvec),
//! V (stvec preload), M (about to mret). A silent stop between two
//! letters names the block that killed the boot. `M!`/`S!` prefix the
//! trap diagnostics, each followed by cause/epc/tval in hex.

// Checkpoint 0 (D-0079): prove execution with no serial dependency.
// sifive_test PASS at 0x10_0000 makes QEMU exit 0 immediately.
#[cfg(feature = "mshim-exit0")]
core::arch::global_asm!(
    r#"
    .section .mshim, "ax"
    .globl _mstart
_mstart:
    li   t0, 0x100000
    li   t1, 0x5555
    sw   t1, 0(t0)
1:  wfi
    j    1b
"#
);

#[cfg(not(feature = "mshim-exit0"))]
core::arch::global_asm!(
    r#"
    .section .mshim, "ax"
    .globl _mstart
_mstart:
    /* Z: the shim is executing. Raw THR store; QEMU's 16550 accepts
     * writes from reset (8N1, no divisor programming — QEMU-only
     * fidelity, which this project is). */
    li   t0, 0x10000000
    li   t1, 'Z'
    sb   t1, 0(t0)

    /* PMP catch-all: one NAPOT entry over everything, RWX. Without
     * at least one matching entry, S/U accesses all fault. W^X stays
     * a page-table property (D-0019); PMP is deliberately permissive,
     * like OpenSBI's Region07. */
    li   t2, -1
    csrw pmpaddr0, t2
    li   t2, 0x1f            /* NAPOT | R | W | X */
    csrw pmpcfg0, t2
    li   t1, 'P'
    sb   t1, 0(t0)

    /* Delegation, transcribed from OpenSBI's banner (D-0079): the
     * kernel was validated under these exact values. Surplus
     * H-extension bits are inert. */
    li   t2, 0xf4b509
    csrw medeleg, t2
    li   t2, 0x1666
    csrw mideleg, t2
    li   t1, 'D'
    sb   t1, 0(t0)

    /* Counters + Sstc. rdtime is the kernel's first instruction;
     * menvcfg.STCE arms the D-0018 stimecmp seam. No M interrupts:
     * nothing here ever wants one. */
    li   t2, -1
    csrw mcounteren, t2
    csrw mcountinhibit, zero
    li   t2, 1
    slli t2, t2, 63          /* menvcfg.STCE */
    csrs menvcfg, t2
    csrw mie, zero
    li   t1, 'C'
    sb   t1, 0(t0)

    /* M diagnostic: any M trap after mret is a bug that says so. */
    la   t2, _mshim_mtrap
    csrw mtvec, t2
    li   t1, 'T'
    sb   t1, 0(t0)

    /* D-0079 amendment to D-0061: preload stvec. With full
     * delegation a PMP or mcounteren failure faults in S before the
     * kernel installs its vector; stvec=0 would loop silently
     * through address 0. The kernel's trap::install overwrites this
     * exactly as it overwrites OpenSBI's leftover. */
    la   t2, _mshim_strap
    csrw stvec, t2
    li   t1, 'V'
    sb   t1, 0(t0)

    /* mret to the unmodified kernel entry: MPP=S, satp is already
     * Bare, a0/a1 untouched since reset. */
    li   t2, 3 << 11
    csrc mstatus, t2
    li   t2, 1 << 11         /* MPP = S */
    csrs mstatus, t2
    la   t2, _start
    csrw mepc, t2
    li   t1, 'M'
    sb   t1, 0(t0)
    mret

    /* M-mode trap diagnostic: "M!" cause epc tval, then park. */
    .align 2
_mshim_mtrap:
    li   t0, 0x10000000
    li   t1, 'M'
    sb   t1, 0(t0)
    li   t1, '!'
    sb   t1, 0(t0)
    csrr t3, mcause
    jal  ra, _mshim_hex
    csrr t3, mepc
    jal  ra, _mshim_hex
    csrr t3, mtval
    jal  ra, _mshim_hex
1:  wfi
    j    1b

    /* S-mode trap diagnostic (runs in S, under the PMP catch-all and
     * Bare translation): "S!" cause epc tval, then park. */
    .align 2
_mshim_strap:
    li   t0, 0x10000000
    li   t1, 'S'
    sb   t1, 0(t0)
    li   t1, '!'
    sb   t1, 0(t0)
    csrr t3, scause
    jal  ra, _mshim_hex
    csrr t3, sepc
    jal  ra, _mshim_hex
    csrr t3, stval
    jal  ra, _mshim_hex
1:  wfi
    j    1b

    /* Print t3 as " %016x". Clobbers t1, t2, t4, t5; t0 = UART. */
_mshim_hex:
    li   t1, ' '
    sb   t1, 0(t0)
    li   t4, 60              /* shift, 60 -> 0 step -4 */
1:  srl  t2, t3, t4
    andi t2, t2, 0xf
    li   t5, 10
    blt  t2, t5, 2f
    addi t2, t2, 'a' - 10 - '0'
2:  addi t2, t2, '0'
    sb   t2, 0(t0)
    addi t4, t4, -4
    bgez t4, 1b
    ret
"#
);
