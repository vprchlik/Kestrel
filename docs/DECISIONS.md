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
- **Decision:** all kernel console output uses the SBI Debug Console
  extension (DBCN), specifically `console_write_byte` (see D-0015). We do
  not write an NS16550A driver unless a later milestone is blocked without
  one.
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
- Date: 2026-08-12 — Status: superseded by D-0017
- **Decision:** `shutdown()` calls the SBI SRST extension (EID 0x53525354,
  type=shutdown); QEMU exits with code 0.
- **Superseded because:** T0.5 re-opened the choice with the T1.5 mapping cost
  and the harness's pass/fail/hang needs made explicit. D-0017 is the
  decision as implemented.

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

## D-0015: Console bytes go through DBCN `console_write_byte` (FID 2)
- Date: 2026-08-12 — Status: accepted
- **Decision:** kernel console output uses the SBI Debug Console extension
  (EID `0x4442434E` `"DBCN"`), function `console_write_byte` (FID 2). One
  `ecall` per byte. Probe DBCN via BASE `sbi_probe_extension` (EID `0x10`,
  FID 3) before the first write; if it is absent, abort (no legacy fallback).
- **Alternatives considered:** legacy `sbi_console_putchar` (EID `0x01`, no
  FID — rejected: deprecated, and it does not teach the `a7`/`a6` convention
  SRST and TIME will use). DBCN `console_write` (FID 0, a whole buffer from a
  physical address — deferred: same `Write::write_str` shape wants bytes, and
  buffer-write is an optimization if console volume ever shows in M4 numbers).
  Raw NS16550A MMIO (rejected in D-0004).
- **Rationale:** DBCN is the current spec and the interface that survives;
  FID 2 is the same shape as `core::fmt::Write` and ~10 lines; the calling
  convention is the one every later SBI call will use.
- **Consequences:** every printed byte traps to M-mode and back (slow; fine
  for a debug console). A missing DBCN is a hard abort, not a silent fallback
  to EID `0x01`. Revisit FID 0 only if M4 measurements blame console `ecall`
  volume.

## D-0016: Unmapped guard page below the boot stack (M1/T1.5)
- Date: 2026-08-12 — Status: accepted (implement at M1/T1.5, not before)
- **Decision:** once Sv39 is live, leave one 4 KiB page immediately below
  `__boot_stack_bottom` unmapped so a stack overflow takes a store page fault
  instead of silently corrupting whatever sits there.
- **Alternatives considered:** keep the stack adjacent to `.bss` with no gap
  (status quo until paging exists — there is no translation, so an unmapped
  page cannot fault). A mapped guard with no W bit is equivalent on this
  hardware; unmapped is simpler (no PTE to get wrong).
- **Rationale:** today `__boot_stack_bottom` sits exactly at `__bss_end`.
  Overflow walks downward into `.bss` (and then into `.data` / `.rodata` /
  `.text`) with no trap. That is undetectable until some static is impossibly
  wrong. Paging is the first moment the hardware can tell us.
- **Consequences:** do **not** implement this in M0. The linker hole lands in
  M1/T1.5 and the mapping in M1/T1.6; the frame allocator must not hand that
  page out. Revisit the linker script then if the gap needs a named symbol.

## D-0017: Shut down via SBI SRST; harness parses serial, not exit codes
- Date: 2026-08-13 — Status: accepted (supersedes D-0011)
- **Decision:** `shutdown()` probes and calls the SBI System Reset extension
  (EID `0x53525354` `"SRST"`, FID 0, type=shutdown, reason=none). We accept
  that this yields **no guest-controlled exit code**: QEMU exits 0 on
  shutdown. Pass vs fail vs hang is distinguished by the test harness
  parsing serial (`M0 BOOT OK` / `PANIC` / timeout), not by `echo $?`.
