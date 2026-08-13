# GLOSSARY

Running list of terms, each defined in 2–3 sentences, in our project's
context. Updated at the end of every milestone (project rule). Alphabetical.

**A/D bits.** The Accessed and Dirty flags in an Sv39 PTE (bits 6 and 7).
Some implementations, QEMU included depending on version and config, fault
instead of setting them in hardware, so every kernel leaf is built with
A=1 and D=1 (D-0019).

**CLINT (Core-Local Interruptor).** The per-hart block on QEMU `virt` (at
0x0200_0000) holding the `mtime` counter, the `mtimecmp` timer comparators, and
software-interrupt registers. It is M-mode hardware: OpenSBI programs it on our
behalf when we call `sbi_set_timer`, so we never touch it directly.

**CSR (Control and Status Register).** Per-hart special registers (e.g.
`sstatus`, `satp`, `scause`) accessed by dedicated instructions (`csrr`,
`csrw`) rather than loads/stores. They configure privileged behavior and record
trap state; which CSRs are accessible depends on the current privilege level.

**DBCN (Debug Console).** The SBI extension (EID `0x4442434E`) that replaces
legacy putchar. We use FID 2, `console_write_byte` — one `ecall` per byte —
after probing it via BASE (D-0015). OpenSBI v1.3 implements it and drives the
16550 for us; a missing DBCN is a hard abort, not a fallback to EID `0x01`.

**DTB (Device Tree Blob).** A binary description of the machine's hardware
(RAM size, device addresses, interrupt routing) that QEMU generates and OpenSBI
passes to the kernel in register `a1`. Real kernels parse it for portability;
we print the pointer but hardcode the `virt` layout instead (see D-0012).

**ecall.** The RISC-V instruction that requests service from a higher
privilege level by raising a synchronous exception. From S-mode it is how we
call OpenSBI (SBI calls); from U-mode it will be how the app makes syscalls
into our kernel — same instruction, different trap destination. It has no
compressed encoding, so it is always 4 bytes and OpenSBI advances `mepc` by
exactly 4; that fact does not license our S-mode handler to hardcode `sepc += 4`
(see RVC).

**first-fit.** The heap walks the free list and takes the first block that
can satisfy the request. Combined with coalescing adjacent frees (D-0027)
it is the K&R allocator: after `Vec` growth the old buffers merge into one
hole rather than a staircase of unusable sizes.

**frame.** A 4 KiB physical page. The frame allocator's intrusive free list
owns `[__heap_end, RAM_END)`; the heap's variable-size blocks live in the
1 MiB carve-out below that and never come from this list (D-0024).

**GlobalAlloc.** Rust's `no_std` heap interface (`alloc` / `dealloc` with a
`Layout` of size plus alignment). Our implementation panics on exhaustion
with those two numbers and never returns null (D-0027).

**hart.** A HARdware Thread — one independent instruction stream with its own
registers and CSRs; a core with hyperthreading would be multiple harts. This
project runs on exactly one hart (hart 0), by constraint D-0007.

**identity map.** A translation where the virtual address equals the physical
address. The kernel is identity-mapped (D-0006), so the PC, stack pointer,
and return addresses keep their numeric values across the `satp` write —
there is no higher-half trampoline.

**mret / sret.** "Return from trap" instructions for M-mode and S-mode
respectively: they restore the previous privilege level and interrupt-enable
state from status-register fields (`MPP`/`SPP`, `MPIE`/`SPIE`) and jump to the
saved PC (`mepc`/`sepc`). They are also the *only* way privilege goes down —
OpenSBI `mret`s into our kernel, and our kernel will `sret` into U-mode.

**MMIO (Memory-Mapped I/O).** Device registers exposed at physical addresses,
accessed with ordinary load/store instructions instead of special I/O
instructions. On `virt`, the UART, PLIC, CLINT, and virtio devices are all
MMIO; a wrong MMIO address typically faults or silently does nothing.

**no_std.** A Rust crate attribute declaring independence from the standard
library, leaving only `core` (language essentials, no OS assumptions) and
optionally `alloc` (heap types, once we provide an allocator). It's the
language-level statement that *we* are the operating system.

**OpenSBI.** The reference open-source implementation of the SBI spec — the
M-mode firmware QEMU bundles as `-bios default`. It initializes the machine,
protects itself with PMP, delegates traps to S-mode, jumps to our kernel, and
then stays resident to service our `ecall`s.

**PLIC (Platform-Level Interrupt Controller).** The chip (at 0x0C00_0000 on
`virt`) that gathers external device interrupts (UART, virtio), prioritizes
them, and routes them to a hart as the "supervisor external interrupt". We only
touch it if M3 chooses interrupt-driven networking over polling.

**PMP (Physical Memory Protection).** M-mode CSRs that grant/deny physical
address ranges to lower privilege levels, checked before paging even applies.
OpenSBI uses PMP to protect its own RAM (first ~322 KiB at 0x8000_0000,
observed on OpenSBI v1.3) — the reason an S-mode read of that region
access-faults.

**PPN / VPN.** Physical and virtual page numbers. An Sv39 VA splits into
VPN[2]/VPN[1]/VPN[0] (9 bits each) plus a 12-bit offset; a leaf PTE's PPN
concatenated with that offset is the PA. `satp.PPN` is the root table's
address shifted right 12 — writing the address itself is a factor-of-4096
error (PLAN M1 concept 11).

**PTE (page-table entry).** A 64-bit Sv39 word: V/R/W/X/U/G/A/D in the low
bits, PPN in [53:10]. Any of R/W/X set makes it a leaf (including a 2 MiB
or 1 GiB superpage); a non-leaf must have V=1 and R=W=X=0 or the walk
stops early (concept 11.4). We use only 4 KiB leaves (D-0026).

