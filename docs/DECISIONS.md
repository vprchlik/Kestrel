# DECISIONS — Architecture Decision Log

Every nontrivial choice gets an entry here **before** the code that implements
it ("nontrivial" = a reviewer could reasonably ask "why not X?"). This log is
the raw material for interview answers and the report's design section.

## Entry format

```
## D-NNNN: <short imperative title>
- Date: YYYY-MM-DD    Status: accepted | superseded by D-MMMM
- Decision: what we will do, one or two sentences.
- Alternatives considered: each with the reason it lost.
- Rationale: why the winner won — argued from project goals
  (legibility, defensibility, scope) and hardware behavior, not fashion.
- Consequences: what this commits us to, what it costs, when to revisit.
```

Entries D-0001 through D-0010 record the project's fixed constraints (locked
at kickoff; do not revisit unless the user explicitly reopens them).
D-0011 onward are working decisions made under those constraints.

---

## D-0001: Write the kernel in Rust with `no_std`
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** the kernel and app are Rust, `#![no_std]` `#![no_main]`, with
  `core` + `alloc` only; unsafe code is allowed but localized and commented.
- **Alternatives considered:** C (the systems lingua franca; rejected: memory
  bugs cost debugging sessions we'd rather spend on OS concepts, and "why Rust
  for OS work" is itself a strong interview topic). C++ (rejected: freestanding
  C++ brings runtime edge cases — exceptions, guards, ABI — without Rust's
  safety payoff). Zig (rejected: attractive for bare-metal but a smaller
  ecosystem and weaker story for the grad-school writeup than Rust's growing
  OS-research presence).
- **Rationale:** Rust gives compile-time memory/aliasing guarantees in the 95%
  of the kernel that doesn't need `unsafe`, a first-class cross-compilation
  story (`riscv64gc-unknown-none-elf` is a rustup-distributed target), and
  `no_std` + `GlobalAlloc` is a clean teaching seam between "language" and "OS
  responsibilities".
- **Consequences:** we own a panic handler, allocator, and entry glue; some
  assembly is unavoidable (entry, trap frame, context switch). No `std`
  conveniences anywhere, ever.

## D-0002: Target rv64gc exactly
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** ISA is RV64GC (= IMAFDC + Zicsr/Zifencei) via the
  `riscv64gc-unknown-none-elf` target.
- **Alternatives considered:** RV32 (rejected: 64-bit matches Sv39 and every
  serious RISC-V OS target; RV32 saves nothing here). RV64IMAC without F/D
  (rejected: avoids FPU-state questions but deviates from the rustup target
  and from what OpenSBI/QEMU default to; not worth a custom target JSON).
- **Rationale:** rv64gc is what QEMU `virt` emulates by default, what the
  prebuilt Rust target ships for, and what real hardware (and Linux distros)
  standardize on — every spec citation and comparison stays apples-to-apples.
- **Consequences:** F/D state exists; until a decision says otherwise, kernel
  and app avoid FP so we can defer FPU context switching (record a decision at
  M2 if that changes).

## D-0003: QEMU `virt` machine with OpenSBI via `-bios default`, single platform
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** the only supported platform is `qemu-system-riscv64 -machine
  virt` booting the bundled OpenSBI (`-bios default`); the kernel is entered in
  S-mode at 0x8020_0000 with `a0`=hartid, `a1`=DTB.