- **Alternatives considered:** sifive_test MMIO at `0x0010_0000` (store
  `0x5555` = exit 0, `(code << 16) | 0x3333` = exit `code` — rejected as
  primary: QEMU-`virt`-only, and T1.5 would have to identity-map that page
  W at the exact moment paging is already the project's hardest step). Keep
  it in the debugging toolbox for contexts where SBI is unreachable.
- **Rationale:** SRST is the firmware interface that survives onto real
  hardware and needs no extra Sv39 mapping. The extra exit-code channel
  buys nothing the harness does not already get from serial + timeout.
- **Consequences:** a panic that parks looks like a hang to `timeout` unless
  serial is grepped for `PANIC` **first** — `just test` does that. Verdicts:
  marker + QEMU exit 0 → `TEST PASS` (exit 0); `PANIC` in serial →
  `TEST FAIL` (exit 1, panic line echoed); timeout without panic →
  `TEST HANG` (exit 2). A failed SRST call prints a reason and parks
  (HANG, with that line). Revisit sifive_test only if a later harness
  truly cannot parse serial.

## D-0018: Arm timers through the SBI TIME extension, not Sstc `stimecmp`
- Date: 2026-08-13 — Status: accepted
- **Decision:** M1 arms the timer with the SBI TIME extension
  (EID `0x54494D45` `"TIME"`, FID 0, `sbi_set_timer(absolute_deadline)`),
  probed via BASE exactly like DBCN and SRST. The arm lives in **one
  function** so that M4 can add an Sstc variant behind a build flag as a
  one-site change and report both numbers.
- **Alternatives considered:** writing `mtimecmp` in the CLINT directly
  (impossible, not merely discouraged: OpenSBI's PMP prints
  `Region00: 0x02000000-0x0200ffff M: (I,R,W) S/U: ()`, so the store raises an
  access fault, and access faults are not delegated — our handler would never
  see it). Sstc's `stimecmp` CSR (one `csrw`, no trap, and what Linux uses on
  this platform — rejected for M1: it cannot be probed from S-mode. `misa` and
  `menvcfg.STCE` are M-mode-only, a write to an unimplemented or disabled CSR
  raises illegal instruction, and code 2 is **not delegated**, so we cannot
  catch our own probe failing. The boot banner's `sstc` line is a log message,
  not a runtime API, and the only programmatic source is the device tree,
  which D-0012 declines to parse). Implementing both with runtime selection
  (rejected: needs the same unavailable probe, and doubles the code path in
  the milestone whose purpose is learning traps).
- **Rationale:** the deciding asymmetry is observability, not cost. At a 10 ms
  tick the firmware round trip is on the order of 0.01% of a core, and even a
  1 ms M2 timeslice leaves it under 0.2% — anyone calling SBI TIME "too slow"
  here is arguing from intuition rather than a number. What differs is failure
  behavior: a missing TIME extension is a probe returning zero, which we print
  and abort on in the same idiom as DBCN and SRST; a missing Sstc is an
  illegal-instruction trap that OpenSBI absorbs into a dump we did not write
  and cannot annotate. Committing to an unprobeable capability during the
  milestone about handling traps correctly is backwards.
- **Consequences:** every arm costs an `ecall` round trip through M-mode. The
  M4 report must either match the Linux baseline's mechanism or treat the
  difference as a measured quantity — we choose the latter, which is strictly
  more informative ("we measured the cost of the firmware round trip on our
  own kernel" is a finding, whereas silently matching Linux is only a
  control). `sip.STIP` is not write-clearable from S-mode under either
  mechanism: re-arming is the acknowledgement.

## D-0019: Map all of RAM R+W once; keep the intrusive frame free list
- Date: 2026-08-13 — Status: accepted
- **Decision:** the kernel address space identity-maps all of RAM except
  OpenSBI's region and the D-0016 guard page: `.text` R+X, `.rodata` R,
  everything else R+W, A+D set on every leaf. The frame allocator stores each
  free frame's successor in that frame's own first 8 bytes, so its total
  metadata is one head pointer in `.bss`.
- **Alternatives considered:** mapping only allocated frames, with a bitmap
  instead of an intrusive list (rejected: it reintroduces a genuine recursion
  — mapping a page requires allocating a table frame, which requires mapping
  it, which may require allocating another table frame — and it adds a map
  call to every allocation path). Pairing map-on-demand *with* the intrusive
  free list (rejected as incoherent: the list lives inside the memory it
  manages, so traversing it dereferences unmapped frames; if you want
  map-on-demand you must take the 4 KiB bitmap with it).
- **Rationale:** the isolation that map-on-demand buys is isolation of the
  kernel from itself, and with a single address space and one application
  (D-0006, D-0010) there is no second principal to protect against. What we
  get in exchange is that the hardest step in the milestone — activating
  paging — happens over a map with no ordering dependencies inside it, and
  page-table frames are addressable the moment they are allocated. D-0014
  points the same way.
- **Consequences:** a stray kernel pointer into an unallocated frame succeeds
  silently instead of faulting; the guard page is the one deliberate
  exception, and W^X still holds for text and rodata. Page tables live in the
  R+W region they describe, which is self-referential but harmless — we are
  not defending against a malicious kernel.

