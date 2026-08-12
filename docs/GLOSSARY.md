# GLOSSARY

Running list of terms, each defined in 2–3 sentences, in our project's
context. Updated at the end of every milestone (project rule). Alphabetical.

**CLINT (Core-Local Interruptor).** The per-hart block on QEMU `virt` (at
0x0200_0000) holding the `mtime` counter, the `mtimecmp` timer comparators, and
software-interrupt registers. It is M-mode hardware: OpenSBI programs it on our
behalf when we call `sbi_set_timer`, so we never touch it directly.

**CSR (Control and Status Register).** Per-hart special registers (e.g.
`sstatus`, `satp`, `scause`) accessed by dedicated instructions (`csrr`,
`csrw`) rather than loads/stores. They configure privileged behavior and record
trap state; which CSRs are accessible depends on the current privilege level.

**DTB (Device Tree Blob).** A binary description of the machine's hardware
(RAM size, device addresses, interrupt routing) that QEMU generates and OpenSBI
passes to the kernel in register `a1`. Real kernels parse it for portability;
we print the pointer but hardcode the `virt` layout instead (see D-0012).

**ecall.** The RISC-V instruction that requests service from a higher
privilege level by raising a synchronous exception. From S-mode it is how we
call OpenSBI (SBI calls); from U-mode it will be how the app makes syscalls
into our kernel — same instruction, different trap destination.

**hart.** A HARdware Thread — one independent instruction stream with its own
registers and CSRs; a core with hyperthreading would be multiple harts. This
project runs on exactly one hart (hart 0), by constraint D-0007.

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

**Sstc.** The RISC-V extension that gives S-mode a `stimecmp` CSR, so the
supervisor can arm timer interrupts without an `ecall` into M-mode. OpenSBI
v1.3 on QEMU `virt` advertises it as `Boot HART ISA Extensions: time,sstc`.
Recorded for M1; unused until then — M1 still plans to go through the SBI
TIME extension unless a later decision switches to `stimecmp`.

**Sv39.** The smallest rv64 virtual-memory mode: 39-bit virtual addresses
translated through three levels of 512-entry page tables to 4 KiB pages (with
2 MiB / 1 GiB leaves possible at higher levels). 512 GiB of address space —
absurdly more than our 128 MiB of RAM, which is why we don't need Sv48.

**timebase.** The rate of the `rdtime` / `mtime` counter. On QEMU `virt`,
OpenSBI reports `Platform Timer Device: aclint-mtimer @ 10000000Hz` — 10 MHz,
so 100 ns per tick. M1 will convert timeslices into comparator deltas with
this number; unused until then.

**TLB (Translation Lookaside Buffer).** The hart's cache of recent
virtual→physical translations, consulted before walking page tables in memory.
It is what makes paging fast and what makes *stale* mappings possible — hence
`sfence.vma`.

**unikernel.** A single application linked with exactly the OS services it
needs into one bootable image — no processes, no dynamic loading, no
general-purpose userland. Our variant deliberately keeps a U/S privilege
boundary and a 5-syscall interface so the isolation cost can be measured
(D-0010).

**virtio / virtio-mmio.** A standard family of paravirtual devices where guest
and hypervisor share ring buffers (virtqueues) in guest memory instead of
emulating real hardware registers. On `virt`, devices appear as virtio-mmio
slots at 0x1000_1000–0x1000_8000; our M3 NIC is a `virtio-net-device`.

**wfi (Wait For Interrupt).** The instruction that hints the hart to sleep
until an interrupt arrives — the polite form of an idle loop, and what our
scaffold's parked hart executes. QEMU actually idles the host CPU on it,
unlike a spin loop.
