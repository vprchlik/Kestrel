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

## D-0029: `sscratch` holds the kernel stack top in U-mode and zero in S-mode
- Date: 2026-08-14 — Status: accepted
- **Decision:** `sscratch` holds the current task's kernel stack top while
  that task executes in U-mode, and is exactly 0 whenever the hart executes
  in S-mode. D-0020 block 1 becomes `csrrw sp, sscratch, sp` followed by
  `bnez sp, 1f` and, on the not-taken path, a second `csrrw` that undoes the
  first. On the U path only, the entry then stores the trapped `sp` (now in
  `sscratch`) into the frame's `x[2]` slot and reloads the kernel's `gp` with
  relaxation disabled. `trap::install()` writes `sscratch = 0` **before** it
  writes `stvec`.
- **Alternatives considered:** reading `sstatus.SPP` to discriminate
  (rejected: `csrr` needs a destination GPR and at the first instruction of
  the handler every GPR still holds the interrupted context — the swap has to
  come first, and once it has, branching on the swapped-in value is free).
  Keeping a task-control-block pointer in `sscratch` at all times, including
  in S-mode (rejected: then a nested trap from S-mode reuses the same kernel
  stack top and overwrites the outer frame; the zero-in-kernel convention is
  what makes the S path self-restoring). Vectored `stvec` with separate entry
  points (rejected: D-0020 fixed Direct mode, and this would spread the same
  discrimination across a table).
- **Rationale:** on a trap from U-mode all 31 GPRs still hold user values,
  `sp` included, and S-mode stores to `U=0` pages are legal — so a frame
  pushed at the trapped `sp` lets a task nominate kernel memory as the spill
  target and the hardware will not stop it. `sscratch` is the only
  architectural slot the handler owns. The `gp` reload is not cosmetic:
  `_start` loads `gp` with `norelax` precisely so the linker may relax kernel
  absolute loads into `gp`-relative ones, so kernel Rust reached from a
  trap-from-U with the user's `gp` reads the wrong addresses, with no fault and
  no proximity to the cause. `tp` needs no such reload — it is the thread
  pointer, used only for thread-locals, which `no_std` without TLS never
  emits, so the kernel never reads it and saving/restoring it as an ordinary
  GPR is sufficient.
- **Exit-path ordering is part of this decision.** Any instruction executed
  while `sscratch` holds the *user* `sp` in S-mode would misclassify a kernel
  exception as a trap-from-U and push a frame at that address. The exit
  therefore writes user `sp` into `sscratch` as late as it can: after all
  GPRs except `t0` are restored, and after `addi sp, sp, 272` (which
  reconstructs the pre-trap `sp` on the S path and yields `kstack_top` on
  the U path), a branch on saved `sstatus.SPP` covers the two `sscratch`
  instructions. The S path restores `t0` and `sret` with `sscratch` still 0.
  The U path is:

      ld      t0, -256(sp)       # x[2] = user sp; sscratch still 0
      csrw    sscratch, t0       # park — window starts
      ld      t0, -232(sp)       # restore t0 from the frame
      csrrw   sp, sscratch, sp   # swap; sscratch = kstack_top
      sret

  The S-mode-with-user-`sscratch` window is **three instructions, not one**.
  It cannot be shorter: `t0` must be reloaded from the frame *before* the
  `csrrw`, because afterward `sp` points at the user stack and the frame is
  no longer at a known offset from `sp`, and every other GPR already holds
  the interrupted context so there is no spare register to hoist the load.
  The `ld` can only fault if the kernel stack is already blown, which is
  the D-0030 unrecoverable hang — so the window is **safe but not
  fault-free**. `csrw` / `csrrw` touch no memory. After the swap, `sret`
  also runs with `sscratch != 0`, but that value is `kstack_top` (the
  U-mode-ready one) and `sret` cannot fault. `x[2]` stays in the frame as
  the diagnostic copy the panic printer already reports.
- **Consequences:** `sscratch = 0` is now a kernel-wide invariant that any
  future S-mode code path must preserve; the boot CSR snapshot prints
  `sscratch` so firmware garbage is visible rather than assumed. The S path
  costs one extra `csrrw` on every kernel-side trap, which is the price of
  needing no scratch register.

## D-0030: Per-task kernel and user stacks are static and linker-placed, with guard holes
- Date: 2026-08-14 — Status: accepted
- **Decision:** each of `MAX_TASKS` (4) task slots gets an 8 KiB kernel stack
  and an 8 KiB user stack, both NOLOAD and placed by the linker script between
  the boot stack and `__kernel_end`, each with a 4 KiB unmapped guard hole
  immediately below it. Task creation allocates nothing.
- **Alternatives considered:** kernel stacks from `frame::alloc_frame()` at
  task creation (rejected on a property of *our* allocator: the free list is
  LIFO over an intrusive list (D-0019), so two frames are not adjacent — an
  8 KiB stack needs contiguity and a guard needs the frame numerically below
  the stack, neither of which the allocator can promise. Making it promise
  them means adding a contiguous-run allocator to serve one caller, which
  D-0014 loses). A single 4 KiB kernel stack per task to avoid the contiguity
  question (rejected: debug builds plus `println!` formatting plus the
  nested-panic path do not fit comfortably, and the failure mode is the silent
  hang below). One shared kernel stack for all tasks (rejected: it only works
  if no task is ever suspended with kernel state live, which is true under
  D-0032 today — but it would make D-0032 load-bearing for memory safety
  rather than for scheduling policy, and M3 would inherit that coupling).
- **Rationale:** static placement buys contiguity and guard holes from the
  linker the same way `.boot_stack` already does (D-0016), and it makes task
  creation allocation-free, which is most of D-0036's answer. It also
  establishes the invariant the trap path depends on: **a task's kernel stack
  is empty whenever that task is in U-mode**, so `sscratch` is a constant per
  task and the frame always lands at `kstack_top - 272`.
- **Kernel stack overflow is NOT handled, and the guard does not make it a
  diagnostic.** Trace it: the overflowing store raises a store page fault from
  S-mode, so `sscratch` is 0, so trap entry keeps the faulting `sp` — already
  inside the guard hole — and block 2's `addi sp, sp, -272` plus its stores go
  through the same hole and fault again. That re-enters `__trap_entry`
  identically, forever. Rust is never reached, so the panic printer and its
  `IN_PANIC` guard never run: **no output, no `scause`, nothing.** This is the
  fault-the-fault-forever case from M1/T1.2 (DEBUGGING.md §4, M1 item 4). The
  guard therefore converts silent corruption of a neighbouring task's stack
  into a silent hang — the damage stops, but nothing is reported. One further
  bound: the guard only guarantees even that much while the overflowing stack
  frame is smaller than the 4 KiB hole; a single frame larger than the guard
  can step clear over it and land the entry's 272-byte push in mapped memory
  below, which is silent corruption again.
- **The standard fix, which M2 does not implement:** a separate double-fault
  stack — on every trap from S-mode, range-check `sp` against the current
  task's kernel stack bounds and switch to a reserved emergency stack when it
  is out of range, so the fault report has somewhere to run. x86 does this in
  hardware (IST); RISC-V S-mode has no equivalent, and there is only one
  `sscratch`, so the second slot would have to be a fixed memory location
  reached by an absolute or `gp`-relative load, plus a comparison, on every
  kernel-side trap. That is a real cost in the hottest path to diagnose a bug
  that should be prevented by keeping frames small. Revisit if a kernel stack
  overflow ever actually costs a debugging session.
- **Consequences (including an M4 threat to validity):** the reserved
  footprint is 8 KiB user stack + 8 KiB kernel stack + two 4 KiB guards +
  64 KiB break window (D-0031) ≈ **88 KiB per task slot**, ~352 KiB for
  `MAX_TASKS = 4`, all NOLOAD. That does not inflate the image, but it *is*
  physical RAM committed up front, so the guest-reported memory footprint M4
  measures against a demand-paged Linux is higher than a fault-driven design
  would report. **M4's report must state this as a methodology difference
  rather than discover it in the numbers:** we commit stacks and break windows
  at link time; Linux reserves address space and populates on fault, so the
  fair comparison is either against Linux's resident set or accompanied by an
  explicit note that our number is a reservation, not a working set.
  Signature and first-response for the overflow hang are in DEBUGGING.md §4.

## D-0031: Separate user sections with the `U` bit; no PTE edits after activation
- Date: 2026-08-14 — Status: accepted
- **Decision:** user code and data live in their own linker sections —
  `.utext` (R+X+U), `.urodata` (R+U), `.udata`/`.ubss` (R+W+U) — plus per-task
  user stacks and a 64 KiB per-task break window (R+W+U). All of it is mapped
  by `page::build` at boot, beside the kernel map. **No page-table entry is
  edited after `page::activate`.**
- **Alternatives considered:** marking existing identity-mapped frames `U=1`
  at task creation with a new `page::set_user(va)` (rejected: it needs a
  remap path — today's `map` panics on remap by design (D-0026) — plus an
  `sfence.vma` site, and it puts page-table mutation in the same milestone
  that puts allocation near the trap path; D-0036 wants the opposite
  direction). Placing user code in kernel `.text` and setting `U=1` there
  (impossible, not merely undesirable: S-mode cannot fetch from a `U=1` page,
  so the kernel would stop being able to execute its own text). A kernel
  alias mapping of user buffers at a second `U=0` virtual address so the
  kernel could read them without `SUM` (rejected: it breaks VA = PA for those
  pages and reintroduces the two-views-of-one-buffer bookkeeping a single
  address space (D-0006) exists to avoid, to save two CSR writes).
- **Rationale:** the `U` bit is per-PTE and per-page, so "user code" is a
  placement question before it is a permission question — the sections are the
  minimal way to answer it. Building the whole map at boot keeps the one
  hard-won property from M1 intact: the address space is validated in software
  (T1.6/T2.2) *before* anything runs on it, and it never changes afterwards,
  so there is no TLB-shootdown story and no mapping code reachable from a trap.
- **Consequences:** demo tasks must be written so every symbol referenced
  from `.utext` resolves inside the user sections or the task's own
  stack/break window — no string literals landing in kernel `.rodata`, no
  compiler-emitted `memcpy` call into kernel `.text`, no `gp`/`tp`-relative
  access. `just check-utext` enforces that by resolving `jal`/`auipc+addi`
  (and friends) against those ranges; it does **not** ban `auipc` or `lui`.
  Those instructions are how a legitimate `.urodata` buffer is addressed.
  A `lui` immediate used as a value (a kernel address passed to `write`)
  is not a symbol reference. T2.5 used to read this as "no `auipc`", which
  was only right while `write` was a stub. The 64 KiB break window per
  task is part of the ~88 KiB static reservation recorded in D-0030,
  including its M4 threat-to-validity note.
  **Defense in depth on the break:** `__ubrkN_wall` is the same address as
  `__kstackN_guard`. A `sbrk` bound-check bug that hands out one page past
  the wall therefore lands in the kernel stack's unmapped guard (a store
  page fault from U-mode, which T2.10 kills as a task) rather than in the
  kernel stack itself (`U=0`, S-mode writable). That is not a substitute
  for the software check; it is what makes a missed check a contained
  U-mode fault instead of kernel-stack corruption. M3 replaces the
  in-kernel demo tasks with an app crate linked into these same sections;
  that is a build-integration change, not a mapping change.

## D-0032: Switch at trap exit; the trap frame *is* the task context
- Date: 2026-08-14 — Status: accepted
- **Decision:** `trap_handler` changes from taking `&mut TrapFrame` to
  *returning* the frame to resume; block 4 gains `mv sp, a0` before the
  restore sequence. A context switch is therefore "the handler returned a
  different frame pointer than it was given". There is no `swtch`-style
  assembly routine and no second saved-context format: the task control block
  stores no register state, only where its frame lives. The fabricated frame
  for a new task sets `sepc` = entry, `sstatus.SPP` = 0, `SPIE` = 1,
  `x[2]` = `ustack_top`, and `gp` = `tp` = 0.
- **Alternatives considered:** an xv6-style
  `switch(&mut old_ksp, new_ksp)` that saves callee-saved registers and `ra`
  on the kernel stack and returns into a different function than it was called
  from (rejected for M2: it buys the ability to suspend a task *mid-kernel*,
  and no M2 syscall can block — `write` completes into DBCN, `gettime` is one
  `rdtime`, `sbrk` moves a pointer, `yield` and `exit` are scheduler
  operations. It costs a second context format to explain, a synthetic switch
  frame with a fake `ra` for new tasks, and the mind-bender that makes it a
  reading session rather than a paragraph). Storing the user context in the
  TCB and copying it in and out of the frame (rejected: the copy is pure
  overhead — the frame is already a complete context — and it creates two
  places where a register lives).
- **Rationale:** hardware clears `sstatus.SIE` on trap entry and we never set
  it (D-0020), so kernel code is never preempted; with no blocking syscall, a
  task's kernel work always runs to completion. Those two facts mean there is
  exactly one point in the system where a task can lose the CPU — the trap
  epilogue — which is precisely the point where its entire context already
  sits in one 272-byte structure at a known address (`kstack_top - 272`,
  D-0030). Returning a pointer keeps all scheduling policy in Rust and leaves
  the assembly one instruction longer. `gp` and `tp` are zeroed in the
  fabricated frame for the same reason: if user code ever does emit a
  `gp`- or `tp`-relative access, it faults near address 0 immediately instead
  of silently reading kernel data through a register the kernel owns.
- **Consequences:** the handler must have no live Rust state on the outgoing
  task's kernel stack when it returns — it does not: it returns normally, its
  epilogue pops its frames, and only then does block 4 move `sp`. Dead frames
  left below the outgoing task's trap frame are never touched again, because
  resuming that task sets `sp` to its frame at the top of its stack. **The
  known upgrade:** if M3 adds a blocking operation, this design has to become
  the separate save path, and that is a change rather than an addition. The
  mitigation is structural and free — keep "choose the next task" (policy)
  in a function separate from "resume this frame" (mechanism) so a second
  resume path can be added without touching the scheduler. M3 also has the
  option of polling virtio in the task's own context, which keeps this design.