## D-0020: Register-indexed `TrapFrame` on the kernel stack, `stvec` Direct
- Date: 2026-08-13 — Status: accepted
- **Decision:** `#[repr(C)] struct TrapFrame { x: [usize; 32], sepc: usize,
  sstatus: usize }` — all 31 GPRs (`x0` is hardwired zero and never saved,
  its slot stays unused) plus `sepc` and `sstatus`, at offsets `8 * regnum`,
  272 bytes total (already 16-byte aligned as the ABI requires). The frame is
  built on the current kernel stack. `stvec` is set in **Direct** mode. Named
  accessors wrap the array for the registers we discuss by name (`a0`–`a7`
  are `x10`–`x17`). `x2` holds the *pre-trap* `sp`, computed with one extra
  `addi`, so fault reports show the stack pointer at the moment of the fault.
- **Alternatives considered:** named struct fields (`ra`, `sp`, `t0`, `a0`, …
  — reads better in Rust, but desynchronizes silently the first time someone
  reorders the struct without editing the assembly offsets; register-indexed
  offsets are derivable from the register name, so drift is not expressible).
  Saving only the caller-saved set (rejected: whether that is sufficient
  depends on whether we arrived by interrupt or by a call-like exception, and
  getting that reasoning wrong produces corruption that surfaces far from the
  trap; 31 stores cost nanoseconds). Vectored `stvec` (rejected: spreads
  dispatch across a table of entry points that M2's rework would then have to
  touch individually).
- **Rationale:** the hardware saves `sepc`, `scause`, `stval`, and two
  `sstatus` bits, and nothing else — all 31 GPRs still hold the interrupted
  code's values, so preserving them is entirely ours, which is why entry and
  exit are assembly. `sepc` and `sstatus` are saved despite being CSRs
  because a trap taken *inside* the handler (an unknown-cause panic that
  itself page-faults) overwrites them.
- **M2-proofing constraints — these are constraints on M2's design session,
  not suggestions:**
  1. **Entry is four separable blocks:** establish the kernel stack pointer,
     save the frame, call Rust, restore and return. In M1 the first block is
     *empty with a comment saying so*; in M2 it becomes the `sscratch` swap
     (`csrrw sp, sscratch, sp`) and nothing else in the entry changes.
  2. **`sstatus` is saved in the frame** partly so M2 can read `SPP` to learn
     whether the trap came from U-mode or S-mode.
  3. **Instruction-width decoding takes the instruction bits as an argument**,
     never dereferencing `sepc` internally (see D-0021 for why).
  4. **`sscratch` is left meaningless in M1.** M2 decides whether it holds the
     kernel stack pointer or the current task's frame pointer, and that
     depends on a task-control-block design that does not exist yet.
- **Consequences:** `stvec`'s target must be 4-byte aligned or the low two
  bits silently become a mode field. The handler never enables interrupts
  inside itself — hardware already cleared `sstatus.SIE` on entry and we do
  not set it, so there is exactly one trap level in M1.

## D-0021: Advance `sepc` by decoded instruction width, never on interrupts
- Date: 2026-08-13 — Status: accepted
- **Decision:** for exceptions we resume *past*, the handler adds the trapped
  instruction's width, decoded from the low two bits of the instruction
  halfword (`0b11` ⇒ 4 bytes, otherwise 2). The decode helper **takes the
  instruction bits as an argument**; the read stays at the call site, where
  the address space is known. For interrupts, `sepc` is never modified. In M2
  the `ecall` path uses the constant 4 with a comment citing that `ecall` has
  no compressed encoding, so the syscall path never reads user memory at all.
- **Alternatives considered:** always `sepc += 4` (rejected: correct for
  `ecall` and `ebreak`, wrong for any compressed instruction — OpenSBI can
  hardcode 4 after an S-mode `ecall` precisely because that instruction has
  no RVC form, and copying the shortcut to a general handler skips a byte and
  lands a later `sret` mid-instruction). A helper that dereferences `sepc`
  itself (rejected: in M2 `sepc` on an `ecall` trap is a *user* virtual
  address, and an S-mode load from a U=1 page faults unless `sstatus.SUM` is
  set — that design inherits either a fault or a hidden SUM dependency in the
  syscall hot path).