- **Alternatives considered:** writing our own M-mode stub (rejected: educational
  but a milestone's worth of PMP/delegation/counter setup before the first
  print — wrong scope). RustSBI (rejected: same interface as OpenSBI, smaller
  deployment base; nothing learned that OpenSBI doesn't teach). Real hardware
  (rejected: board bring-up variance would eat the schedule; QEMU gives
  determinism and free instrumentation — `-d int`, GDB stub).
- **Rationale:** staying in S-mode above stable firmware is exactly the
  position a real OS occupies; the SBI boundary is small, documented, and
  interview-relevant ("what does firmware do for you?").
- **Consequences:** anything M-mode (PMP, `medeleg`, mtime programming) is
  OpenSBI's job — we interact via `ecall` only. QEMU version gets pinned in
  the M4 report for reproducibility.

## D-0004: Console I/O goes through the SBI console, no raw UART driver
- Date: 2026-08-12 — Status: accepted
- **Decision:** all kernel console output uses the SBI console extension
  (legacy putchar or DBCN — sub-decision recorded when implemented in M0/T0.3).
  We do not write an NS16550A driver unless a later milestone is blocked
  without one.
- **Alternatives considered:** raw NS16550A MMIO driver at 0x1000_0000
  (rejected for now: a fine exercise but duplicates what firmware already does;
  adds an MMIO mapping dependency into M0 that Sv39 work in M1 would then have
  to preserve).
- **Rationale:** minimal-and-legible: one `ecall` wrapper is ten lines and
  works before paging, after paging, and inside trap handlers. The UART itself
  still gets exercised — OpenSBI drives it — satisfying the "UART hello"
  acceptance.
- **Consequences:** console output traps to M-mode on every call (slow — fine
  for a debug console; note it in the M4 report if it shows up in
  measurements). Revisit only if M3 needs interrupt-driven console input.

## D-0005: Sv39 paging
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** virtual memory uses Sv39 (three-level, 39-bit VA, 4 KiB pages),
  enabled from M1 onward.
- **Alternatives considered:** Bare mode / no paging (rejected: forfeits W^X,
  fault isolation for U-mode, and the single most interview-dense subsystem in
  the project). Sv48/Sv57 (rejected: a fourth/fifth level buys address space we
  will never use — 512 GiB is already ~4000× our RAM — and costs one more level
  of walk complexity in every diagram and debugging session).
- **Rationale:** Sv39 is the smallest paging mode every rv64 implementation
  must support and the default assumption of RISC-V OS literature; three levels
  is exactly enough to teach multi-level translation.
- **Consequences:** all mapping code, diagrams, and the report assume 3 levels;
  `satp.MODE=8` hardcoded with a citation.

## D-0006: Single address space, kernel identity-mapped
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** one root page table for the lifetime of the system; kernel
  identity-mapped (VA = PA) with W^X permissions; the app lives in the same
  address space, isolated by the U bit and permission bits, not by separate
  tables.
- **Alternatives considered:** per-task address spaces with `satp` switching
  (rejected: it's the defining feature of a *process* abstraction, which a
  unikernel deliberately discards; would add ASID/TLB churn and copy-in/out
  machinery for zero benefit with one app). Higher-half kernel mapping
  (rejected: classic and useful for real OSes, but with a single address space
  it adds an offset-translation layer to every debugging session for no
  isolation gain here).
- **Rationale:** the unikernel thesis *is* "one app, one address space, cheap
  syscalls" — this decision is the project's identity, and M4 measures its
  payoff against Linux.
- **Consequences:** a buggy app can be stopped by permission bits but tasks
  can't be isolated from each other (fine: M3 has exactly one). U-mode pages
  need the U bit; kernel access to them will require `sstatus.SUM` handling —
  to be recorded in M2 detailing.

## D-0007: Single hart, no SMP
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** the kernel runs on hart 0 only; QEMU launched with `-smp 1`
  semantics (default); secondary harts, IPIs, and locking-for-parallelism are
  out of scope.
- **Alternatives considered:** SMP bring-up via SBI HSM (rejected: doubles the
  complexity of every subsystem — per-hart stacks, real spinlocks, memory-
  ordering audits — while the unikernel story needs none of it).
- **Rationale:** concurrency in this project comes from interrupts, not
  parallelism; that's teachable with interrupt-disable critical sections,
  which we can implement and *fully explain*. SMP correctness is a project of
  its own.
- **Consequences:** "locks" are interrupt-disabling guards; any `static mut` /
  interior-mutability pattern must still be justified against interrupt
  reentrancy in a decision or comment. The M4 comparison runs Linux with 1 CPU
  for fairness.

## D-0008: Device scope is UART + virtio-net, nothing else
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** the only devices the kernel knows exist are the console UART
  (via SBI, per D-0004) and one virtio-net-device on virtio-mmio.
- **Alternatives considered:** virtio-blk (rejected: pointless without a
  filesystem, see D-0009). RTC/GPU/rng/9p (rejected: no milestone needs them).
- **Rationale:** the demo workload (HTTP responder) needs exactly a NIC and a
  console; every additional driver is surface area that competes with the
  writeup for time.
- **Consequences:** the driver layer can be honest-to-goodness simple — no
  device model, no bus abstraction; the virtio-mmio probe in M3 hardcodes the
  QEMU `virt` slot range with a citation.

## D-0009: No filesystem, no dynamic loading, no POSIX compatibility
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** no block storage, no VFS, no ELF loader (the app is linked into
  the image at build time), and the syscall interface is our own 5 calls — not
  a POSIX subset.
- **Alternatives considered:** initramfs-style bundled read-only FS (rejected:
  the app is compiled in; there is nothing to load). Runtime ELF loading
  (rejected: it *is* interesting, but it reintroduces the process abstraction
  the unikernel premise removes). Newlib/POSIX shim (rejected: hundreds of
  stub syscalls to pretend to be Unix, which drowns the actual interface
  design lesson).
- **Rationale:** each rejected feature is a fine project by itself; including
  any of them breaks the 1–3-month solo scope and blurs the unikernel claim
  that M4 needs to measure.
- **Consequences:** the app's only environment is the 5 syscalls; "files"
  don't exist (console is `write`, time is `gettime`); the build system, not
  a loader, binds app to kernel.

## D-0010: One app in-image, running in U-mode over 5 syscalls
- Date: 2026-08-12 — Status: accepted (fixed constraint)
- **Decision:** exactly one application, compiled into the kernel image,
  running as the sole U-mode task over `write`, `exit`, `sbrk`, `gettime`,
  `yield`.
- **Alternatives considered:** single-privilege unikernel — app and kernel all
  in S-mode, syscalls are function calls (rejected, and this is the
  interesting one: it's what MirageOS/Unikraft-style unikernels typically do
  and it's *faster*; we deliberately keep the U/S boundary because (a) building
  privilege transition + trap-based syscalls is a core learning goal, and (b)
  it gives M4 a measurable syscall cost to compare against Linux — a
  function-call "syscall" would make that comparison vacuous). Multiple apps
  (rejected: reintroduces scheduling/isolation policy questions unikernels
  exist to avoid; M2 uses 2+ *kernel-defined* tasks only to prove the
  scheduler, then M3 collapses to one app).
- **Rationale:** goals 1 and 2 both need the U/S machinery to exist and be
  measured; the "unikernel" claim stays honest because the *deployment unit*
  is still a single-purpose image.
- **Consequences:** M4 must report syscall latency as trap-based and may
  discuss the forgone function-call design as future work; the 5-call
  interface is a hard wall — any app need not expressible in it becomes a
  decision entry, not a sixth syscall added casually.

---

## D-0011: Clean exit via SBI system reset (SRST), not the sifive-test device
- Date: 2026-08-12 — Status: accepted
- **Decision:** `shutdown()` calls the SBI SRST extension (EID 0x53525354,
  type=shutdown); QEMU exits with code 0.
- **Alternatives considered:** writing 0x5555 to the sifive-test MMIO device at
  0x0010_0000 (works and allows arbitrary exit codes; rejected as primary
  because it's a QEMU-`virt`-specific back door where SRST is the documented
  firmware interface we already speak — one mechanism family, fewer special
  cases). Keep it in the debugging toolbox for exiting from contexts where SBI
  is unavailable (e.g. broken trap state).
- **Rationale:** consistency with D-0004: talk to firmware until firmware
  can't do the job.
- **Consequences:** distinguishing pass/fail exits from the *test harness* is
  done by matching serial output, not exit codes; revisit if M4 harness needs
  guest-controlled exit codes (then the test device is the tool).

## D-0012: Hardcode the QEMU `virt` memory map; do not parse the DTB
- Date: 2026-08-12 — Status: accepted
- **Decision:** RAM base/size (0x8000_0000 + 128 MiB), UART, PLIC, and
  virtio-mmio addresses are named constants with citations to the QEMU source
  (`hw/riscv/virt.c`); we standardize on QEMU's default RAM size and the
  `justfile` never passes `-m`. The DTB pointer in `a1` is printed at boot but
  not parsed.
- **Alternatives considered:** minimal DTB parse for `/memory` and
  `timebase-frequency` (rejected for now: a flattened-device-tree parser is
  real surface area — strings block, struct walking, endianness — serving
  exactly one platform we already know by heart; D-0003 fixed that platform).
- **Rationale:** minimal-and-legible; the constants are auditable against one
  file of QEMU source; a wrong constant fails loudly and immediately.
- **Consequences:** changing QEMU's `-m` silently breaks the frame allocator's
  idea of RAM end — mitigated by an M1 boot assertion probing that the DTB
  pointer (which QEMU places near end of RAM) is consistent with the constant.
  Revisit only if the project ever targets a second platform (it won't).

## D-0013: Hand-roll the frame allocator and heap allocator; no allocator crates
- Date: 2026-08-12 — Status: accepted
- **Decision:** M1 implements a free-list physical frame allocator and a
  linked-list heap allocator behind `GlobalAlloc`, written in-tree.
- **Alternatives considered:** `linked_list_allocator` / `buddy_system_allocator`
  crates (rejected: they're good code, but goal 1 requires defending the
  allocator in an interview — "I depended on a crate" defends nothing; our
  allocation patterns are tame, fragmentation sophistication buys us nothing).
- **Rationale:** the allocators are small (≈60–150 lines each), high-yield
  teaching artifacts, and the fail-loudly policy (panic on exhaustion with the
  requested size) keeps them honest.
- **Consequences:** we accept worse fragmentation behavior than a buddy
  allocator; if a real workload ever fragments the heap, that becomes a
  documented finding (interesting!) rather than a hidden crate swap.

## D-0014: Minimal and legible beats clever — the tiebreaker rule
- Date: 2026-08-12 — Status: accepted (fixed constraint, meta)
- **Decision:** when two designs both satisfy a milestone, choose the one that
  is shorter to explain from hardware behavior up, even at measured cost in
  performance or generality. Cleverness must buy a milestone requirement or it
  loses.
- **Alternatives considered:** performance-first (rejected: M4 measures, but
  the project's product is *understanding* + a defensible writeup, not
  throughput). Generality-first / "build it like a real OS" (rejected: every
  abstraction layer added "for later" is unfalsifiable scope creep; D-0009
  exists for the same reason).
- **Rationale:** both stated goals (interview defense, research writeup) reward
  a system whose every line has a reason the author can articulate.
- **Consequences:** this entry is the citation for future "why didn't you..."
  questions; deviations from it require their own decision entry.