**RVC (compressed instructions).** The C extension gives 16-bit encodings for
common integer operations; a trap can land on either a 2-byte or a 4-byte
instruction. `ecall` / `ebreak` / CSR ops have no C encoding and are always
4 bytes — OpenSBI can therefore advance `mepc` by 4 after an S-mode `ecall`.
Our S-mode handler (M1/T1.2) must not copy that shortcut: inspect the
instruction at `sepc` (low two bits `0b11` ⇒ 32-bit, otherwise 16-bit) and
advance by that width, or a compressed trap will skip a byte and a later
`sret` will land mid-instruction.

**satp (Supervisor Address Translation and Protection).** The CSR that turns
paging on: it holds the translation mode (8 = Sv39) and the physical page
number of the root page table. Writing it (plus `sfence.vma`) is the single
instant the kernel goes from physical to virtual addressing.

**SBI (Supervisor Binary Interface).** The calling convention between an
S-mode OS and M-mode firmware: extension ID in `a7`, function ID in `a6`,
arguments in `a0..a5`, then `ecall`. It is to firmware what the syscall ABI is
to a kernel; we use it for console output, timers, and shutdown.

**scause / sepc / stval.** The three CSRs the hardware fills on every trap
into S-mode: *why* (interrupt bit + cause code), *where* (PC of the
interrupted instruction), and *what* (faulting address or offending
instruction bits). Reading them as a triple is the first move of all debugging
here (see DEBUGGING.md §3).

**sfence.vma.** The instruction that synchronizes the TLB with page-table
memory: after changing PTEs or `satp`, older translations may still be cached
until you execute it. Project rule: fence after every PTE change — cheap
insurance on one hart.

**sstatus.SPP.** The S-mode "previous privilege" bit (sstatus bit 8): 0 = U,
1 = S. It records where `sret` will return, not the privilege the hart is
running at now. Observed 0 at `kmain` — OpenSBI's initial value after `mret`
into S-mode, not evidence that we are in U-mode. Becomes load-bearing in M2
when we set SPP=U before `sret` into the app.

**SRST (System Reset).** The SBI extension (EID `0x53525354`) that asks
firmware to reset or shut down the machine. We probe it, then call FID 0 with
type=shutdown (D-0017). QEMU exits 0; there is no guest-controlled fail code.
If the `ecall` returns, we print the error and park.

**Sstc.** The RISC-V extension that gives S-mode a `stimecmp` CSR, so the
supervisor can arm timer interrupts without an `ecall` into M-mode. OpenSBI
v1.3 on QEMU `virt` advertises it as `Boot HART ISA Extensions: time,sstc`.
M1 arms through SBI TIME instead (D-0018); Sstc is the M4 comparison, not
the M1 mechanism.

**superpage.** An Sv39 leaf at level 1 (2 MiB) or level 2 (1 GiB). The PPN
must be aligned to that size or the walk faults. M1 maps everything with
4 KiB (level-0) leaves (D-0026): kernel W^X and the 4 KiB guard already
force that grain, and a mixed path is how a non-leaf with R/W/X set
accidentally becomes a misaligned superpage.

**Sv39.** The smallest rv64 virtual-memory mode: 39-bit virtual addresses
translated through three levels of 512-entry page tables to 4 KiB pages (with
2 MiB / 1 GiB leaves possible at higher levels; we do not use them, D-0026).
512 GiB of address space — absurdly more than our 128 MiB of RAM, which is
why we don't need Sv48.

**TIME (SBI).** The SBI extension we use to arm the supervisor timer
(`sbi_set_timer`, absolute `rdtime` deadline). Re-arming is also how S-mode
acknowledges `sip.STIP`, which is not write-clearable here (D-0018). 10 ms
is 100_000 ticks at this platform's 10 MHz timebase.

**timebase.** The rate of the `rdtime` / `mtime` counter. On QEMU `virt`,
OpenSBI reports `Platform Timer Device: aclint-mtimer @ 10000000Hz` — 10 MHz,
so 100 ns per tick. M1's timeslice is `rdtime() + 100_000` (D-0018).

**TLB (Translation Lookaside Buffer).** The hart's cache of recent
virtual→physical translations, consulted before walking page tables in memory.
It is what makes paging fast and what makes *stale* mappings possible — hence
`sfence.vma`.

**TrapFrame.** The 272-byte register save area built on the kernel stack
at trap entry: `x[0..31]` at `8 * regnum`, then `sepc` and `sstatus`
(D-0020). `x[2]` is the pre-trap `sp`. Direct `stvec`; `sscratch` unused
until M2.

**unikernel.** A single application linked with exactly the OS services it
needs into one bootable image — no processes, no dynamic loading, no
general-purpose userland. Our variant deliberately keeps a U/S privilege
boundary and a 5-syscall interface so the isolation cost can be measured
(D-0010).

**virtio / virtio-mmio.** A standard family of paravirtual devices where guest
and hypervisor share ring buffers (virtqueues) in guest memory instead of
emulating real hardware registers. On `virt`, devices appear as virtio-mmio
slots at 0x1000_1000–0x1000_8000; our M3 NIC is a `virtio-net-device`. M1
maps none of that MMIO (D-0025).

**W^X.** Write xor execute: a page is never both writable and executable.
Kernel `.text` is R+X, `.rodata` is R, `.data`/`.bss`/stack/heap/frames are
R+W. The identity map enforces this at page granularity (D-0019).

**wfi (Wait For Interrupt).** The instruction that hints the hart to sleep
until an interrupt arrives — the polite form of an idle loop, and what our
parked hart executes. With `sie.STIE` set it wakes every 10 ms; that is not
quiescence. QEMU actually idles the host CPU on it, unlike a spin loop.