- **Rationale:** `sepc` points *at* the faulting instruction for exceptions,
  so returning without advancing re-executes it forever; for interrupts it
  points at an instruction that has not run yet, so advancing skips it
  silently until the consequence is inexplicable. Two rules, one CSR.
- **Consequences:** the width decode is exercised in M1 only by the `ebreak`
  continue-past test, which is enough to prove it; M2's syscall path
  deliberately avoids needing it.

## D-0022: Clear `sstatus.SIE` across the `satp` switch
- Date: 2026-08-13 — Status: accepted
- **Decision:** T1.7 clears `sstatus.SIE`, writes `satp`, executes
  `sfence.vma`, then restores the previous `SIE`.
- **Alternatives considered:** reordering M1 so paging (T1.7) precedes timer
  interrupts (T1.3) (rejected: traps and timers are the natural teaching
  order, and the frame allocator that paging depends on wants the trap handler
  working first). Leaving interrupts enabled and trusting the window to be
  short (rejected: it is exactly the window in which an unvalidated mapping
  would be exercised, and the failure is the silent trap loop).
- **Rationale:** ticks have been arriving every 10 ms since T1.3. A timer
  interrupt taken between the `csrw satp` and the `sfence.vma` vectors through
  `stvec` under a translation regime we have not yet validated — precondition
  12 of prerequisite concept 11. Three lines of CSR manipulation remove an
  entire class of nondeterministic hang.
- **Consequences:** a tick can be missed across the switch, which is
  irrelevant to any M1 acceptance criterion. Any future code that changes the
  active address space inherits the same requirement.

## D-0023: Hardcode RAM end, validate the DTB header, treat the DTB as clobberable
- Date: 2026-08-13 — Status: accepted (refines D-0012)
- **Decision:** `RAM_END = 0x8800_0000` stays a named constant (D-0012). At
  boot, before the frame allocator is initialized, read two big-endian `u32`s
  from the DTB pointer in `a1`: the magic (`0xd00dfeed`) and `totalsize`. If
  the magic is wrong, or `a1 + totalsize` falls outside the assumed RAM
  window, panic printing both values. The DTB region is then **explicitly
  clobberable** — it sits at `0x87e0_0000`, inside the range handed to the
  frame allocator, and we never parse it.
- **Alternatives considered:** the heuristic D-0012 originally suggested,
  "the DTB pointer looks like it is near the top of RAM" (rejected: that is a
  coincidence of QEMU's loader, not a guarantee, so it can pass while being
  meaningless). A real `/memory` parse (rejected: 150–250 lines of
  structure-block walking, strings block, big-endian decoding, and
  `#address-cells` handling, to buy portability D-0003 already declined).
  Probing memory by touching it (rejected, and the reason is instructive:
  with paging off and PMP's catch-all region permitting everything, a load
  above RAM end on QEMU `virt` hits unassigned space, which logs a
  `guest_errors` line and returns zero rather than faulting — a probe that
  cannot fail cannot find the boundary).
- **Rationale:** ten lines of header check catch the realistic failure (`-m`
  changed, or `a1` is not what we think) without a parser, and they fail
  loudly with the numbers needed to diagnose.
- **Consequences:** **ordering constraint** — the sanity check must run before
  allocator init, because afterwards the blob may be handed out as frames and
  the check would read heap. Written here so that nobody in M3 wonders why the
  device tree turned into allocated memory. The `justfile` must continue never
  passing `-m`.

## D-0024: Reserve a fixed 1 MiB heap region before building the free list
- Date: 2026-08-13 — Status: accepted
- **Decision:** the heap is a fixed 1 MiB region carved immediately above
  `__kernel_end`, reserved *before* the frame free list is built, so the free
  list simply starts above it. The heap is mapped R+W like the rest of RAM
  (D-0019); the `GlobalAlloc` implementation manages blocks inside it.
