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
  before the freeze. Any M3 requirement to allocate after boot must reopen
  this entry explicitly rather than quietly calling `unfreeze()`; the likely
  answer there is `SIE` masking plus preallocated pools, and it would amend
  D-0028 rather than replace it. Boot prints `frames frozen: free=N` so the
  transition is visible in every serial log.