## D-0033: SBI-shaped syscall ABI — number in `a7`, `a0` error and `a1` value
- Date: 2026-08-14 — Status: accepted
- **Decision:** the syscall number is in `a7`, arguments in `a0`–`a5`, and the
  return is a pair: `a0` = error (0 on success), `a1` = value — written into
  the trap frame, not into registers, because the epilogue restores from the
  frame. Numbering starts at 1: 1 `write`, 2 `exit`, 3 `sbrk`, 4 `gettime`,
  5 `yield`; **0 is reserved and invalid**. Signatures:
  `write(ptr, len) -> count` (console only, no `fd`), `exit(code)` (does not
  return), `sbrk(delta) -> old_break` (0 queries, negative shrinks, past the
  wall returns `NO_MEM` with the break unchanged), `gettime() -> raw time
  counter`, `yield()`. `sepc` advances by the constant 4, citing that RVC has
  `c.ebreak` but no `c.ecall`.
- **Alternatives considered:** a Linux-style single-register return with small
  negative errors in `a0` (rejected: it forces an argument that no legitimate
  return value can look like an error, which here is true only because of our
  memory map — a property of the platform, not of the ABI. The pair costs one
  register and removes the question). Reusing SBI's exact numeric error codes
  (rejected: SBI's list does not contain the errors we need, and a false
  identity is worse than an honest analogy — so the *shape* is SBI's, the
  numbering is ours). A reserved `fd`-like first argument to `write` so M3's
  socket multiplexing would be ABI-compatible (rejected: it is a hook for a
  future milestone, which the standing rules forbid, and the cost of changing
  the ABI later is one edit in the single in-tree caller). `gettime` returning
  nanoseconds (rejected: it hides the 10 MHz timebase that D-0012 makes an
  explicit platform constant, and the app can multiply; the raw counter is
  also exactly what the kernel arms the timer with, and 100 ns resolution is
  what M4's latency bracketing wants).
- **Rationale:** the kernel has spoken this convention since M0 — EID/FID in
  `a7`/`a6`, arguments in `a0`–`a5`, `(error, value)` back — so mirroring it
  means one calling convention in the whole system, which is both a legibility
  win and a good interview answer ("our syscall ABI is the ABI our kernel
  itself calls"). Starting the numbering at 1 is the fail-loudly choice: a
  wild jump with a zeroed `a7` lands on "invalid syscall 0" rather than
  silently being `write`.
- **Consequences:** `a1` is clobbered on every syscall return, which the user
  side must know. The five-call wall (D-0010) is unchanged: a need not
  expressible here becomes a decision entry, not a sixth number. M3 will
  likely revisit `write`'s single sink.

## D-0034: Validate user pointers against static intervals; `SUM` only around a validated copy; user faults never panic the kernel
- Date: 2026-08-14 — Status: accepted
- **Decision:** every user pointer crossing the syscall boundary is checked by
  `user_range_ok(task, ptr, len)`: reject on `checked_add` overflow, then
  require full containment in one of that task's static intervals — its user
  stack, the live part of its break window, `.udata`+`.ubss`, or `.urodata`
  for read-only sources. `sstatus.SUM` is raised **after** validation, only
  around a `memcpy` inside `copy_from_user` / `copy_to_user`, with a 4 KiB
  per-call cap, and dropped before any formatting or dispatch. A U-mode fault,
  an invalid pointer, or an unknown syscall number **kills the task** — print
  `task N killed: <cause> sepc=… stval=…`, mark it `Exited`, reschedule — and
  never panics the kernel.
- **Alternatives considered:** walking the page table per page and requiring
  `U=1` (rejected: that is what a kernel with a dynamic address space must do;
  here every user interval is fixed by the linker plus one TCB field, so the
  walk re-derives a known fact at O(len/4096) instead of O(1)). Trusting the
  hardware to catch bad pointers (rejected because it cannot: see the
  rationale). Panicking on a bad user pointer (rejected: if a U-mode task can
  take the machine down, the U/S boundary M2 exists to build is decorative).
- **Rationale:** the `U` bit protects user → kernel and nothing else. With
  `SUM=1` the kernel may read `U=1` pages, and it could always read `U=0`
  pages, so a copy loop will faithfully read kernel `.bss` if the task names
  that address — there is no hardware check to enable, and with paging on
  there is no physical back door either, because every S-mode load goes
  through the same translation and the same check. Validation is software or
  it does not exist. Bounding the `SUM` window matters for the same reason in
  reverse: while it is up, every kernel bug that dereferences a wild pointer
  into user memory succeeds instead of faulting, so the window contains a
  `memcpy` and no decisions. The fail-loudly rule still applies where it
  belongs — a violated *kernel* invariant panics; a misbehaving *task* is a
  contained, reported condition, which is the whole point of the privilege
  boundary.
- **Consequences:** the last line of defence is unchanged — if validation is
  correct the kernel never faults on a user pointer, and if it is wrong the
  result is a kernel page fault, which panics loudly with `scause`/`stval`.
  `just test-user-fault` asserts the containment: the faulting task dies, the
  other one finishes, and the run exits 0. Every new interval a task can own
  (M3's app sections) must be added to the validator, or legitimate pointers
  start failing.

  **"User faults never panic the kernel" holds only for the delegated
  subset.** OpenSBI sets `MEDELEG = 0xf0b509`, so codes 1, 2, 4, 5, 6, 7, 9
  never reach S-mode (PLAN M1 concept 9). Cause 2 — illegal instruction —
  is the one a task can actually raise: `unimp`, or an FP op with `FS=Off`
  (fabricated `sstatus` leaves FS Off, D-0032). That trap goes to M-mode.
  OpenSBI dumps `mcause`/`mepc`/`mtval` and parks the hart. Our handler
  never runs, so there is no `task N killed` line and no reschedule; the
  machine looks dead. That is outside our containment **by platform design,
  not by choice** — we do not control `medeleg`. Symptom and first-response
  are in DEBUGGING.md §4 (M2). The user-fault selftest therefore loads from
  VA 0 (cause 13, delegated) rather than executing `unimp`.

## D-0035: One tick per slice; no idle loop in M2
- Date: 2026-08-14 — Status: accepted
- **Decision:** the timeslice is exactly one timer interrupt at the existing
  10 ms period (D-0018) — the handler switches on every tick, so no per-task
  tick counter exists. There is no idle loop and no `Blocked` task state.
- **Alternatives considered:** a multi-tick slice with a counter in the TCB
  (rejected for now: it is a policy knob with no M2 requirement behind it;
  "the slice is the tick" is one sentence and one fewer field). Shortening
  the period to 1 ms for finer preemption (rejected: unnecessary for a demo
  that must show interleaving inside a 3 s hang-guard — and `just test-stress`
  already proved the allocators survive 1 ms ticks, so this stays available at
  no risk). A `wfi` idle task (rejected: unreachable — see rationale).
- **Rationale:** with no blocking syscall there is no state in which a task is
  unrunnable but not finished, so the ready set is empty only when every task
  has exited, and that path shuts the machine down from the last `exit`. An
  idle loop would be code no test could reach, which the standing rule against
  implementing beyond the milestone forbids. A task that yields while it is
  the only runnable one simply gets the CPU back from round-robin.
- **Consequences:** `kmain` never returns once the first task starts, so the
  boot stack is dead from that point and `park()` after it is unreachable.
  The moment M3 introduces a blocking operation, both halves of this entry
  reopen together: a `Blocked` state and an idle loop arrive with it, and
  D-0032's resume path is the third piece.

  **Known fairness property, not a bug.** A syscall-heavy task gets more CPU
  than a compute-heavy one under "slice = one tick". Kernel code runs with
  `SIE = 0` (D-0020), so a pending `STIP` cannot preempt until `sret`. The
  tick is not lost — it fires immediately after `sret` (PLAN M1 concept 3) —
  but the slice has already been stretched by however long the syscall ran.
  Two standard fixes we are **deliberately not doing:**
  1. Charge elapsed time (`rdtime` delta) rather than ticks, so a syscall
     that ate part of the 10 ms slice leaves the remainder.
  2. Make the kernel preemptible (`SIE = 1` in S), so a tick can switch
     mid-syscall.
  Either one reopens D-0020 / D-0028 / D-0036. M2 keeps tick accounting.

## D-0036: Resolve D-0028 by preallocation, enforced with `frame::freeze()`
- Date: 2026-08-14 — Status: accepted (resolves D-0028 for M2)
- **Decision:** M2 takes D-0028's third option — preallocate everything the
  trap path could need, so the "trap handlers must not allocate" invariant
  holds by construction — and enforces it with `frame::freeze()`, called
  immediately before the first `sret` into U-mode. After the freeze,
  `alloc_frame` and `free_frame` panic printing the request. We do **not**
  mask `sstatus.SIE` around frame-list mutation, and we do not add a
  frame-side re-entry detector. Allocator logic is unchanged; the heap keeps
  its existing `IN_ALLOC` detector.
- **Two independent reasons the hazard disappears in M2. They fail
  independently, which is why both are recorded:**
  1. **Nothing in the trap path allocates.** Kernel stacks and user sections
     are static and linker-placed (D-0030, D-0031); the map is complete before
     activation and never edited (D-0031); `sbrk` moves a pointer inside a
     preallocated 64 KiB window. The frame allocator is used only by
     `page::build` at boot. **Broken by:** M3 unfreezing to allocate virtio
     buffers, or any new syscall that backs memory on demand.
  2. **After the first `sret`, no kernel code runs with `SIE = 1` at all.**
     `kmain` never returns (D-0035), so from that point kernel code executes
     only inside trap handlers, where hardware cleared `SIE` and we never set
     it (D-0020). There is no interruptible kernel-side allocator caller left
     to be interrupted. The storm's 20 timer interrupts landed inside
     `alloc_frame` only because `kmain` ran with interrupts enabled.
     **Broken by:** any future kernel-side loop that runs with `SIE = 1` — an
     idle loop that polls, a boot-like phase re-entered later, or M3 polling
     virtio in kernel context with interrupts on.
- **Alternatives considered:** masking `SIE` around the frame-list mutation
  (rejected as M2's answer, and the cost was not the reason: the critical
  section is three instructions in `alloc_frame` — the 4 KiB zeroing stays
  outside it — and two stores in `free_frame`, so the tick jitter is
  nanoseconds. It was rejected because it is the only option that *authorizes*
  allocation from a handler while protecting just one of the structures such a
  handler would touch: a handler that pops a frame while the interrupted code
  was midway through `page::map` corrupts the page table, which no lock on the
  free list protects, and a handler that hits exhaustion panics in interrupt
  context, which is a dead machine rather than a diagnostic. It buys real
  atomicity for one structure and false confidence about the operation).
  A frame-side re-entry detector mirroring `IN_ALLOC` (rejected as
  unnecessary while frozen: the freeze asserts the invariant globally instead
  of catching one failure shape, and it is the stronger check — but the
  detector is the natural fallback the moment M3 unfreezes). Freezing the
  kernel heap too (rejected: `IN_ALLOC` already panics loudly on the shape
  that matters, and freezing would be a claim to walk back the first time a
  diagnostic wants a `String`).
- **Rationale:** the storm found no allocator bug, so the evidence did not ask
  for a lock — it asked why the invariant was unenforced. Preallocation
  removes the hazard's precondition rather than guarding it, which is also
  what M2's design wanted for independent reasons (static stacks come from the
  allocator's inability to promise contiguity, D-0030). `freeze()` converts
  "unenforced invariant held by the handler's current contents" into a loud
  runtime assertion.
- **Consequences:** `just test-stress` still passes because the storm runs
  before the freeze (`stress` never calls `enter`, so it never freezes). Any
  M3 requirement to allocate after boot must reopen this entry explicitly
  rather than quietly calling `unfreeze()`; the likely answer there is `SIE`
  masking plus preallocated pools, and it would amend D-0028 rather than
  replace it. Boot prints `frames frozen: free=N` so the transition is
  visible in every serial log.

  T2.9's scheduler does not break reason 2. After `kmain` the only kernel
  code that runs is `trap_handler` and what it calls (`preempt`, `yield_cpu`,
  `after_exit`, syscall bodies). Hardware clears `SIE` on trap; those
  functions return a frame pointer and never `sstatus::set(SIE)`. `sret`
  restores `SIE` from `SPIE` in U only. There is still no interruptible
  kernel-side allocator caller.

  **69 frames consumed before freeze (was 67 before T3.1).** `frames frozen:
  free=N` compared with the `FRAME OK` total is a gap of 69 on the default
  image: 67 page tables plus 2. The 67 are `page::tables_used()` — one Sv39
  root, one L1 (VPN[2]=2 covers `0x8000_0000..0xC000_0000`; we do not
  allocate an L0 for the unmapped OpenSBI 2 MiB at `0x8000_0000`), 63 L0
  tables for `0x8020_0000..0x8800_0000`, plus D-0039's extra L1 (VPN[2]=0)
  and L0 (VPN[1]=0x80) for the virtio-mmio window. Those two table frames
  come from the RAM pool; the eight MMIO pages themselves do not, so
  `FRAME OK`'s `frames N` (`total_frames()`) does not move. The other two
  held frames are the `FRAME OK` self-test's leftover pair: it allocates
  `a` and `b`, frees `a`, reallocates `c==a`, and never frees `b` or `c`.
  That is a deliberate LIFO check, not a leak to plug; freeze then pins
  them. Feature images can print a different `FRAME OK` total (`__heap_end`
  moves with code size) — `just test-stress` compares exhaust's panic
  against **that boot's** `FRAME OK` line, not against `just run`.

## D-0037: Hand-rolled network stack; the TCP scope tripwire
- Date: 2026-08-15 — Status: accepted
- **Decision:** M3's network path is written from scratch: Ethernet framing,
  ARP, IPv4, ICMP echo, UDP echo, and a minimal TCP that serves one HTTP
  response to a real client. No smoltcp, no third-party stack, no TLS.
  **Tripwire:** any TCP work beyond "serves one GET to curl, verified in a
  capture" requires M4 to already have first numbers. No retransmission
  tuning, no multiple connections, no feature past the demo until
  measurement exists.
- **Alternatives considered:** `smoltcp` (rejected: it is the sane
  production choice and precisely thereby the wrong one here — the project's
  value is being able to defend every byte of the path, and M4's phase
  decomposition wants a stack whose cost structure we authored). UDP-only
  demo (rejected: the headline metric is boot-to-first-**HTTP**-byte, and
  HTTP-over-TCP against a real client is the credibility line). TLS
  (rejected: an order of magnitude more surface with zero measurement
  value at this layer).
- **Rationale:** the stack is the instrument for M4's measurement, not a
  product. A hand-rolled stack lets every microsecond on the response path
  be attributed to a line we wrote, which is what "find the floor" needs.
  The tripwire exists because TCP is the recognized schedule risk: the
  demo defines done, and measurement — the project's actual deliverable —
  outranks protocol completeness.
- **Consequences:** our TCP is honest about what it omits (D-0041) and the
  report may not make throughput or robustness claims beyond the demo. The
  tripwire is enforced structurally: M3's task list ends at T3.12 and
  contains no TCP-polish task.

## D-0038: Modern virtio-mmio, split virtqueue, static DMA pool; the freeze stands
- Date: 2026-08-15 — Status: accepted
- **Decision:** the driver speaks modern virtio-mmio (version 2, forced with
  `-global virtio-mmio.force-legacy=false`) with split virtqueues, 16
  descriptors per queue, negotiating `VIRTIO_F_VERSION_1` and
  `VIRTIO_NET_F_MAC` only — no `MRG_RXBUF`, so RX buffers are 2048 bytes,
  whole-frame, single-descriptor chains. Rings and buffers are page-aligned
  statics in kernel `.bss` (RX 16×2048 + TX 8×2048 + rings ≈ 64 KiB): the
  frame allocator is never touched, `frame::freeze()` stands verbatim, and
  D-0036's reason 1 survives unamended. RX buffers are re-posted after
  consumption, never freed — the NIC owns a fixed set of 24 buffers from
  boot to shutdown; there is no buffer allocation path.
  `virtq::verify()` runs before `DRIVER_OK` (the T1.6 move): alignment
  16/2/4, every descriptor address inside the pool and identity-mapped,
  indices zero, and the six queue-address registers read back and compared.
  Memory barriers from day one: `fence w,w` before the avail-idx store,
  `fence w,o` before the notify store, `fence r,r` after reading used-idx.
- **Alternatives considered:** legacy virtio-mmio (rejected: page-shifted
  `QueuePFN` forces the three structures into one contiguous layout and the
  10/12-byte header ambiguity into the fast path; "we speak the current
  spec" is also the defensible interview position). `MRG_RXBUF` (rejected:
  buys nothing at one connection and adds descriptor-chain walking).
  Preallocating the pool from the frame allocator before freeze (rejected:
  buffer addresses would depend on allocation order and the pool would need
  its own bookkeeping; statics are placed by the linker and verifiable by
  name). Unfreezing plus a frame-side detector (rejected: reopens the
  exact hazard D-0036 closed, and D-0036 predicted this moment).
- **Rationale:** D-0036 expected M3 to need the allocator; it does not,
  because everything the NIC touches is fixed-size and known at link time —
  the same static-preallocation logic that produced D-0030's task slots.
  The barriers are written although QEMU's device model will likely hide
  their absence: a bug that cannot be provoked on the only test platform
  must be prevented by review, not testing.
- **Consequences:** 24 buffers cap in-flight traffic — irrelevant at one
  connection, recorded for M4's threats-to-validity. The pool is dead
  weight in netless images (~64 KiB of `.bss`; nothing, against 128 MiB).
  `virtq::verify()` joins the boot cost that `fast-boot` may eventually
  strip, with the same price-of-paranoia accounting as the map verify.

  **T3.2: the six queue-address registers are write-only.** Virtio 1.2
  §4.2.2 marks QueueDesc/QueueDriver/QueueDevice Low/High as write-only.
  QEMU 8.2 `virtio_mmio_read` returns 0 and logs `LOG_GUEST_ERROR`, so a
  FEATURES_OK-style readback cannot catch a wrong offset or a swapped
  high/low word on this transport — a missed write and a successful write
  both read as 0. The load-bearing guards are: a single `write_addr`
  helper that always stores `gpa as u32` at `off` and `gpa >> 32` at
  `off+4` (swapped halves are unrepresentable at the call site); named
  offsets citing the spec; and `verify()` still reading both halves, so a
  device that implements the registers as RW panics on mismatch
  (`wrote=… read=…; wrong offset or swapped high/low`). A zero read of a
  nonzero write is printed as write-only MMIO, not treated as a match.
  QueueReady is readable and must stay 0 across the write+verify window
  — that one readback *does* work, and it is what keeps the device from
  owning the ring while we check it.

## D-0039: Map the virtio-mmio window at build; map-then-probe
- Date: 2026-08-15 — Status: accepted (amends D-0025; D-0031 intact)
- **Decision:** `page::build` maps the 8-page virtio-mmio window
  `0x1000_1000..0x1000_9000` (8 transports, 0x1000 stride, QEMU
  `hw/riscv/virt.c`) as R+W, never X, U=0, at boot, before `activate`.
  Discovery probes all 8 slots after paging is on. No PTE is edited after
  activation — D-0031's ban stands. Every QEMU invocation in the harness
  gains the NIC flags so feature images do not diverge from the default
  boot.
- **Alternatives considered:** probe-then-map (impossible under D-0031:
  probing reads the magic register, which requires a mapped page — the
  no-edit rule forces map-then-probe). Mapping only the discovered slot
  (same impossibility). Relaxing D-0031 with a one-shot post-activation
  map call (rejected: the one hard-won M1 property is that the address
  space is validated before anything runs on it and never changes; eight
  pages of window is a small price for keeping it).
- **Rationale:** D-0025 already contained its own amendment clause — "M3
  adds the virtio pages in the same task that adds the driver" — and this
  entry is that clause exercised. The permissions are justified against
  the code that uses them: the driver reads/writes device registers (R+W)
  and nothing fetches from device memory (never X).
- **Consequences:** eight pages of device memory are now reachable by a
  stray kernel pointer that previously would have faulted — the cost
  D-0025's rationale conceded the moment a driver exists. The T2.2 verify
  walk asserts the window's mapping and permissions. sifive_test remains
  unmapped (D-0017's escape hatch stays an `ecall`). Mapping a new VPN[2]
  costs two page-table frames (L1 + L0); `tables_used` is 67 and freeze
  holds 69. The MMIO pages are not RAM, so the frame allocator's
  `total_frames()` / `FRAME OK` count is untouched.

## D-0040: Driver and stack in the kernel; `recv`/`send`; polling, no PLIC
- Date: 2026-08-15 — Status: accepted (amends D-0010 and D-0033)
- **Decision:** the virtio driver and the entire network stack live in the
  kernel. The app talks payloads over two new syscalls — `recv` (6):
  `recv(buf, len) → (err, n)`, returning request payload or an
  `EAGAIN`-style error, **each call polling the NIC and advancing the
  stack**; `send` (7): `send(buf, len, flags)`, a FIN flag bit closing the
  connection. One listener, one connection at a time, no accept, no fds.
  Polling only: the PLIC stays unmapped and uninitialized in M3. Two
  invariants: **the NIC is touched only from syscall context** (never from
  the trap path — networking adds zero code to the path D-0028 constrains;
  TCP's timer is driven from `recv` polling via `rdtime`, not the tick
  handler), and **remote bytes are user input** (a malformed packet
  increments a counter and is dropped; it never panics the kernel —
  D-0034's spirit extended to the wire).
- **Alternatives considered:** raw-frame syscalls with the stack in the app
  (rejected: every packet crosses U/S twice through the SUM window instead
  of twice per connection; TCP timers would depend on a task that might be
  spinning elsewhere; and the whole stack becomes compiled-Rust-in-`.utext`,
  multiplying T3.9's checker risk by the stack's size). Everything in the
  task with MMIO and rings mapped U=1 (rejected outright: a U-writable
  descriptor table lets user code point device DMA at kernel memory — the
  U/S boundary the project exists to measure becomes decorative exactly
  where it matters). PLIC interrupts (rejected for M3: init cost lands in
  the boot path the headline metric measures, and per-packet trap entry
  plus claim/complete MMIO round-trips land in the response path; polling
  costs host CPU, which the metric does not price — a single-purpose VM
  serving one request has nothing better to do with the core). Multiplexing
  over `write` (rejected: overloading the console syscall with a channel
  argument saves one number at the cost of the ABI's legibility).
- **Rationale:** the response path is RX-used-ring → TCP → TX-avail-ring
  entirely in S-mode, with exactly two U/S crossings for the whole
  connection. The layering story is defensible: the kernel is the
  transport, the app is the HTTP server — mirroring real OS structure and
  what Unikraft's lib-stack does inside its single domain. D-0035 survives
  untouched: no Blocked state, no idle loop — a task waiting for a packet
  is running, spinning on `recv`, and that spin *is* the poll loop.
- **Consequences:** the five-syscall wall becomes seven, by decision entry
  as D-0010 prescribed. M2's polling-first recommendation is upgraded to
  no-PLIC-in-M3; if M4's data ever wants interrupt-driven numbers for the
  comparison, that is a new entry. The app cannot be given a second
  connection without reopening this entry.

## D-0041: Minimal TCP: passive open, stop-and-wait, one fixed RTO
- Date: 2026-08-15 — Status: accepted
- **Decision:** passive open only. States: LISTEN → SYN_RCVD → ESTABLISHED
  → FIN_WAIT_1 → FIN_WAIT_2 → truncated TIME_WAIT (log and drop to CLOSED
  immediately), plus CLOSE_WAIT → LAST_ACK for the peer-closes-first race.
  Duplicate SYN in SYN_RCVD re-sends the SYN/ACK. ISN from `rdtime` low
  bits. MSS parsed from the SYN; all other options skipped via the data
  offset field, which is honored on every segment. Fixed 8 KiB advertised
  window. Stop-and-wait transmission: at most one unacked data segment in
  flight, retransmitted on a fixed 200 ms `rdtime` deadline checked from
  the polling loop, 8 attempts then RST. Anything unexpected gets RST plus
  a counter — never silence, never a panic. Checksums with pseudo-header
  in both directions. SYN and FIN each consume a sequence number.
- **Alternatives considered:** no retransmission at all (defensible on a
  near-lossless slirp leg and rejected anyway: the failure symptom is curl
  hanging forever with nothing on serial — the single worst debugging
  experience available in this project — bought back for ~30 lines; a
  stack without retransmission is also not honestly called TCP).
  Congestion control, window scaling, SACK, timestamps (all rejected: the
  response is one segment; there is no window to grow and no loss pattern
  to recover; each is listed so the report can say what was omitted and
  why the omission is invisible at this workload). Full TIME_WAIT
  (rejected: a one-shot server holding 2MSL state serves nothing; the
  consequence — a retransmitted peer FIN meets RST — is visible in the
  capture and harmless).
- **Rationale:** the peer is libslirp (D-0042), which negotiates MSS and
  little else, so the design rules that matter are the ones that keep any
  naive stack alive against any peer: honor data offset (the number-one
  naive-stack killer is assuming 20-byte headers), get the SYN/FIN
  sequence-number consumption right (the off-by-one produces
  hangs-at-close that masquerade as retransmit bugs), ACK unconditionally
  on in-order receipt, and say RST when confused so the capture shows it.
- **Consequences:** sequenced to land with demonstrable checkpoints —
  handshake in pcap (T3.10), data + close + provoked retransmit + curl
  end-to-end (T3.11). The tripwire (D-0037) applies beyond that line. A
  browser (multiple parallel connections) is out of scope by construction;
  curl is the demo client.

## D-0042: Static network configuration; no DHCP; slirp is the peer
- Date: 2026-08-15 — Status: accepted
- **Decision:** the guest uses QEMU user-net's contractual constants
  statically: 10.0.2.15/24, gateway 10.0.2.2. No DHCP client. The TCP/UDP
  demo ports arrive via `hostfwd`. It is recorded plainly that under
  user-net the guest's TCP peer is **libslirp's internal stack** — a
  `hostfwd` connection is terminated on the host side and re-originated
  from 10.0.2.2 — and that inbound ICMP echo is unroutable, so ICMP is
  exercised guest→out (ping 10.0.2.2, slirp answers).
- **Alternatives considered:** DHCP (rejected: burns boot milliseconds to
  discover constants — the wrong direction for a boot-to-first-byte
  metric — and adds a UDP client state machine with no measurement value;
  the Linux baseline gets static config too, preserving comparability).
  Tap networking for a real host-kernel peer (rejected for M3: needs
  root/setup and breaks the "runs anywhere per SETUP.md" property;
  recorded as the M4 threat-to-validity escape hatch if a hostile TCP
  peer is ever needed).
- **Rationale:** slirp's addressing is documented, stable API surface, not
  guesswork. The de-risking is honest: curl's kernel-grade TCP options
  never reach us, which makes the demo achievable, and the pcap — not the
  claim "survives a real client" — is the arbiter of protocol correctness.
- **Consequences:** the M4 report's threats-to-validity section inherits
  the slirp-termination caveat verbatim. If the demo ever moves to tap,
  ARP stops being a one-gateway affair and this entry reopens.

## D-0043: Measurement edges, `fast-boot`, capture in the harness
- Date: 2026-08-15 — Status: accepted
- **Decision:** the boot-to-first-HTTP-byte edges are named: E0 = host
  clock at QEMU exec; E1 = machine start (`mtime` ≈ 0); E2 = kernel entry
  (`rdtime` at `_start`); E3g = `rdtime` at response-TX publish;
  E3w = pcap timestamp of that frame; E4 = first byte at the client.
  E0→E4 is both the honest and the comparable number (identical harness,
  no guest cooperation required); E2→E3g decomposed by phase at 100 ns
  `rdtime` resolution is the floor number, available for Whimbrel only
  (stated, not hidden). Divergences are reported where they occur:
  E3w−E3g prices virtio+slirp transit, E4−E3w the host loopback, E0→E1
  QEMU init shared by all systems. The client runs a tight (~1 ms)
  connect-retry loop started before E0; boot-to-ready (E0 → first
  successful connect) is reported alongside. **The E2 assumption is
  validated, not assumed:** T3.12(a) freezes the machine at reset and
  reads `time` via GDB before the first guest instruction; the observed
  offset is recorded in this entry when measured, and the firmware row of
  the M4 table cites the measurement. Phase timestamps live in a static
  array and are printed only after the response is sent (DBCN is one
  `ecall` per byte). A `fast-boot` cargo feature (same codebase, sibling
  shape) removes the boot tick wait, self-tests, and non-essential prints;
  it **keeps the map verify initially**, and the safe/fast delta is
  reported as the price-of-paranoia finding. The M1 timer acceptance moves,
  not vanishes: the default profile's 30-tick wait shrinks to 3 ticks with
  `tick 3` on serial, and timer coverage also holds structurally (the
  T2.9 preemption counters cannot advance without live ticks). Packet
  capture (`-object filter-dump`) is standing harness infrastructure from
  the first TX packet onward; `tshark` joins SETUP.md and
  `scripts/install.sh` as a dependency so assertions run everywhere the
  harness does. Full determinism is not promised — slirp timing rides
  host scheduling; the harness promises reproducible statistics (N
  trials, median/IQR, pinned QEMU version) plus a pcap per run.
  **Unikraft baseline:** the riscv64 port is an open PR
  (unikraft/unikraft#1698, rebased June 2026; kraftkit riscv64 merged), so
  the M4 comparison rests on a timeboxed feasibility spike at the M3/M4
  boundary with a recorded fallback ladder: (1) it works — full three-way
  under identical conditions; (2) runs only on arm64/x86_64 — two-way
  riscv64 head-to-head as primary, Unikraft as a labeled different-ISA
  reference; (3) does not run — two-way plus qualitative boot-path
  analysis from source.
- **Alternatives considered:** first-serial-byte as the readiness edge
  (rejected as primary: serial cost differs across guests and is not the
  service the user waits for; kept as a secondary marker). `-icount` for
  determinism (rejected: does not tame the slirp/host boundary and
  distorts the host-clock edges the comparison needs). Stripping the map
  verify in `fast-boot` from the start (rejected: it is the project's
  signature safety net, and "we measured the cost of our own verification"
  is itself a result — it gets stripped only if the phase data shows it
  matters, as a recorded amendment).
- **Rationale:** a benchmark whose edges are not pinned before measurement
  drifts toward the number its author wanted. Naming E0–E4 now, validating
  E2, and building the instrumentation into T3.12 makes M4 a measurement
  exercise instead of a definition argument.
- **Consequences:** boot prints nothing new on the measured path; the
  phase block appears after first-byte. The `fast-boot` profile is a
  feature flag, not a fork — the sibling-selftest pattern already proves
  the shape. **E2 offset, measured T3.12(a):** QEMU `-S` at reset,
  `pc=0x1000` (OpenSBI), GDB `$time` = **0**. `rdtime` at `_start` is
  therefore the OpenSBI phase with nothing to subtract. Re-measure with
  `just measure-e2` if the firmware or QEMU version changes.
  **T3.12 wrap amendment (2026-08-16):** Headline boot-to-first-byte uses a
  client retry loop started **before E0** (`CLIENT_EARLY=1`,
  `just test-fast-release`). `sret→E3g` on `just test` / `just test-fast`
  is waiting for `HTTP READY` then spawning curl — that is harness time,
  not kernel work, and is not the M4 number. Kernel boot-to-ready is
  E2→sret (stack-ready is E2→listen). Debug `opt-level=0` paging (~150 ms
  walking ~32k pages twice) is not the cost of paging; M4 cites
  `cargo build --release --features fast-boot`. The default `just test`
  curl-after-`HTTP READY` path stays a correctness gate. Release LTO
  constant-folds `==` of distinct linker symbols (DEBUGGING.md §4.14);
  `task::pa` / page / virtq address helpers `black_box` those loads.

## D-0044: App crate in the user sections; check-utext bans FP, including compressed
- Date: 2026-08-15 — Status: accepted
- **Decision:** the M3 app is a real crate linked into the existing user
  sections — the linker script matches the app archive's
  `.text/.rodata/.data/.bss` into `.utext/.urodata/.udata/.ubss` — with a
  `usys` wrapper crate for the syscalls. `check-utext` grows to handle
  compiled-Rust output and **rejects every floating-point instruction in
  `.utext`: the F/D mnemonics and the compressed forms** — `c.fld`,
  `c.fsd`, `c.fldsp`, `c.fsdsp` — the encodings a compiler emits silently
  and a naive mnemonic list misses (llvm-objdump may print either
  spelling; both are banned). `sstatus.FS` stays Off.
- **Alternatives considered:** enabling FS so FP just works (rejected: the
  TrapFrame saves no FP state, so it is only safe while exactly one task
  uses FP — an invariant nobody is checking two milestones from now; and
  the demo needs no floats). Building the app for a soft-float target
  (rejected: `riscv64gc`'s hard-float ABI and a soft-float ABI cannot link
  into one image). Trusting that integer HTTP code emits no FP (rejected:
  hope is not a gate; the checker already fails closed on unknown forms,
  so the ban is a natural extension).
- **Rationale:** an FP instruction from U-mode with FS=Off is an
  **undelegated illegal instruction** — OpenSBI dump, hart parked, no
  `task N killed` line, the M2 known limit and the worst failure mode in
  the project. The choice is to handle it at runtime (enable FS, save FP
  state) or make it unrepresentable at build time; the checker already
  exists and the demo has no floats, so unrepresentable wins.
- **Consequences:** the app crate must avoid `f32`/`f64` end to end
  (formatting included); a violation fails `just check-utext` by name
  rather than parking the hart silently. If a future milestone needs FP in
  U-mode, this entry reopens together with a TrapFrame FP-state design.
  T3.9 carries the checker work and its acceptance includes a planted
  `c.fld` being caught.

## D-0045: ARP cache wraparound is exercised at init, then cleared
- Date: 2026-08-15 — Status: accepted
- **Decision:** the 4-entry ARP cache's wraparound eviction runs a
  five-insert self-test at driver init (distinct synthetic IPs), asserts
  the oldest is gone and the newest four remain, prints `ARP CACHE WRAP
  OK`, then **clears** the table so dummy entries do not shadow the
  gateway. slirp only ever offers one peer (10.0.2.2); a wraparound path
  that ships unrun is an untested path.
- **Alternatives considered:** leave wraparound untested and document it
  (rejected: the user-facing rule is not to ship an unrun wrap; a comment
  is not a test). Wait for five real peers (rejected: they will not
  arrive). Keep the dummy entries after the self-test (rejected: they
  occupy the only slots the gateway then has to evict into — a live
  table polluted by a test).
- **Rationale:** a 4-slot ring whose occupancy never exceeds 1 is a lie
  about being a cache. The self-test is the same shape as `heap::self_test`
  / `frame::self_test`: prove the invariant on every boot, then leave
  production state clean.
- **Consequences:** every image that reaches `net::init` prints
  `ARP CACHE WRAP OK`. `just test` greps it. If eviction breaks, boot
  panics before any packet.

## D-0046: T3.6 ARP test does not depend on GARP teaching slirp
- Date: 2026-08-15 — Status: accepted
- **Decision:** the first hostfwd connect fires on `DRIVER_OK`, which is
  printed **before** the GARP (T3.5 item 10: slirp caches a GARP and
  then never ARPs). That connect is the ARP trigger. After the guest
  prints `TX ARP reply`, the harness connects a **second** time. Pcap
  asserts slirp's request, then our unicast reply, then IPv4
  10.0.2.2→10.0.2.15 with a later frame number. The GARP still goes out
  after the reply so T3.4's greps hold; it is not how this test teaches
  slirp our MAC.
- **Alternatives considered:** skip the GARP for this task (rejected:
  T3.4 acceptance still runs on the same boot). Fire the first connect
  after the GARP (rejected: that is the T3.5 failure — no request).
  Treat the first connect's SYN as the "proceeds past ARP" proof
  (rejected: the stated acceptance is a *subsequent* connect; relying
  on slirp sending SYN on the same attempt that elicited ARP couples
  the proof to one slirp timing). A second QEMU `-netdev` trick or a
  static ARP on the host (rejected: extra moving parts; two connects
  on the existing hostfwd is the same trigger T3.5 already uses).
- **Rationale:** GARP-caching makes "one connect after DRIVER_OK"
  order-dependent. Splitting provoke (connect #1, before GARP, must
  ARP) from prove (connect #2, after our reply, must be IPv4) keeps
  both halves deterministic.
- **Consequences:** `scripts/boot-test.sh` waits for `TX ARP reply`
  before the second `provoke-hostfwd` **on `net-init-selftest` only**
  (D-0054). `assert-pcap-arp-reply.sh` fail-closes on request-only,
  reply-before-request, and reply-without a later IPv4 frame.
  Panic/hang images never print `DRIVER_OK` and are not provoked.
  **T3.12 wrap amendment:** After D-0054 the guest ARPs `10.0.2.2`
  itself; slirp often never ARPs us, so `TX ARP reply` is not a boot
  event. The watcher fires **one** `provoke-hostfwd` after
  `gateway 10.0.2.2 MAC learned` (cache full, SYN not `noarp`). The
  slirp-asked-first pcap chain (`assert-pcap-slirp-arp.sh`,
  `assert-pcap-arp-reply.sh`) is no longer a live gate; the scripts
  remain for fail-closed coverage of the scripts themselves.
  `just test-net-init` / `just test-net-tcp` keep the handshake asserts;
  `net-init-selftest` stays in `poll_rx` until ESTABLISHED and LISTEN
  restore so the feature image does not exit during ping.

## D-0047: TX uses the ARP cache; empty gateway is a panic, not a queue
- Date: 2026-08-16 — Status: accepted
- **Decision:** every IPv4 TX is Ethernet-unicast to the **gateway**
  MAC (`10.0.2.2`) looked up in the ARP cache. There is no routing
  table. If that lookup misses, the driver **panics** by name. It does
  not emit an ARP request, and it does not queue the datagram. T3.7's
  ping runs after `wait_gateway_arp` (D-0054), which learns 10.0.2.2 from
  slirp's reply to our request; an empty cache at that point is a real
  resolution failure, not a missed hostfwd window.
- **Alternatives considered:** ARP-then-queue (rejected: there is no TX
  queue, and a pending ICMP datagram plus a wait-for-ARP loop is a
  second protocol on the same descriptor we reuse after `wait_tx`).
  Broadcast the IP datagram (rejected: slirp would still ARP, and we
  would be sending IPv4 to ff:ff:ff:ff:ff:ff). Hard-code QEMU's slirp
  MAC (rejected: the cache would be ornamental; T3.6 already proved
  learn-from-request).
- **Rationale:** fail loudly. A silent drop looks like slirp never
  answered the ping; a panic saying `no MAC for gateway 10.0.2.2`
  names the missing precondition. The guest-initiated ping is sequenced
  after the cache is populated, so the panic is a regression alarm, not
  a startup race.
- **Consequences:** `arp::lookup([10,0,2,2])` is the only L2 resolution
  IPv4 has. The echo-reply path uses the same lookup even though the
  IPv4 destination is the requester — Ethernet dest is still the
  gateway (no routing). T3.12 (D-0054) fills the cache with an ARP
  request at init; an empty cache after that wait is a real miss.

## D-0048: ICMP echo server exists; slirp only lets us test the client
- Date: 2026-08-16 — Status: accepted
- **Decision:** type 8 → type 0 (echo reply) is implemented: copy
  identifier/sequence/data, recompute the ICMP checksum, swap IPv4
  addresses, TX to the gateway MAC. **The harness cannot exercise that
  half.** Under `-netdev user`, inbound ICMP echo is unroutable
  (PLAN concept 4, D-0042); a host `ping 10.0.2.15` never arrives as
  an RX used-ring entry. The tested direction is guest→out: we ping
  10.0.2.2, slirp answers. A build self-test (`ICMP REPLY BUILD OK`)
  proves the reply writer produces a checksum-valid type-0 copy; it is
  not a wire test. RTT is `rdtime` at the 10 MHz virt timebase
  (`timer::TICK_NS` = 100): one line
  `PING RTT dst=10.0.2.2 id= seq= tx= rx= ticks= ns=` so M4 can parse
  the same keys rather than a one-off debug print. `tx` is `rdtime` at
  QueueNotify of our echo request; `rx` is `rdtime` when the matching
  echo reply is classified. `ns = ticks * 100`.
- **Alternatives considered:** skip the server path because slirp cannot
  deliver inbound echo (rejected: "untested" and "unimplemented" must
  stay distinct; the limitation is recorded, the code is not omitted).
  Test inbound via tap (rejected for M3: D-0042; tap is the M4
  escape hatch). Print only microseconds (rejected: the native unit is
  `rdtime` ticks; ns is a scaled copy of the same integer, not a
  different clock).
- **Rationale:** an echo server that exists only in a comment is how
  the dishonest skip happens. Shipping the type-8 path and saying
  plainly that user-net cannot feed it keeps the interview answer
  honest: the client RTT is the acceptance; the server is correct by
  construction and untested on the wire.
- **Consequences:** `just test` greps `PING RTT` and asserts pcap
  echo-request then echo-reply. It does not send a host ping. Malformed
  IPv4/ICMP counters (`csum`, `frag`, `ihl`, …) must read 0 on that
  path; `ipv4 drop_proto` may be non-zero because hostfwd SYNs are
  IPv4/TCP, not malformed. **That exception expired at T3.10 (D-0049);
  `just test` now requires `proto=0`.**

## D-0049: `drop_proto` hostfwd-SYN exception expires at T3.10
- Date: 2026-08-16 — Status: accepted
- **Decision:** through T3.9, `ipv4 drop_proto` is allowed to be
  non-zero on the happy path. The hostfwd TCP connects that provoke
  slirp ARP (T3.5/T3.6) deliver protocol 6 to a stack that only handles
  ICMP (1) and UDP (17). That is expected noise, **named as temporary**.
  T3.10 (TCP passive open) **removes the exception**: once we parse
  SYNs, a non-zero `drop_proto` means an unknown protocol, not a
  hostfwd SYN, and it must not sit in the "expected noise" category.
  `just test` requires `proto=0`. The `ipv4: drop proto=6 (TCP; expected
  until T3.10)` print is gone: protocol 6 is delivered to `tcp`.
- **Alternatives considered:** stop sending hostfwd connects after ARP
  is cached (rejected: T3.6's second connect is the "past ARP" proof).
  Classify TCP as a separate `drop_tcp` that stays non-zero forever
  (rejected: that launders the exception past T3.10). Ignore proto=6
  without a counter (rejected: silent).
- **Rationale:** a counter that is "allowed to be whatever" is how
  real drops hide. Dating the exception to the task that makes it
  false keeps T3.10 from inheriting T3.7's excuse.
- **Consequences:** T3.8 did not grep `proto=0`. T3.10 deleted the
  exception: `just test` / `just test-net-init` fail the boot if
  `drop_proto != 0` on the happy path. Do not "fix" it by stopping
  the hostfwd watcher.

## D-0050: UDP echo swaps ports and addresses; checksum 0 is 0xFFFF
- Date: 2026-08-16 — Status: accepted
- **Decision:** UDP echo on guest port 7 (`hostfwd=udp::7777-:7`).
  Parse/build uses the 12-byte IPv4 pseudo-header (src, dst, zero,
  protocol 17, UDP length) plus the real UDP header and payload.
  UDP length is summed **twice** — once in the pseudo-header, once
  as the Length field in the real header — that is RFC 768, not a
  double-count bug. A computed checksum of 0 is transmitted as
  `0xFFFF` (0 means "no checksum"). On RX, checksum 0 is **dropped**
  (`drop_csum`) — a **deliberate deviation from RFC 768**, which
  permits zero to mean "no checksum was computed." We do not treat
  optional-checksum as valid. slirp always fills in a real checksum,
  so the QEMU user-net peer never exercises the RFC's zero; a
  real-world peer that legitimately sends 0 is dropped by this
  policy, not by accident. Echo **mirrors** payload and UDP length,
  **swaps** source/dest ports and IPv4 addresses, **recomputes** IP
  checksum, UDP checksum, TTL, and Ethernet dest (gateway MAC). The
  harness waits for serial `UDP ECHO READY` before sending; the
  client is a datagram socket with a 2 s recv timeout (the `nc -u`
  shape) so a silent guest is TEST FAIL, not a hang.
- **Alternatives considered:** accept RX checksum 0 per RFC 768
  (rejected: that is skipping verification, the T3.7 dishonest skip
  applied to UDP; recorded here as a named deviation rather than
  left implicit, because a non-slirp peer may send zero in good
  faith). Rebuild the datagram from parsed fields (rejected:
  echo is a swap; rebuilding is how payload bytes get lost). Fire
  `nc -u` on `DRIVER_OK` (rejected: the guest is still in ARP/ping;
  UDP would sit in the used ring or be dropped as proto-not-yet).
  Call distro `nc -u` directly (rejected: EOF/timeout behavior is not
  portable; a SOCK_DGRAM + `settimeout` is the same packet and is
  fail-closed).
- **Rationale:** the pseudo-header and the 0/`0xFFFF` wrinkle are the
  interview questions; they live in `udp.rs` with a self-test that
  forces a zero computed sum. Dropping RX 0 is stricter than the RFC
  and is defensible against slirp (it always computes one) but is
  still a protocol choice, not "the RFC says so." The harness race
  is the same lesson as T3.5: provoke only after the guest has
  printed that it is polling for this packet.
- **Consequences:** every QEMU invocation gains
  `hostfwd=udp::7777-:7`. `just test-net-udp` is a sibling feature
  (`net-udp-selftest`) so the default boot does not spin 2 s waiting
  for a datagram `just test` never sends. T3.9 moved the echo into the
  app over `recv`/`send`; this entry's wire behavior stays.
  Revisit RX-0 if a non-slirp peer (tap, M4) needs optional-checksum.

## D-0051: Compiled Rust in `.utext` stays inside the app/usys archives
- Date: 2026-08-16 — Status: accepted
- **Decision:** T3.9 links a real `app` crate (and a `usys` wrapper) into
  the existing user sections via linker `EXCLUDE_FILE` on those
  archives' `.text/.rodata/.data/.bss` (D-0044). The image has **one**
  `#[panic_handler]` lang item, and it stays in the kernel: S-mode
  cannot fetch `U=1` pages (SUM does not affect instruction fetch),
  and U-mode cannot fetch `U=0` pages, so a single handler cannot
  serve both. The app crate therefore does not carry a lang-item
  panic handler. Its abort path is `usys::exit` / `unimp` in `.utext`.
  The app is written so rustc does not emit calls into `core`'s
  panicking/fmt/builtins: no `panic!`/`unwrap`/`expect`, no indexing
  that can fail, no `format!`/`println!`, no `f32`/`f64`, overflow
  checks and debug assertions off on the `app` and `usys` packages,
  `opt-level = 1` so small copies inline instead of calling
  `memcpy`/`memset`. `-C no-redzone` is an x86 concern; RISC-V has
  no red zone, so the flag is a no-op here and is not set. `panic =
  "abort"` is already the workspace profile (no unwinder /
  `eh_personality` in either half). `check-utext` requires `app_main`
  to sit in `[__utext_start, __utext_end)` so a failed section match
  cannot hide in kernel `.text`. Unknown mnemonics stay a hard
  error; FP including `c.fld`/`c.fsd`/`c.fldsp`/`c.fsdsp` is
  rejected by name (D-0044). `recv` (6) / `send` (7) join the ABI;
  0 stays reserved; numbers `>= 8` still kill. UDP `send` ignores
  the FIN bit (T3.10's TCP close); a task waiting on a packet stays
  `Running` and spins on `recv` (D-0035, D-0040).
- **Alternatives considered:** `#[panic_handler]` in the app crate as
  well as the kernel (rejected: rustc allows one `panic_impl` per
  image; a second compilation + `objcopy --redefine-sym` is how
  you fake two, and that is exactly the archive-boundary iteration
  this task is forbidden to wander into). Putting the one lang item
  in `.utext` (rejected: a kernel `panic!` would instruction-fault
  fetching U=1 text). `panic = "immediate-abort"` (rejected: needs
  nightly `-Zunstable-options` on 1.97). Sharing `core` helpers
  that are not inlined (rejected: those objects already live in
  kernel `.text`; an `auipc+jalr` from `.utext` into them is the
  silent-wrong outcome `check-utext` exists to catch). Building the
  app for a soft-float target (rejected: D-0044, ABI mismatch).
  Growing `check-utext` with a permissive default for unknown ops
  (rejected: the checker's contract is fail-closed). A `Blocked`
  state for `recv` (rejected: D-0035). Kernel auto-echo remaining
  in `classify_udp` "for the harness" (rejected: T3.9's acceptance
  is the echo moving into the app).
- **Rationale:** the structural risk is not the echo logic; it is
  LLVM emitting a symbol that resolves outside the user sections.
  The mitigations are "don't generate that call" plus a checker
  that fails if we did. The panic-handler split is hardware: two
  privilege levels, one identity map, one lang item.
- **Consequences:** rustc passes `libapp-HASH.rlib` whose members are
  `app-HASH.*.rcgu.o`, and LLVM names string literals
  `.rodata..Lanon.*`. Matching only `*libapp-*.rlib:(.rodata)` left
  those strings in kernel `.rodata` (`auipc` from `.utext` into
  `0x8022xxxx`). The working placement is `#[link_section]` on user
  functions and data, plus matching both the rlib and the `*.rcgu.o`
  member names. If that pairing breaks, **stop** and inspect
  `cargo rustc -- --print link-args` — do not iterate wildcards.
  Symptom of the silent case: `app_main` at `0x8020xxxx`, every test
  green until the first `sret`. The planted `c.fld` image is a
  build-only feature (`utext-c-fld-selftest`); it must not ship in
  the default kernel. `net-udp-selftest` drops `no-sret` so the app
  actually runs. Revisit if a future app needs `core::fmt` or a real
  `memcpy` in `.utext` (that is a local `#[no_mangle]` in `usys`,
  not a link to `compiler_builtins`).

## D-0052: T3.10 handshake only; no RST on FIN or a second 4-tuple
- Date: 2026-08-16 — Status: superseded in part by D-0053
- **Decision:** T3.10 implements LISTEN → SYN_RCVD → ESTABLISHED and
  nothing past that. One listener (guest port 80), one TCB. Duplicate
  SYN in SYN_RCVD re-sends the same SYN/ACK (same ISN). Payload, FIN,
  RST, and a SYN from a second 4-tuple are **dropped with a counter,
  not RST**. Empty ARP cache on a SYN is the same drop (no panic,
  no ARP-and-queue). D-0041's RST-on-unexpected and the close
  sequence land at T3.11.
- **Alternatives considered:** RST every unexpected segment as D-0041
  already says (rejected at this checkpoint: the hostfwd watcher
  `close()`s after `connect()`, slirp sends FIN, and a second
  hostfwd connect is a second 4-tuple; an honest RST would fail the
  T3.10 "no RST" pcap gate without testing close). Queue a second
  SYN until the first TCB is free (rejected: one connection, no
  timer, and T3.11 is when close exists). Panic on a SYN before the
  gateway MAC is cached (rejected: D-0040, remote bytes never panic).
- **Rationale:** the acceptance is a standalone handshake: SYN →
  SYN/ACK → ACK, ESTABLISHED set, no RST. The harness that makes
  slirp ARP (D-0046) is also the harness that completes the
  handshake; it must not be rewritten to hide FINs. Dropping
  unexpected segments keeps the capture honest for this checkpoint
  without pretending close is implemented.
- **Consequences:** `just test-net-init` fires one hostfwd connect after
  the gateway MAC is learned (D-0046 / D-0054).
  `just test` does not use the watcher. `busy` / `unexpected` in `tcp_drop` may be non-zero on a happy
  boot (second SYN, peer FIN after ESTABLISHED). Malformed counters
  (`short`, `doff`, `csum`, `opt`) must read 0. `drop_proto` must
  read 0 (D-0049). T3.11 (D-0053) implements FIN consumption and
  close. A second 4-tuple is still dropped, not queued and not RST:
  that is one TCB, not a second connection.

## D-0053: One-shot HTTP/1.0, Connection: close, one-segment request
- Date: 2026-08-16 — Status: accepted
- **Decision:** T3.11 serves one GET. The app parses a request line in
  the **one segment `recv` returned** (no kernel reassembly, no
  cross-segment buffer). `send` of a fixed `HTTP/1.0 200 OK` body
  with `Connection: close` and the FIN flag is the only data TX and
  the only close the app issues. Stop-and-wait: at most one unacked
  segment; 200 ms `rdtime` RTO from the `recv` poll loop; 8 attempts
  then RST. FIN consumes a sequence number on send (`snd_nxt +=
  len + 1`) and on receive (`rcv_nxt += len + 1`). Close states:
  FIN_WAIT_1 → FIN_WAIT_2 → truncated TIME_WAIT (log, LISTEN);
  CLOSE_WAIT → LAST_ACK when the peer FINs first. An unused TCB
  (no payload delivered to `recv`, no `send`) that sees peer FIN is
  closed by the kernel (FIN+ACK) so LISTEN is restored — that is
  the hostfwd probe, not keep-alive. A second 4-tuple SYN is
  dropped (`busy`), not queued and not RST. The `tcp-drop-first-tx`
  selftest **posts the first data segment** (so the capture has it)
  and **ignores ACKs until one RTO retransmit**; lossless slirp
  would otherwise ACK immediately and hide the timer. While those
  ACKs are held, a peer FIN is also deferred: ACKing the close first
  lets slirp CLOSED and the RTO copy meets RST instead of an ACK.
  The tripwire (D-0037) applies the moment curl has its 200: no
  second `send`, no second TCB, no header-driven keep-alive.
- **Alternatives considered:** reassemble a request line across
  segments (rejected: curl's GET is one segment; a buffer is the
  start of a real HTTP parser and the tripwire forbids it). RST a
  second 4-tuple (rejected: the hostfwd watcher shares the pcap;
  an RST there fails "no RST on the happy path" without proving
  multi-connection). Drop the first TX from the virtio ring
  (rejected: the capture would contain one copy, not two). Full
  TIME_WAIT (rejected: D-0041; a retransmitted peer FIN after we
  are LISTEN meets RST, visible, harmless).
- **Rationale:** the demo is one GET, one response, one FIN pair.
  Everything else is a door D-0037 says not to walk through.
- **Consequences:** the exact body is `whimbrel\n` (9 bytes,
  `Content-Length: 9`). T3.12 flips `just test` to `M3 UNIKERNEL OK`.
  `just test-net-http` is the curl checkpoint; `just test-net-rto` is
  the timer checkpoint. `http-persist` (T3.12) recycles LISTEN after
  close so `just run-http` can serve sequential Connection: close
  connections; it is not keep-alive and still one TCB.
  `recv` returns 0 only after the peer FIN **and** our inflight
  segment is ACKed — otherwise the app would `exit` before the RTO
  could fire. Truncated TIME_WAIT does not clear that EOF. A late
  FIN after LISTEN is dropped, not RST, so the happy-path capture
  stays RST-free (the hostfwd watcher and a retransmitted peer FIN
  share that pcap).

## D-0054: ARP for the gateway at init; do not wait to be asked
- Date: 2026-08-16 — Status: accepted
- **Decision:** `net::init` transmits an ARP request for `10.0.2.2` and
  waits for the reply. That populates the cache D-0047 panics on. The
  kernel no longer waits to be ARPed by slirp (the ~2 s `wait_rx_arp`
  that panics on a standalone boot). We still answer a request for our
  IP if one arrives. GARP still goes out after the cache is filled.
- **Alternatives considered:** keep the hostfwd watcher as a boot
  dependency (rejected: M4 measurement runs and `just run-http` have no
  watcher; a demo that panics without an external connect is not a
  unikernel). Hard-code slirp's MAC (rejected: D-0047 — the cache would
  be ornamental). ARP-and-queue on the first IPv4 TX (rejected: still
  D-0047; init is the one place we may wait).
- **Rationale:** being asked is a harness accident, not a protocol
  requirement. An ARP client is the missing half of RFC 826 and the
  only way D-0047's empty-cache panic means "resolution failed" rather
  than "nobody poked hostfwd in time".
- **Consequences:** `just run-http` boots to LISTEN with nothing else
  running. The T3.5/T3.6 "slirp ARPs us first" pcap chain is no longer
  the default-boot story; the live assert is our request then slirp's
  reply (`assert-pcap-gateway-arp.sh`). D-0046's watcher remains for the
  `net-init-selftest` handshake sibling only, and fires after the cache
  is filled rather than after `TX ARP reply`. D-0047 is amended: the
  panic is a real miss.

## D-0055: M4 methodology frozen before any optimization
- Date: 2026-08-16 — Status: accepted (turbo-off override 2026-08-17)
-   **Decision:** the benchmark protocol is fixed now and every M4 number
  obeys it. Per (system, config) batch: 3 warmup trials marked and
  excluded, 30 recorded trials. Warmup is round-robin across configs;
  recorded trials of every config in a batch are interleaved and
  shuffled so elapsed-time drift hits both arms. Statistics: median and IQR for every
  comparison and before/after claim; minimum shown alongside as the
  observed floor bound; means never. Host controls: one host machine for
  all report numbers, performance governor, `taskset`-pinned QEMU and
  client on separate cores; recorded per batch: QEMU version + binary
  hash, whimbrel git SHA + dirty flag, host kernel, CPU model, governor,
  1-minute load average. Pinning is enforced fail-loudly: the harness
  refuses to aggregate rows with mismatched QEMU version or a dirty
  working tree. Data shape: long/tidy CSV — `results/runs.csv` (one row
  per trial: identity, host metadata, E0-anchored edges, client attempt
  count, pcap path) and `results/phases.csv` (one row per trial × phase:
  ticks, ns since E2, delta, source); a summarizer emits
  n/median/IQR/min/max; report tables are generated from CSV by script,
  never typed. Phase data reaches the dataset by parsing the existing
  machine-shaped `PHASE` serial lines; a line that fails to parse fails
  the batch. The measurement client is a persistent process stamping a
  monotonic clock at ~1 ms cadence (audit finding 32 retires the
  fork-per-attempt curl loop); its measured granularity is reported.
  **Stability criterion (the T4.1 finish line):** two interleaved
  30-trial batches whose per-metric medians agree within max(2%, 200 µs)
  for every metric ≥ 1 ms. This is batch 1 vs batch 2, not safe vs fast
  inside one batch. **No optimization work until that criterion
  holds and the baseline is frozen at a recorded SHA.** Pre-baseline
  corrections (D-0056) and instrumentation (D-0057) are exempt from the
  no-optimization rule, never from the gate list.
- **Alternatives considered:** wide CSV, one column per phase (rejected:
  every new stamp is a schema migration; long format makes T4.2 additive).
  Minimum as the headline statistic — theoretically closest to a floor
  under one-sided noise (rejected for comparisons: an order statistic
  improves with N and is unfair across systems with different noise
  profiles; kept as the shown floor bound).   Mean/stddev (rejected: one
  descheduled run poisons a mean invisibly). Deeper host surgery —
  isolcpus, turbo off (rejected *for gates and for the original
  runs-anywhere host*: breaks that property; `taskset` plus N trials
  plus recorded load average covers what those claims need). **Overridden
  for the dedicated host only — see Consequences.** Record-and-warn on
  QEMU mismatch (rejected: a
  mixed-version CSV is silent corruption; fail loudly is the house rule).
  `-icount` for determinism (already rejected in D-0043).
- **Rationale:** every before/after claim in the report is only as real
  as its "before". A numeric stability criterion makes "the harness is
  ready" a fact instead of a feeling, and it is also the tripwire that
  catches audit finding 12's predicted tick-quantization variance — if
  the criterion fails, the investigation is methodology work with a
  finding at the end, not a reason to lower the bar.
- **Consequences:** trials are ~2–3 s wall, so 30+3 per config is cheap;
  the QEMU invocation is already quadruplicated (audit finding 28) and
  the harness must source a single shared definition rather than become
  a fifth copy. `just bench` regenerating every cited number is the
  milestone acceptance test. Finding 14 (`-C force-frame-pointers=yes`
  in the measured build) is settled inside T4.1 with one A/B batch:
  strip it if the delta clears the stability floor, else record it as a
  stated condition — either way with the number in hand.
  **T4.1 implementation:** `scripts/qemu-args.sh` is the shared QEMU argv
  (boot-test, justfile, bench); `scripts/bench.sh` writes long/tidy
  `results/runs.csv` and `results/phases.csv`. The summarizer refuses
  dirty trees and mixed QEMU/git SHA. **Finding 14 A/B (release+fast-boot,
  N=30 recorded, git `9871d87`, QEMU 8.2.2):** E2→E3g median with
  `-C force-frame-pointers=yes` is 31.062 ms, without is 30.812 ms,
  Δ = +0.250 ms (FP slower). E0→E4 Δ = +0.078 ms. Both sit inside
  max(2%, 200 µs). The operator chose strip (see Finding 14 strip
  below). **Stability criterion (two consecutive
  30-trial batches, git `356b37a`):** not met. release-default shifted
  systematically slower on batch 2 (E2→E3g 98.5 → 105.6 ms; paging,
  DRIVER_OK, listen, freeze, first_rx all moved). release-fast-boot's
  guest-internal phases stayed inside the bar; its E0-anchored edges
  missed by a little (E0→E4 74.0 → 75.6 ms; first-connect 17.58 →
  18.08 ms). The criterion is not widened. Client granularity median
  1.000232 ms (C1; curl was 5–15 ms).
  **Finding 14 strip:** both A/B deltas sat inside the floor, but `.text`
  dropped ~15% (0xc32c → 0xaa1c) and image size is a reported metric.
  `-C force-frame-pointers=yes` is removed from `.cargo/config.toml`
  (release / measured path). Debug re-adds it by merging
  `scripts/cargo-debug.sh` (`just build`, `just debug`, `boot-test.sh`
  PROFILE=debug) so GDB backtraces still work. `RUSTFLAGS` is not used:
  it replaces linker.ld.
  **Batch-order confound:** the first T4.1 pair ran configs as sequential
  blocks and batch 2 always after batch 1, so monotonic host drift reads
  as a systematic batch difference. Recorded trials are now interleaved
  within a batch and shuffled (`shuffle_seed` on every run row; warmup is
  round-robin, 3 per config, then the shuffled recorded schedule).
  **Stability under interleaving:** the criterion still compares **two
  interleaved batches** (batch 1 vs batch 2 per-metric medians, same
  N=30 recorded per config, same max(2%, 200 µs) bar). It is not a
  within-batch comparison of the two arms — default vs fast-boot is the
  treatment contrast and is supposed to differ. The bar is not widened.
  **Steal:** each trial records `/proc/stat` aggregate steal delta
  (`steal_ticks`, `steal_ns`). USER_HZ=100 ⇒ 10 ms/tick, coarser than
  the 200 µs floor and coarser than the first miss (E0→E4 Δ 1.64 ms).
  Steal is diagnostic, not a stability metric.
  **Fresh interleaved pair (git `9678270`, shuffle_seed
  `1786876111394580533`, QEMU 8.2.2, N=30×2 configs×2 batches):** steal
  was 0 on 119/120 recorded trials and 1 tick on one fast-boot trial
  (E0→E4 78.6 ms, not the slowest). Spearman(steal, E0→E4) = −0.017;
  the slow quartile's mean steal was 0. Steal does not explain the slow
  trials. `/proc/stat` steal is the wrong instrument for sub-tick
  jitter. release-default now **passes** batch 1 vs 2 (E2→E3g 106.130 vs
  106.676 ms) — the old 98.5 → 105.6 ms shift was the batch-order
  confound. release-fast-boot still **fails** on the host-side edge only:
  E0→E4 75.509 → 77.292 ms (Δ 1.78 ms, tol 1.55 ms); guest E2→E3g
  30.831 → 30.949 ms stays inside the bar. Spearman(run_order, E0→E4)
  = 0.55 on fast-boot: wall-clock drift remains, and the 2% bar is
  tighter on the shorter arm (~1.55 ms vs ~3.0 ms on default). The
  criterion is not widened. This host is a 4-CPU KVM guest (`hypervisor`,
  `systemd-detect-virt=kvm`, cgroup `pod-…`) with no cpufreq and a
  generic `Intel(R) Xeon(R) Processor` CPUID; D-0055's performance
  governor is `unavailable`. Cumulative steal is nonzero (203 ticks at
  diagnosis start) but almost never lands inside a 2 s trial. Report-grade
  numbers are not obtainable here; the bench host has to be dedicated.
  **Dedicated host (after T4.1 diagnosis):** development and correctness
  gates run anywhere. Every report number comes from a dedicated Ubuntu
  machine whose spec block lives in the report (SETUP.md § dedicated
  measurement host). Required, and **fail-closed** in `scripts/bench.py`
  before a report-grade batch — missing evidence is a fail, not a skip:
  `systemd-detect-virt` = `none`, cpufreq present, every online CPU on
  the performance governor, SMT off, turbo/boost off, steal 0 on every
  trial in the batch. The original alternative that rejected turbo-off
  still holds for gates and for any machine that is not this host; it
  does not hold for report numbers.
  **Turbo-off override (dedicated host):** boost off costs ~20% peak
  clock on the provisioned 7800X3D (4.2 GHz vs 5.05 GHz), so absolute
  numbers are larger — TCG is host-bound. Boost-state and thermal
  variance are removed, which is what the stability criterion measures.
  Every compared system runs on this same host under the same boost-off
  policy, so comparisons are unaffected; only the absolute floor moves.
  Host-control asserts (virt / governor / SMT / boost /
  steal, fail-closed, all five recorded in `runs.csv`) land from the
  dedicated-host tree; do not implement them here.
  The cloud workspace is a pod on a KVM guest with no cpufreq and cannot
  meet this entry's host controls.
  **Harness findings that survive the move:** (1) per-trial `/proc/stat`
  steal at USER_HZ=100 cannot resolve millisecond misses — steal=0 is
  necessary, not sufficient. (2) Interleaving configs and shuffling
  recorded trials fixed a batch-order confound that had looked like a
  guest-internal shift (release-default E2→E3g 98.5 → 105.6 ms under
  sequential blocks; the same contrast passed once trials were mixed).

## D-0056: Pre-baseline corrections (T4.0b)
- Date: 2026-08-16 — Status: accepted
- **Decision:** four audit findings are fixed before the first baseline
  batch, because each changes what the baseline would mean.
  1. **Fail-closed harness (finding 31):** `scripts/boot-test.sh` runs
     under `set -euo pipefail` from line 1, with deliberate `set +e`
     islands only where exit codes are inspected. The build-failure mode
     is exercised once (DEBUGGING.md §4 item 8: an untested failure mode
     is an unwritten assert) — a broken `cargo build` must yield FAIL on
     every gate, never a stale-kernel PASS.
  2. **E3g at publish (finding 9):** the E3g stamp moves between
     `post_tx` (descriptor publish — D-0043's definition) and
     `virtq::notify`. A second stamp `E3g_doorbell` lands after the
     notify store returns. Under TCG the doorbell runs the device model
     synchronously, so E3g_doorbell − E3g prices the device handoff as
     its own measured line instead of silently inflating E3g and
     distorting E3w − E3g.
  3. **Spin, don't `wfi`, in boot RX waits (finding 12):** the `wfi`s in
     `wait_gateway_arp` and `wait_ping_reply` are removed in all
     profiles — one code path (D-0014). This un-quantizes ARP/ping
     latency from the 10 ms tick. **Recorded corollary (finding 13):
     those `wfi`s made timer ticks load-bearing for boot progress — with
     no tick armed, the first `wfi` with nothing pending halts forever,
     ahead of the timeout check. Any future rung that removes tick
     arming from fast-boot is legal only because this entry removed the
     sleeps first.**
  4. **Buffer sizes by construction (finding 36):** the app crate exports
     its recv buffer sizes; the kernel adds `const _` asserts tying them
     to `tcp::PAYLOAD_MAX` and `net::UDP_PAYLOAD_MAX`, and the UDP
     image's buffer grows to match. `recv`'s
     copy-then-consume-everything shape stays; what changes is that
     silent truncation stops being representable.
- **Alternatives considered:** measuring first and fixing after
  (rejected: a baseline with a fail-open harness, a mislabeled E3g, and
  tick-quantized waits is a "before" the report would have to disown).
  Fast-boot-only spins with `wfi` kept in the default profile (rejected:
  two code paths for one loop, and the safe/fast delta would then mix a
  power idiom into the price-of-paranoia line). Moving E3g without
  keeping a doorbell stamp (rejected: the synchronous handoff is exactly
  the kind of term floor-finding wants measured, and it is free to keep).
- **Rationale:** these are corrections to the instruments, not
  optimizations of the apparatus — the distinction D-0055 draws. Each was
  found by reading code the gates already passed, which is the audit's
  point: green gates and zero warnings do not certify that a label tells
  the truth.
- **Consequences:** the spin change costs host CPU during two boot waits
  (bounded by the same 2 s timeouts; D-0040 already accepted that
  polling is free at this workload). The stamp addition touches the
  triplicated justfile phase lists (finding 26) in the same commit.
  `E3g` keeps its name and its D-0043 definition; every prior chat-level
  E3g number predates the harness and dies under the report rule anyway.

## D-0057: Attribution stamps and phase renames (T4.2)
- Date: 2026-08-16 — Status: accepted
- **Decision:** the phase set becomes the audit's decomposition,
  verbatim. New stamps: `frame_init` (after `frame::init`; check_dtb
  rides with it), `task_init`, `page_build`, `page_verify`, `activate`
  (the `satp` switch — finding 3 named this remainder `paging`; the T4.2
  landing names it `activate` so the old composite cannot hide behind
  the same word), `virtq_init` (splitting the
  doubled program+verify out of DRIVER_OK, finding 4), `serving_ready`
  (gateway MAC learned — the true earliest-serve point, finding 6),
  `heap_init` and `accounting` (splitting the freeze delta, finding 7),
  `syn_rx` and `established` (splitting client arrival out of the E3g
  tail, finding 9). Renames: `listen` → `net_init_done` (finding 6).
  Stamp overhead is measured by two adjacent stamps at boot (`stamp_a`,
  `stamp_b`) and reported with the table; every attributed delta is
  quoted against that floor. Phase deltas from `_start` through `E3g`
  must sum to E2→E3g within that floor (harness fail-closed, not a
  visual check). The audit's finding-10 cost inventory and finding-12
  variance prediction were **pre-registered claims**: T4.2's first
  attributed table is checked against both, and agreement or
  disagreement is recorded in the report draft — if they disagree, one
  of them is wrong and that is a result, not an embarrassment. Finding
  12's disagreement is overtaken-by-fix (D-0056.3), not a wrong
  mechanism; both that outcome and finding 10's item-by-item score go
  in the report (`docs/AUDIT-2026-08.md`).
- **Alternatives considered:** keeping the nine-stamp set and attributing
  by code reading alone (rejected: FREEZE already proved labels lie
  while gates stay green). Perf-style sampling under TCG (rejected:
  wrong tool for a 40 ms boot in an emulator; stamps at 100 ns
  resolution are the native instrument). Renaming `E3g` (rejected:
  D-0043 fixed its meaning; D-0056 moved the stamp to match the name).
- **Rationale:** rung attribution is impossible while "paging" contains
  four unrelated costs; every ladder decision downstream keys off this
  table. Instrumentation precedes the baseline freeze because a frozen
  baseline without attribution would have to be re-frozen immediately.
- **Consequences (the co-edit checklist, audit finding 26):** T4.2
  touched `src/phase.rs` (N=22, index consts, NAMES) and collapsed the
  three justfile HTTP greps onto one `phase_names` variable. They can
  collapse that far — the three loops were identical — but they cannot
  merge with `phase::NAMES` without a generator (Rust consts vs shell).
  The harness still parses names from serial and is not a fourth copy.
  A stamp that is legitimately unset in some image must be exempted per
  image, not grepped away. Phase names are frozen after T4.2 so
  `phases.csv` rows stay comparable across the whole ladder.
  **T4.2 landing (this host, ladder ordering only — not a results
  table, not a report number):** one boot each of debug-default,
  debug-fast-boot, release-default, release-fast-boot. Stamp overhead
  (`stamp_b` − `stamp_a`) was 6.8 µs / 7.0 µs on release and 27.6 µs /
  34.2 µs on debug. Phase deltas summed to E2→E3g within that floor.
  Finding 10 vs **release+fast-boot** (the inventory's path), quoted
  against the 6.8 µs floor:
  - **Right class:** `frame_init` is ms (here 93 ms, list-build dominates
    the old "paging" blob); `page_build` 1.4 ms and `page_verify` 2.2 ms
    are both ms, verify ≥ build; `accounting` 5.1 ms (inventory ~6 ms);
    `freeze` itself is 11 µs under fast-boot (the bool store); `heap_init`
    is trivial (30 µs); `sret` is 30 µs; `ping_gateway` is ms on the safe
    profile (`net_init_done` 6.1 ms) once it is not overlapped by an
    early client.
  - **Wrong class / mixed:** `task_init` is 0.89 ms, not µs; `virtq_init`
    first pass is 1.0 ms, not tens of µs; `stvec` (DBCN + CSR + install)
    is 0.29 ms, not µs. Those three share a direction — predicted µs,
    measured sub-ms — a systematic bias in the audit's cost estimates,
    and an observation about pre-registration itself. `timer::init`
    rides inside `frame_init` (the inventory's tick-trap cost is not its
    own line; fast-boot also skips the tick-3 wait). `ping_gateway` is
    **not** ms under CLIENT_EARLY fast-boot: `syn_rx` lands during the
    ARP/ping wait, so the diagnostic RTT is overlapped (finding 6). HTTP
    READY's 11 DBCN ecalls are not a visible E3g-tail line when the
    client is early — `established`→`E3g` is 1.7 ms of serve, not
    console.
  - **Finding 6 confirmed:** on CLIENT_EARLY, `syn_rx` and `established`
    fire before `serving_ready` / `net_init_done`. TCP was serving during
    the ping wait; `listen` was the wrong name twice.
  - **Finding 12 refuted here, overtaken by a fix:** `first_rx` is
    0.60 ms on this boot, not a 10 ms tick-wide IQR. D-0056.3 already
    removed `wfi` in `wait_gateway_arp` / `wait_ping_reply`, so T4.2
    never saw the quantization the audit predicted. The mechanism was
    real at `4660fab`; the prediction was not simply wrong. Report both.
  - **Safe-profile leftover:** without fast-boot, `freeze` is still 6.3 ms
    because `freeze()`'s `free_count()` println argument is evaluated —
    a second walk after `accounting`. Finding 7 called that out for
    fast-boot only. Fast-boot compiles the print out (`println!` →
    `print!` cfg), so only the accounting walk is on the measured path.
    D-0060's O(1) `free_count` therefore collapses **two** walks on the
    safe profile, not one.
  Debug paging is still opt-level=0 (page_build+verify 81 ms fast / 103 ms
  default on this boot), not the cost of paging, and must not migrate into
  a results table (finding 20). **Ladder order after this table:**
  `frame_init` first (free-list build ~93 ms dwarfs everything; not
  paging), then `accounting`, then `page_verify`. Superpages (D-0059)
  re-evaluated once `page_build`+`page_verify` (3.6 ms combined on this
  boot) is a larger share of what remains. Magnitudes are this noisy
  KVM pod; the dedicated host re-measures. No rung starts until that
  baseline exists (D-0055).

## D-0058: Optimization-ladder governance
- Date: 2026-08-16 — Status: accepted
- **Decision:** rungs land one at a time, each as: hypothesis → expected
  gain from the attributed table → land with its co-edit list → full
  gate list green → N-trial safe+fast regeneration → ladder row in the
  draft (before/after medians, IQR, min) → one commit. **A rung is
  eligible only if its attributed projected gain is ≥ 5% of the current
  E2→E3g median**; below that it is declined-with-reason in the ladder
  table. **Planned order (amended after T4.2 attribution):** rung 1
  `frame_init` (bump-pointer / lazy free-list; the old "rung 3" candidate,
  now first because the free-list build — not paging — is the dominant
  kernel term), rung 2 O(1) accounting (D-0060; two `free_count()` walks
  on the safe profile, not one), rung 3 `page_verify`. Superpages
  (D-0059) are **re-evaluated** once `page_build`+`page_verify` is a
  larger share of what remains — they are not next. Further residue,
  still data-driven: `ping_gateway` gated out of fast-boot (needs its
  own decision entry: wire behavior changes; ARP wait and GARP stay in
  all profiles), tick arming under fast-boot (legal only after D-0056.3;
  co-edits the `tick 3` gate), E3g-tail work only if `syn_rx`→`E3g`
  shows kernel time worth the risk. No rung lands until the dedicated
  host freezes a baseline (D-0055). The ladder closes when no remaining
  candidate clears the bar — that closure is the floor declaration the
  report cites.
  **Declined now, recorded so the report can say why:** DBCN
  buffer-write FID 0 (nothing prints on the measured path);
  interrupt-driven networking (D-0040's boot-cost argument is a boot
  benchmark's argument); Sstc under `-bios default` (unprobeable,
  D-0018 — it exists only inside D-0061's variant). The T4.3b audit
  cleanup (findings 33–35, 37–39) is not a rung: it lands after the
  baseline freeze precisely because it must not move any number.
- **Alternatives considered:** batching rungs for fewer measurement
  cycles (rejected: un-attributable regressions; one rung, one row is
  the whole point). A time budget per rung (rejected: calendar-shaped;
  the 5% bar is the returns-shaped equivalent). Optimizing the safe
  profile too (rejected: the safe build is the control; it changes only
  when correctness demands). Keeping the pre-T4.2 order (accounting,
  then superpages, then bump-hybrid) after attribution showed
  `frame_init` ~93 ms (rejected: the 5% bar would have been theater).
- **Rationale:** the ladder's product is the before/after table, and the
  table is only evidence if every row shares the same frozen protocol.
  The 5% bar operationalizes "diminishing returns" so the open-ended
  runway cannot become an unfinished ladder. Attribution is allowed to
  reorder the plan; that is what T4.2 was for.
- **Consequences:** the safe−fast per-phase delta (price of paranoia) is
  recomputed at every rung; the D-0043 promise that verification cost
  survives as its own line is kept by construction. Expected arc, stated
  as an estimate and not a claim: rung 1 collapses the free-list build,
  rung 2 collapses accounting (and the safe profile's second freeze
  walk), then `page_verify` is next among kernel terms; superpages wait
  until paging is a larger share of the remainder. After those, firmware
  (~24 ms class) dominates the honest number and D-0061 is the next
  candidate by the ladder's own rule.

## D-0059: 2 MiB superpages for the RAM interior (amends D-0026)
- Date: 2026-08-16 — Status: accepted (re-evaluate after T4.4–T4.6
  `page_verify`; not next)
- **Decision:** the identity map goes mixed-granularity. 4 KiB leaves
  stay for everything the map distinguishes at 4 KiB grain: kernel image
  W^X regions, guard holes, user sections, task-slot stacks and break
  windows, and the virtio-mmio window. The aligned interior of the big
  R+W RAM range becomes 2 MiB level-1 leaves, with 4 KiB fragments from
  the fine-grained region's end up to the first 2 MiB boundary. A new
  `map_2m` panics on misaligned VA/PA (concept: a superpage PPN with
  nonzero low bits is a hardware fault, D-0026's recorded failure mode).
  The software walker and every verifier become level-aware: each region
  carries an *expected leaf level*, RAM-interior probes must resolve at
  L1 with aligned PPN, everything else at L0, and the wrong level is a
  panic, not a pass. The cliff-specific `require_leaf` probes
  (post-`satp` PC, `__trap_entry`, live `sp`) stay in 4 KiB regions and
  do not change.
- **Alternatives considered:** 1 GiB leaves (still rejected, D-0026's
  original reason: one PTE would span OpenSBI, guards, and every W^X
  boundary). Keeping 4 KiB everywhere and only fixing the walk cost with
  a faster loop (rejected: the cost is the ~32k-entry structure itself —
  build and verify are both linear in leaves). Dropping the verify pass
  under fast-boot instead (rejected: D-0043 keeps verify deliberately;
  shrinking its cost by shrinking the structure preserves the
  price-of-paranoia finding instead of deleting it).
- **Rationale:** D-0026 said revisit "only with an explicit alignment
  check on the leaf PPN" — this entry is that revisit, with the check in
  both the mapper and the verifier. Pre-T4.2, page_build + page_verify
  were expected to be the dominant kernel term. T4.2 showed they are
  3.6 ms combined on the KVM-pod boot, behind `frame_init` (~93 ms) and
  `accounting` (~5 ms) — so this rung waits until those two have landed
  (or been declined) and paging is a larger share of what remains.
  When it does land, leaf count drops from ~32.6k to a few hundred, and
  verification cost scales down with it — measured twice, before and
  after, which is itself a result about what verification costs are
  made of.
- **Consequences — the co-edit checklist (audit findings 24/25/27); every
  item is walked in the same change or the rung does not merge:**
  1. `src/task.rs` frames-consumed assert: `tables != 67` and the
     leftover split (finding 24) — recompute and update deliberately.
  2. `src/page.rs` doc comment deriving 67 (`:113-119` at `4660fab`).
  3. `walk()`'s superpage panic citing D-0026 (`:356-361`) — becomes the
     level-aware acceptance path.
  4. `assert_range` `level == 0` (`:526`) and `require_leaf` L0-only
     (`:720`) — per-region expected level.
  5. `virtq` pool verification through `require_identity_rw*`
     (`src/virtq.rs:305-341,359`) — the pool lives in `.bss` (4 KiB
     region) and must still verify at L0.
  6. D-0036's "69 frames (67 tables + 2)" amendment and D-0039's
     "tables_used is 67" consequence — prose updated with the new
     derivation.
  7. justfile probe-format greps (`:87-94`) if the printed row format
     grows a level column (finding 27).
  8. DEBUGGING.md gains the superpage first-response note (`info mem`
     cross-check; misaligned-superpage signature).
- Revisit trigger: none — after this lands, D-0026's 4-KiB-only rule is
  superseded for the RAM interior and stands everywhere else.

## D-0060: O(1) frame accounting (rung 2)
- Date: 2026-08-16 — Status: accepted (lands at T4.5; was rung 1 before
  T4.2 reordered the ladder)
- **Decision:** `alloc_frame` / `free_frame` maintain an allocated
  counter; `free_count()` becomes `TOTAL − allocated`, O(1). The
  `task::enter` frames-consumed assert keeps its exact semantics at
  ~zero cost. The paranoia is not deleted — it is made free: the safe
  build's `frame::self_test` gains a cross-check of the counter against
  a full list walk, so counter drift cannot hide, and `stress`'s
  restored-list assertion keeps a full walk on its own path (audit
  finding 30) so the storm still verifies the actual list.
- **Alternatives considered:** deleting the accounting assert (rejected:
  it caught nothing yet, but it is exactly the boot-time invariant check
  this project keeps; the audit showed its cost, not its uselessness).
  Gating the assert out of fast-boot (rejected: then safe and fast
  diverge on an invariant, and the safe−fast delta stops meaning
  "verification cost" and starts meaning "different kernels"). Keeping
  the walk and just labeling it (rejected: ~6 ms for a subtraction's
  worth of information fails D-0014 in the other direction).
- **Rationale:** the audit's sharpest finding was that this walk hid
  inside a stamp named "freeze". The fix demonstrates the ladder's
  preferred move: keep the check, collapse its cost, and let the
  before/after row show paranoia becoming free.
- **Consequences:** `free_count()` stops being evidence about list
  integrity (the counter is bookkeeping, not a walk); integrity evidence
  lives in the safe build's cross-check and the stress storm. The
  freeze-adjacent `accounting` phase delta should collapse to ~µs;
  finding 7's ~6 ms prediction is the before row. The safe profile's
  `freeze()` still evaluates `free_count()` as a `println!` argument
  (fast-boot compiles that print out) — a second full-list walk after
  the accounting stamp. This rung therefore fixes **two** walks on the
  safe profile, not one.

## D-0061: `-bios none` measurement variant (scoped amendment to D-0003)
- Date: 2026-08-16 — Status: accepted (investigation; lands at T4.7 or
  is abandoned by its own criteria)
- **Decision:** one variant exists to measure firmware cost by removal.
  `-bios default` remains the platform and the default for every gate
  and every primary number; the variant is a build lane and one report
  exhibit. Design: a pure-boot M-mode shim linked at 0x8000_0000 in the
  same ELF (second LOAD segment; kernel keeps its 0x8020_0000 link
  address and S-mode identity). The shim programs a PMP catch-all, full
  delegation (`medeleg`/`mideleg`), `mcounteren.TM`, `menvcfg.STCE`
  (Sstc), then `mret`s into the existing `_start`. **No resident M-mode
  services:** timer = `csrw stimecmp` at D-0018's reserved one-site
  seam; console = polled NS16550A TX in S-mode (D-0004 revisited for
  this variant only); shutdown = sifive_test store (D-0017's toolbox);
  UART and sifive_test pages mapped at build (D-0039 pattern). `mtvec`
  points at a park-with-diagnostic — after boot, any M-mode trap is a
  bug and says so. **Allowlisted S-kernel seams:** entry, timer-arm
  site, console backend, shutdown backend, the two page mappings.
  **Abandon criteria, returns-based:** stop and write up the partial
  result if (a) the variant demands S-kernel changes beyond the
  allowlist, (b) the first working boot shows E0→E4 savings under 2× the
  largest remaining S-mode rung, or (c) M-mode debugging exceeds what
  the DEBUGGING.md channels can name.
- **Alternatives considered:** pure M-mode kernel (rejected: `satp` does
  not govern M-mode, so paging/W^X/U-isolation — the project's identity
  and its measurable syscall boundary — evaporate). Resident mini-SBI
  implementing DBCN/TIME/SRST behind the same ecall ABI (rejected: keeps
  an M trap handler, an M stack, and the MTI→STIP forwarding dance — the
  structure whose cost we are removing, rebuilt small). Skipping the
  variant and citing OpenSBI's cost as an assumption (rejected: it is
  the largest single term in the honest number; floor-finding measures
  it or does not claim it).
- **Rationale:** the with/without pair turns firmware cost from an
  assumption into a measurement, and it carries a structural finding no
  table row can: mainline riscv64 Linux is an S-mode SBI consumer and
  cannot take this rung — the unikernel can absorb the firmware layer,
  the general-purpose OS cannot. Sstc is available here precisely
  because we own `menvcfg` — D-0018's objection was unprobeability under
  firmware, and the entry reserved the one-site seam this variant uses.
- **Consequences:** variant touchpoints per audit finding 29 (`-bios
  default` in four harness locations; `measure-e2.sh`'s reset asserts
  are meaningless under the variant; `linker.ld` entry; check-utext's
  kernel_lo stays valid). A `just test-m` lane covers a gate subset
  (boot, net, HTTP, fast-release); the full 16-gate list stays on
  `-bios default`. Delegating cause 2 becomes possible in the variant
  (M2's undelegated-illegal-instruction limit would lift) — noted as an
  observation, not built upon: scope stays measurement. E2 ≈ E1 in the
  variant; the firmware row of its table is ~0 by construction and the
  exhibit says so.

## D-0062: Linux baseline — buildroot, /init-is-the-server, two rows
- Date: 2026-08-16 — Status: accepted
- **Decision:** buildroot at a pinned release, sha-recorded.
  `qemu_riscv64_virt_defconfig` base; kernel config trimmed toward
  tinyconfig keeping serial console, virtio-mmio + virtio-net, IPv4 TCP,
  initramfs, devtmpfs, ELF binfmt; modules, IPv6, block, and everything
  else discoverable-as-unused off; each delta lives in a committed
  defconfig fragment. **Two Linux rows:** trimmed (primary — the
  good-faith floor attempt) and stock defconfig (reference — what tuning
  bought). Initramfs is a hand-rolled cpio containing `/init` and a
  console node; `/init` *is* the server: static C, no busybox, no shell —
  socket, `SO_REUSEADDR`, bind :80, listen, write `READY`, accept loop,
  single read, write the byte-identical 92-byte response, close.
  Cmdline primary: `console=ttyS0 quiet loglevel=0 rdinit=/init`;
  secondary instrumented config: `loglevel=7` + `CONFIG_PRINTK_TIME` +
  `initcall_debug`. **Edge mapping:** cross-system comparisons ride only
  on client-observed edges (E0 → first-connect, E0 → E4), identical for
  all systems; Linux's phase decomposition comes from the instrumented
  run's printk/initcall timestamps and is presented as its own exhibit
  with the asymmetry stated — different instrument, measured on the
  logging config, quiet-vs-instrumented headline delta shown. Identical
  conditions: same QEMU binary, `-machine virt`, single CPU, default
  128 MiB, same netdev/hostfwd/filter-dump.
- **Alternatives considered:** busybox init + httpd (rejected: every
  userspace byte between kernel and server is a confound; PID-1-is-the-
  accept-loop is the honest analogue of app-in-image). One
  maximally-tuned Linux row (rejected: invites "you hobbled Linux" and
  "you didn't tune enough" simultaneously; two rows plus a published
  config make the tuning claim falsifiable). Fabricating E2-anchored
  stamps for Linux from serial timing (rejected: precision theater;
  coarser-but-labeled beats fake-comparable). Distro kernel + custom
  initramfs (rejected: unpinnable config surface; buildroot pins the
  whole toolchain).
- **Rationale:** the comparison's integrity lives in the shared
  client-observed edges and the identical wire artifact (same 92 bytes,
  same handshake shape in the pcap); everything guest-internal is
  per-system evidence, honestly labeled. The threats section states
  plainly that a Linux boot-time specialist could likely do better and
  the config is published for falsification — we claim *a* minimal
  Linux, not *the* minimal Linux.
- **Consequences:** `bench/linux/` (or equivalent) holds the defconfig
  fragment, `server.c`, and a build script with pinned tarball hash;
  D-0030's reservation-vs-working-set caveat attaches to the memory
  exhibit. The build is host-heavy but mechanical and cached.

## D-0063: Unikraft spike — go/no-go and the no-core-patches line
- Date: 2026-08-16 — Status: accepted
- **Decision:** pin the unikraft/unikraft PR #1698 branch commit and the
  kraftkit version in this entry when the spike starts. **Go** = the
  HTTP example builds for qemu/riscv64 at the pin, boots on our pinned
  QEMU with documented flag deltas, and answers the harness client.
  **No-go** = build failure surviving config-level fixes; riscv64
  network path nonfunctional; or any fix requiring patches to Unikraft
  internals. **The no-core-patches line is both the go/no-go and the
  abandon criterion:** config and build-system fixes leave "Unikraft"
  meaning Unikraft; core patches would make the row "our fork", which
  contaminates the comparison — the spike ends where configuring their
  system becomes developing it. Fallback outcomes per D-0043, in report
  terms: (1) works — three-way on client-observed edges plus their
  native boot instrumentation as a labeled per-system exhibit;
  (2) different-ISA only — a separate exhibit that never shares a table
  with riscv64 numbers, plus a source-level riscv64 boot-path analysis;
  (3) does not run — two-way quantitative plus a qualitative Unikraft
  section from source, stated in the abstract, not a footnote.
  "Identical conditions" = same host, same pinned QEMU (a required
  different QEMU version triggers a Whimbrel control row under that QEMU
  to bound the version effect), same machine/slirp/hostfwd topology,
  same client protocol, same first-byte edge; every deviation goes in a
  deltas table. Sequenced immediately after the baseline freeze so the
  comparison section's shape settles while the draft is young.
- **Alternatives considered:** patching their riscv64 port to make the
  three-way happen (rejected: the number would describe our fork).
  Waiting for the PR to merge (rejected: unbounded external dependency;
  the fallback ladder exists so the report converges regardless).
  Skipping Unikraft (rejected: the comparison against a mature unikernel
  is the context that makes the floor claim interesting).
- **Rationale:** the spike is bounded structurally, not by calendar: its
  end state is one of three pre-named report shapes, so no outcome is a
  schedule failure — only an unrecorded outcome would be.
- **Consequences:** the pin (commit + kraftkit version) is recorded here
  at spike start; whichever fallback fires, the report's abstract states
  the comparison shape in its opening paragraphs.

## D-0064: Report structure, claims discipline, convergence, audits, quizzes
- Date: 2026-08-16 — Status: accepted
- **Decision:** report structure: abstract → background (short) →
  architecture of the apparatus (decision-log distilled; the deliberate
  U/S choice and its measurement consequence; what our TCP omits and why
  it is invisible at this workload) → methodology (edges per D-0043,
  protocol per D-0055, client, pinning, stamp overhead) → results →
  threats to validity → future work → appendices. **Centerpiece exhibit
  columns, fixed now:** phase | what the work is | safe median | fast
  median | fast IQR | fast min | after-ladder median | Δ vs baseline |
  structurally necessary? — one row per attributed phase; the safe−fast
  pair is the price-of-paranoia finding; the last column is the
  floor-finding argument made row by row. Companion exhibits: the ladder
  table (rung × cumulative E2→E3g, declined rungs included with
  reasons) and the cross-system table (system × E0→first-connect,
  E0→E4, image bytes, RAM; median/IQR/min; N stated). **Claims
  discipline:** results claim only measured medians under stated
  conditions; "fastest" never appears without its conditions clause in
  the same sentence; floor language is "minimum structurally necessary
  under these conditions, bounded below by the rows argued necessary";
  the Linux row is "a minimal Linux tuned in good faith, config
  published". **Appendix, created with the skeleton:** "numbers that
  must be regenerated" — seeded from audit findings 16–23, listing every
  inherited quantitative claim with its disposition (regenerate /
  historical-only / structural), so the kill-list exists before any
  prose does. **Draft-early:** the skeleton is written with real numbers
  at T4.3; all later work edits the draft; exhibit tables are generated
  from CSV. **Second audit:** inside T4.11, between the
  content-complete draft and the quiz — same findings-only format as
  `docs/AUDIT-2026-08.md`, scoped to what changed since it (superpage
  walker/verifier as landed, harness as-built, the `-bios none` shim if
  it landed, and every report number checked against the CSVs that
  claim to generate it); recorded as `docs/AUDIT-<date>.md`; blockers
  fixed before the quiz. **Quizzes:** the comprehensive end-of-project
  quiz sits between the second audit and the final revision pass, so
  what it surfaces marks sections needing rework; the standing
  5-question milestone quiz happens at T4.12 as usual. **Convergence
  gate** (duplicated in PLAN.md; the PLAN copy is normative): harness
  stable and all numbers regenerated; ladder closed by the 5% bar;
  `-bios none` concluded either way; comparison section in its selected
  fallback shape; threats each mitigated-and-measured or stated; second
  audit's blockers closed; both quizzes done; sign-off.
- **Alternatives considered:** writing the report after the data is
  "done" (rejected: draft-early is the structural rule — a skeleton with
  real numbers exists from T4.3 and everything edits it). Quiz after
  final (rejected: ceremonial). Hand-typed exhibit tables (rejected:
  the one mechanism that guarantees prose cannot drift from data is
  generating tables from the CSVs). Skipping a second audit because the
  first was clean-ish (rejected: the first audit's premise — green gates
  do not certify labels — applies with more force to code written during
  a measurement campaign).
- **Rationale:** the report is the artifact; its integrity mechanisms —
  generated exhibits, the regeneration appendix, pre-registered
  predictions citable from `docs/AUDIT-2026-08.md`, a scoped second
  audit — are what let it claim floor-finding instead of benchmarketing.
- **Consequences:** `report/` lives in-repo; markdown source plus a
  table-generation script over `results/*.csv`; `just bench`
  regenerating every cited number is the acceptance test; the
  threats-to-validity list opened at T4.0 (TCG ≠ hardware; slirp as
  peer; client granularity measured; single hart and fixed RAM;
  debug-era history killed by the regeneration rule; Linux-tuning
  fairness; Unikraft pin; instrumentation observer effect; host
  variance; E3w fidelity; reservation vs working set per D-0030) is
  maintained in the draft from day one.