- **Alternatives considered:** popping 256 frames from the allocator at init
  (rejected: contiguity would depend on the order the free list happened to be
  built in — a correctness property resting on an allocator implementation
  detail). A `static` array in `.bss` (rejected: inflates the image's `.bss`
  and hides the heap from the physical-memory accounting we want to report).
  Growing the heap on demand (rejected: `sbrk` in M2 is the app's break, not
  the kernel heap's; a fixed kernel heap is one fewer moving part).
- **Rationale:** carving first makes both regions trivially non-overlapping by
  construction, and 1 MiB is far more than M1's `Box`/`Vec`/`String` self-test
  or M2's task structures need, while leaving ~125 MiB of frames.
- **Consequences:** heap exhaustion panics rather than growing (there is no
  OOM recovery story in a unikernel). If M3's network buffers want more, the
  constant changes and the decision gets an amendment.

## D-0025: Map no MMIO in M1
- Date: 2026-08-13 — Status: accepted (amends the pre-M0 T1.5 plan text)
- **Decision:** the M1 kernel address space maps no device memory at all — no
  UART page, no virtio-mmio range, no sifive_test.
- **Alternatives considered:** mapping the UART and the virtio slots "for
  later", as the original pre-M0 plan text said (rejected: we never touch the
  UART because D-0004 routes the console through SBI, and virtio belongs to
  M3; mapping devices we do not use contradicts the standing rule against
  implementing beyond the current milestone, and every unused mapping is a
  window a stray pointer can hit without faulting).
- **Rationale:** the console is an `ecall`, which needs no virtual address,
  and OpenSBI's region stays unmapped deliberately so a stray access there is
  a page fault we decode rather than an access fault firmware absorbs. M3 adds
  the virtio pages in the same task that adds the driver, where the
  permissions can be justified against the code that uses them.
- **Consequences:** the sifive_test escape hatch that D-0017 keeps in the
  debugging toolbox is unusable after paging is on until someone maps that
  page by hand; the supported emergency exit post-T1.7 is SBI SRST, which is
  an `ecall` and needs no mapping. Note this in DEBUGGING.md if it ever comes
  up in practice.

## D-0026: Map every region with 4 KiB leaves; no superpages
- Date: 2026-08-13 — Status: accepted
- **Decision:** the M1 kernel address space is built entirely from 4 KiB
  (level-0) leaves. No 2 MiB or 1 GiB superpage leaves.
- **Alternatives considered:** 1 GiB leaves for the RAM window (rejected: a
  1 GiB page at `0x8000_0000` would cover OpenSBI, the guard hole, and every
  W^X boundary in one PTE — the permissions in the PLAN memory-map table
  cannot be expressed). 2 MiB leaves for the aligned interior of
  `[__heap_end, RAM_END)` with 4 KiB leaves everywhere else (rejected: it is
  a second mapping path whose failure mode is concept 11.4 — a non-leaf with
  any of R/W/X set *is* a superpage, and a misaligned PPN on that leaf
  faults). `__heap_end` is not 2 MiB-aligned (`0x8031_8000` after T1.5), so
  the mixed path is mandatory if superpages are used at all, not optional.
- **Rationale:** kernel W^X and the 4 KiB guard already force 4 KiB
  granularity across the image. One leaf size means one walk, one verifier,
  and one PPN-shift. ~32k identity maps at boot are cheap next to that.
  D-0014.
- **Consequences:** the software walker in T1.6 panics if a translation
  resolves at level 1 or 2. Revisit only if a later milestone has a real
  reason to map a huge contiguous R+W region at a coarser grain — and then
  only with an explicit alignment check on the leaf PPN.

## D-0027: Address-sorted heap free list, coalesce on free, first-fit
- Date: 2026-08-13 — Status: accepted
- **Decision:** the kernel heap is a first-fit free list of variable-size
  blocks over `[__heap_start, __heap_end)`, kept sorted by address.
  `dealloc` coalesces with the previous and next block when they are
  adjacent. The block header sits immediately before the aligned user
  pointer. Prefix bytes needed to satisfy `Layout::align` are split back
  into the free list when they are large enough to hold a header; otherwise
  they are absorbed into the allocated block (recorded as pad, recovered on
  free). Exhaustion panics with the requested size and alignment; `alloc`
  never returns null.
- **Alternatives considered:** no coalescing (rejected: Vec growth frees
  each previous buffer next to the last one, and without a merge those
  holes cannot satisfy the next doubling — the 1 MiB tail would hide it
  until a later workload). Best-fit (rejected: more walk, same M1
  workload). A bitmap or buddy over the same 1 MiB (rejected: D-0013 /
  D-0014 — the linked list is the thing we have to defend). Returning
  null from `GlobalAlloc` and relying on `handle_alloc_error` (rejected:
  the panic message would not include size and align).
- **Rationale:** an address-sorted list makes adjacency a pointer
  comparison at insert time, so coalescing is the cheap correctness
  property rather than an extra pass. First-fit plus coalesce is the K&R
  allocator; that is the interview explanation.
- **Consequences:** a stream of mixed-size alloc/free that never produces
  adjacent holes can still fragment until a request fails with free bytes
  remaining. M1's self-test does not hit that; if M2/M3 does, it is a
  finding, not a silent crate swap.

## D-0028: Trap handlers must not allocate
- Date: 2026-08-14 — Status: accepted (constraint on M2's design session)
- **Decision:** neither the heap nor the frame allocator may be entered
  from a trap handler. That is an invariant, not an implementation of
  mutual exclusion. The 1 ms allocator storm restored one coalesced heap
  block and the starting frame free-list length with ticks live; it did
  not add a lock. Allocator logic stays as it is until M2 picks a
  mechanism.
- **Current enforcement (honest):** the heap sets `IN_ALLOC` around
  `try_alloc` / `insert_coalesced` and panics on re-entry
  (`heap re-entered: size={} align={}`). That is a detector, not a
  critical-section lock — it does not clear `sstatus.SIE`. Frames have
  **no** detector. `alloc_frame` reads `HEAD`, copies the next pointer,
  stores `HEAD`, then zeros 4 KiB; `free_frame` writes the old head into
  the frame and then stores `HEAD`. A nested `alloc_frame` between the
  read and the store double-allocates the same PA or drops a list node;
  an interrupt between `free_frame`'s two stores corrupts the LIFO list.
  Both paths are silent. Hardware already clears `SIE` on trap entry, so
  the handler itself is not re-interruptible; the race is the interrupted
  *caller* sitting in the middle of a list mutation when the handler
  mutates the same list.
- **Held only by the handler's current contents.** `trap_handler` calls
  `timer::on_interrupt`, which bumps a counter, programs the next
  deadline, and sometimes `println!` (SBI DBCN bytes from the stack, no
  `GlobalAlloc`, no `alloc_frame`). Nothing in that path touches either
  free list. The storm exercised interrupt-during-mutation under that
  contract and found no corruption. The contract is unenforced: any later
  edit that allocates from the trap path breaks it without a compile
  error.
- **Alternatives considered:** masking `SIE` around frame-list mutation
  now (rejected for this entry: the storm did not find a bug, and this
  task was evidence, not a lock). A frame-side `IN_ALLOC` twin now
  (rejected for the same reason; it would also only catch nesting, not
  a handler that allocates while the caller is *not* in `alloc_frame`).
  Declaring the invariant "good enough because M1's handler is small"
  without recording it (rejected: M2 puts allocation on the trap path
  and the finding would be rediscovered as a Heisenbug).
- **Rationale:** ticks have been live since T1.3, before `frame::init`.
  M1 can keep the invariant by construction because the only trap work
  is a counter and an SBI call. M2 cannot: `ecall` dispatch is a trap,
  and a scheduler that pops frames for task stacks (or a `sbrk` that
  maps them) runs in that path. Once the handler allocates, a timer
  taken in the middle of `alloc_frame` is no longer a harmless
  `on_interrupt` — it is a second walker of an unguarded intrusive list.
- **M2-proofing constraints — these are constraints on M2's design
  session, not suggestions. Do not write M2 code until one of the
  following is an accepted decision:**
  1. **Mask `sstatus.SIE` around frame-list mutation** (`HEAD` read
     through the `HEAD` store in `alloc_frame`; the two stores in
     `free_frame`). Defers a timer until the list is consistent. Does
     not by itself forbid a handler from allocating *after* the mask
     drops.
  2. **A frame-side re-entry detector mirroring `IN_ALLOC`**, panicking
     on nested `alloc_frame` / `free_frame`. Loud, like the heap. Does
     not make the list atomic if the handler allocates while the caller
     is *outside* the allocator, and does not close the interrupt window
     for a non-allocating handler.
  3. **Preallocate everything the trap path could need** (syscall
     scratch, task stacks, whatever `sbrk` / the scheduler would pop)
     so the invariant holds by construction the way M1's timer path
     does. Allocation stays out of `__trap_entry` and `trap_handler`.
- Pick one, or a combination, in the M2 design session. Do not pick
  here. Heap `IN_ALLOC` stays; this entry does not ask M2 to invent a
  second heap policy unless the chosen option forces it.
- **Consequences:** until M2 records that follow-up, adding a `Box`, a
  `Vec`, or an `alloc_frame` under `trap_handler` / `timer::on_interrupt`
  is a bug even if it appears to work. The `just test-stress` storm is
  evidence that the *current* handler is safe, not a license to grow it.
