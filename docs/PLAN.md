# PLAN — rv64gc Unikernel

Long-term plan for a minimal RISC-V (rv64gc) unikernel in Rust, running on
QEMU's `virt` machine over OpenSBI, with a single application compiled into the
image running in U-mode over a 5-syscall interface, plus a benchmark report
against a minimal Linux VM.

## How to read this document

- **Milestones M0–M4 are a fixed sequence.** Each milestone's acceptance test
  defines "done". No milestone starts before the previous one's acceptance test
  passes.
- **Effort tiers** (per task, not per milestone):
  - **S (small)** — one focused sitting; tens of lines; low bug risk.
  - **M (medium)** — one or two sittings including reading; moderate bug risk;
    may need one GDB session.
  - **L (large)** — several sittings; high bug risk; expect dedicated debugging
    time and re-reading of the spec. There are only a handful of L tasks in the
    whole project — they are the intellectual core.
- **Units of work, not calendar time.** Nothing here is scheduled. A milestone
  is done when its acceptance test passes and the end-of-milestone ritual
  (glossary/decisions update + 5-question quiz, see `.cursor/rules/project.mdc`)
  is complete.
- M0 and M1 are detailed to individual-task resolution below. **M2–M4 are
  intentionally kept at task-list resolution** and marked
  `[TO BE DETAILED at milestone start]` — the first action at each of those
  milestones is expanding its section to full resolution using what prior
  milestones taught us, and getting sign-off before code.

## Milestone overview

| Milestone | Name | One-line goal | Status |
|---|---|---|---|
| M0 | Boot | OpenSBI → kernel entry → UART "hello" → clean QEMU exit | in progress |
| M1 | Fundamentals | Traps, SBI timer interrupts, frame allocator, Sv39 paging, heap | not started |
| M2 | Execution | U-mode, 5 syscalls, context switch, preemptive scheduling of 2+ tasks | [TO BE DETAILED at milestone start] |
| M3 | Unikernel | App-in-image as sole U-mode task, virtio-net, tiny HTTP responder | [TO BE DETAILED at milestone start] |
| M4 | Evaluation | Scripted reproducible benchmarks vs minimal Linux VM + technical report | [TO BE DETAILED at milestone start] |

---

# M0 — Boot

**Goal:** QEMU with `-bios default` loads OpenSBI, OpenSBI jumps to our kernel
in S-mode at 0x8020_0000, the kernel prints a hello message over the UART (via
the SBI console), then shuts the machine down so QEMU exits cleanly with code 0.

## Prerequisite concepts

Understand each of these before (or while) doing the tasks. Each is a concept
you should be able to explain out loud, unprompted, in an interview.

**1. RISC-V privilege levels (M/S/U).** A RISC-V hart executes at one of three
privilege levels: Machine (M) is the highest and is where firmware lives with
full hardware access; Supervisor (S) is where operating systems live, with
access to virtual memory control and a subset of CSRs; User (U) is where
applications live, with no CSR access. Privilege only changes via traps (going
up) and the `mret`/`sret` instructions (going down). Our kernel spends its whole
life in S-mode; OpenSBI stays resident in M-mode and services our `ecall`s; the
app will eventually run in U-mode.

**2. SBI and the boot chain.** The Supervisor Binary Interface is the "syscall
interface" between an S-mode kernel and M-mode firmware: the kernel loads a
function/extension ID into registers and executes `ecall`, trapping into M-mode
where OpenSBI handles it (console output, setting timers, shutting down). The
boot chain on QEMU `virt` is: QEMU's built-in ROM at 0x1000 runs a few
instructions, jumps to OpenSBI at 0x8000_0000 (start of RAM), OpenSBI sets up
M-mode (PMP, trap delegation), then `mret`s into our kernel at 0x8020_0000 in
S-mode with `a0 = hartid` and `a1 = pointer to the device tree blob`.

**3. The QEMU `virt` memory map.** Physical RAM starts at 0x8000_0000 (default
128 MiB, so it ends at 0x8800_0000). OpenSBI occupies the first ~322 KiB of
RAM (observed: `Firmware Base 0x80000000`, `Firmware Size 322 KB`) and
protects itself with PMP — touching it from S-mode causes an access
fault, which is a classic mystery crash. Memory-mapped devices live below RAM:
UART (NS16550A) at 0x1000_0000, virtio-mmio slots at 0x1000_1000–0x1000_8000,
PLIC at 0x0C00_0000, CLINT at 0x0200_0000, and the "test finisher" device at
0x0010_0000. There is no BIOS-style firmware to enumerate any of this; addresses
come from the device tree or (our approach) from reading QEMU's source and
hardcoding them with a comment.

**4. Linker scripts, link address vs. load address.** The compiler emits code
that assumes it will run at specific addresses (for absolute references and for
the entry point). The linker script is where we state that assumption: we place
our first section at 0x8020_0000 because that is where QEMU loads a `-kernel`
payload and where OpenSBI jumps. If the link address and the actual load/jump
address disagree, the very first instruction fetch or the first absolute load
goes to the wrong place and the machine hangs with zero output — the most
common M0 failure. We also use the script to define symbols (`__bss_start`,
`__kernel_end`, stack top) that startup code needs.

**5. What Rust code needs before it can run.** Unlike a hosted program, nothing
has prepared the world for us: there is no stack pointer, `.bss` (all zero-
initialized statics) is whatever RAM happened to contain, and there is no
`main` caller. Our handwritten assembly entry must: set `sp` to a stack we
reserved in the linker script, zero `.bss`, and only then call Rust. Rust
additionally requires a `#[panic_handler]` because `core` needs somewhere to go
when an invariant fails — ours will print the panic location and message
(failing loudly) once the console works.

**6. Console options: SBI console vs. raw UART driver.** Two ways to print: (a)
ask OpenSBI via an `ecall` to output a byte (it owns the UART already, this is
~10 lines), or (b) drive the NS16550A registers at 0x1000_0000 ourselves.
For M0 we use the SBI console — it is the minimal, legible choice and works
even before we map any device memory in M1. A raw UART driver is deliberately
out of scope until/unless a later milestone needs it (decision D-0004 territory;
revisit only if M3 interrupt-driven console I/O demands it).

**7. Exiting QEMU cleanly.** "Kill the terminal" is not an acceptance test. Two
clean mechanisms: the SBI system reset extension (`SRST`, EID 0x53525354) asks
OpenSBI to shut down, which QEMU implements as process exit; or writing magic
values to the sifive-test device at 0x0010_0000 (0x5555 = pass). We use SBI
SRST as the primary mechanism (consistent with "talk to firmware, not raw
hardware, until we must") — see D-0011. A clean exit also unlocks scripted
testing: `just test` can run QEMU headless and inspect its output and exit.

## Tasks

### T0.1 — Verify the environment boots OpenSBI alone — S
Follow `docs/SETUP.md`; run bare QEMU with no kernel.

- **Acceptance:** `qemu-system-riscv64 -machine virt -nographic -bios default`
  prints the OpenSBI banner (version line, platform `riscv-virtio,qemu`).
  Without a `-kernel`, `Domain0 Next Address` is `0x0` — OpenSBI has nowhere
  to jump. With our kernel it is `0x80200000` in S-mode (already observed).
  Exit with `Ctrl-a x`.

### T0.2 — Kernel entry: linker script + assembly `_start` — M
The scaffolding (linker script, `_start` that sets `sp` and parks) already
exists; this task is to *understand* it line by line, then extend `_start` to
zero `.bss` and `call kmain` passing through `a0`/`a1`.

- **Acceptance (no console yet, so we use GDB):** `just debug` in one terminal,
  `just gdb` in another; `break kmain`, `continue` stops in `kmain` with
  `p/x $sp` inside our stack region, `p/x $a0` = 0 (hartid) and `p/x $a1`
  pointing into high RAM (the DTB). `info registers sstatus` confirms S-mode
  context as set up by OpenSBI.

### T0.3 — SBI console output + `println!` — M
An `sbi` module with a raw `ecall` wrapper; DBCN `console_write_byte`
(EID `0x4442434E`, FID 2 — see D-0015); a `core::fmt::Write` implementation
and `print!`/`println!` macros. Probe DBCN via BASE before the first write.

- **Acceptance:** `just run` prints exactly:
  ```
  kestrel: hello from hart 0, dtb at 0x<some address in RAM>
  ```
  after the OpenSBI banner.

### T0.4 — Panic handler that prints, then parks — S
Replace the parking panic handler: print `PANIC at <file>:<line>: <message>`
via the console, then `wfi` loop. Verify with a deliberate `panic!` then remove
it.

- **Acceptance:** a temporary `panic!("selftest")` in `kmain` produces
  `PANIC at src/main.rs:<line>: selftest` on the serial output.

### T0.5 — Clean shutdown via SBI SRST — S
`sbi::shutdown()` using the SRST extension; call it at the end of `kmain`,
printing a final marker line first.

- **Acceptance:** `just run` prints `M0 BOOT OK`, QEMU's process exits on its
  own (no `Ctrl-a x`), and `echo $?` is `0`.

### T0.6 — Wire the headless boot test — S
Make `just test` (already scaffolded) assert on the marker.

- **Acceptance:** `just test expect="M0 BOOT OK"` prints `TEST PASS` and exits 0.

## Milestone acceptance test

```
$ just test expect="M0 BOOT OK"
```
prints `TEST PASS: found "M0 BOOT OK"` and exits 0. And interactively:
```
$ just run
... OpenSBI banner ...
kestrel: hello from hart 0, dtb at 0x87e00000
M0 BOOT OK
$ echo $?
0
```
(The exact DTB address may differ; it must lie in RAM.)

## Risks and likely failure modes

- **Silent hang, no output at all:** link address ≠ 0x8020_0000; entry section
  not placed first in the image; ELF entry point not `_start`. Check
  `just objdump` / `readelf -l` before suspecting anything else.
- **Hang or garbage after some output:** `sp` not set or set to unmapped/too-low
  address; `.bss` not zeroed (statics contain junk — symptoms often deferred
  and weird).
- **Access fault immediately:** touched OpenSBI's protected RAM below
  0x8020_0000 (PMP violation).
- **`ecall` returns garbage / no output:** wrong register convention — SBI takes
  EID in `a7`, FID in `a6`, args in `a0..a5`; legacy extensions differ (no FID).
- **Works interactively, test hangs:** kernel never calls shutdown, so headless
  QEMU never exits — the test recipe uses `timeout` as a backstop, but the
  acceptance requires a real clean exit.

---

# M1 — Fundamentals

**Goal:** the kernel can take traps without dying (printing full diagnostic
state for unexpected ones), receives timer interrupts via SBI, owns physical
memory through a frame allocator, runs with Sv39 paging enabled and the kernel
identity-mapped with correct permissions, and has a working heap (`Box`, `Vec`).

## Prerequisite concepts

**1. CSRs — control and status registers.** CSRs are per-hart registers
addressed by number, accessed only via `csrr`/`csrw`/`csrs`/`csrc`
instructions, that configure privileged behavior. The S-mode set we care about:
`stvec` (trap vector address), `sstatus` (global state incl. interrupt-enable
bit SIE and previous-privilege bit SPP), `sie`/`sip` (which interrupt classes
are enabled/pending), `scause`/`sepc`/`stval` (what/where/detail of the last
trap), `satp` (paging mode + root page table), and `sscratch` (a free register
for the trap handler's use). Reading them is also our main debugging tool.

**2. What the hardware does on a trap — exactly.** When a trap targets S-mode,
the hart atomically: saves the interrupted `pc` into `sepc`, writes the cause
into `scause` (top bit = interrupt vs. exception), writes a cause-specific
detail into `stval` (e.g. the faulting address), saves the current interrupt-
enable state (`sstatus.SIE → sstatus.SPIE`, then clears SIE) and privilege
(`sstatus.SPP`), and jumps to the address in `stvec`. **Nothing else is saved.**
All 31 general-purpose registers still contain the interrupted code's values —
saving and restoring them is entirely our job, which is why the trap handler
begins and ends in assembly. `sret` reverses the process: restores privilege
from SPP, interrupt state from SPIE, and jumps to `sepc`.

**3. Interrupts vs. exceptions, and delegation.** Exceptions are synchronous
(caused by the executing instruction: illegal instruction, page fault, `ecall`);
interrupts are asynchronous (timer, external device, software). By default all
traps go to M-mode; OpenSBI sets `medeleg`/`mideleg` at boot to delegate most
S-relevant traps down to us — that's the only reason our `stvec` ever fires.
An interrupt is taken only when its bit is set in `sie` AND `sstatus.SIE` is
set (when in S-mode) AND it is pending in `sip`.

**4. Time and timers via SBI.** rv64 harts expose a real-time counter readable
via `rdtime`; on QEMU `virt` it ticks at 10 MHz (`timebase-frequency` in the
DTB). There is no "periodic timer" — there is one comparator: you ask for the
next interrupt at an absolute counter value via the SBI TIME extension
(`sbi_set_timer`), the timer interrupt fires when `time >=` that value, and the
handler must arm the next one itself. Forgetting to re-arm (interrupt fires
once, never again) and forgetting that the argument is absolute, not a delta,
are the two classic bugs.

**5. Physical memory and frame allocation.** After boot we own RAM from the end
of our kernel image (`__kernel_end`, from the linker script) to the end of RAM
(0x8800_0000 with QEMU's default 128 MiB — we hardcode this and assert against
it rather than parse the DTB; see D-0012). Paging hardware deals in 4 KiB
pages, so we manage this range as 4 KiB frames. Design: a free-list allocator
(each free frame stores the address of the next free frame in its own first 8
bytes) — O(1) alloc/free, ~60 lines, zero metadata overhead, and trivially
explainable. Everything that needs memory later (page tables, heap, task
stacks) sits on top of this.

**6. Sv39 address translation.** Sv39 maps 39-bit virtual addresses using a
three-level radix tree of 512-entry, 4 KiB page tables: VA bits [38:30] index
level 2, [29:21] level 1, [20:12] level 0, [11:0] carry through as the page
offset. Each PTE holds a physical page number plus flag bits — V (valid),
R/W/X (permissions; if any of R/W/X is set the PTE is a leaf, at any level —
that's how 2 MiB and 1 GiB "megapages" work), U (U-mode accessible), A/D
(accessed/dirty), G (global). Bits [63:39] of a valid VA must equal bit 38
(sign extension) — kernel addresses like 0x8020_0000 are fine. Translation is
activated by writing `satp` = mode 8 (Sv39) | root table's physical page
number.

**7. The TLB and `sfence.vma`.** The hart caches translations in a TLB and is
allowed to keep using stale entries after you edit page tables or switch
`satp`. `sfence.vma` flushes those cached translations. Rule for this project:
execute `sfence.vma` after writing `satp` and after any PTE modification. With
a single address space this costs almost nothing and eliminates an entire
class of "works until it doesn't" bugs. Related QEMU-specific gotcha: set the
A and D bits in kernel PTEs up front — implementations (QEMU included,
depending on version/config) may fault instead of setting them in hardware.

**8. A heap in `no_std` Rust.** `alloc` (`Box`, `Vec`, `String`) becomes
available in `no_std` the moment we provide a `#[global_allocator]`
implementing `GlobalAlloc` (`alloc`/`dealloc` with size+alignment). We
hand-roll a simple allocator (linked-list of free blocks over a fixed heap
region built from frames) rather than pull in a crate — the point is being
able to defend it (D-0013). Allocation failure panics loudly with the
requested size; there is no OOM recovery story in a unikernel.

## Tasks

### T1.1 — CSR access module — S
`riscv::csr` module: typed read/write/set/clear helpers for the CSRs listed
above (macro or per-CSR functions — keep it boring), plus bitfield constants
with spec citations.

- **Acceptance:** `kmain` prints `sstatus`, `sie`, `stvec` as hex at boot;
  values are sane (e.g. `stvec` still 0 or OpenSBI's leftover before we set it).

### T1.2 — Trap vector, trap frame, dispatch — L
Assembly `__trap_entry`: allocate a trap frame on the stack, save all 31 GPRs
(+ `sepc`, `sstatus`), call Rust `trap_handler(&mut TrapFrame)`, restore,
`sret`. `stvec` set to it (direct mode — note the 4-byte alignment
requirement). Rust dispatch on `scause`: known causes handled, **everything
unknown panics printing `scause`/`sepc`/`stval` decoded** per the fail-loudly
rule.

- **Acceptance:** a deliberate `unsafe { asm!("ebreak") }` in `kmain` prints a
  trap report with `scause=3 (breakpoint)`, `sepc` = the ebreak address, then
  execution *continues* past it (handler advances `sepc` by the instruction
  width) and prints `TRAP OK`.

### T1.3 — Timer interrupts via SBI — M
Enable `sie.STIE` and `sstatus.SIE`; arm the first timer via SBI `set_timer`;
on supervisor-timer interrupt, increment a tick counter and re-arm at
`now + 1_000_000` (100 ms at 10 MHz). Print a line every 10 ticks.

- **Acceptance:** `just run` prints `tick 10`, `tick 20`, `tick 30` at ~1 s
  wall-clock intervals (then shuts down after 30 for the test:
  `just test expect="tick 30"` passes).

### T1.4 — Physical frame allocator — M
Free-list allocator over `[align_up(__kernel_end, 4K), 0x8800_0000)`.
`alloc_frame() -> PhysFrame` (zeroed), `free_frame(PhysFrame)`. Panics on
exhaustion, panics on double-free of the most recent frame (cheap check),
frames are 4 KiB-aligned by construction.

- **Acceptance:** boot-time self-test: allocate two frames (distinct, aligned,
  zeroed), free the first, allocate again and get it back (LIFO), prints
  `FRAME OK`. Run under `just test expect="FRAME OK"`.

### T1.5 — Sv39 paging with kernel mapped — L
Page-table module: PTE type with flag constants, `map(root, va, pa, flags)`
that walks/creates intermediate tables from the frame allocator. Build the
kernel address space: identity-map `.text` R+X, `.rodata` R, `.data`/`.bss`/
stack/heap-to-be R+W (section boundaries from linker symbols — this is why W^X
needs the linker script's 4 KiB alignment), identity-map the UART page and
virtio-mmio range R+W for later, set A+D on all leaves. Write `satp`,
`sfence.vma`, and *keep executing*.

- **Acceptance:** prints `PAGING OK` from virtual (=identity) addresses after
  the `satp` write; then a deliberate read of an unmapped address (e.g.
  0x9000_0000) produces our loud panic with `scause=13 (load page fault)`,
  `stval=0x90000000`. Both observed in one `just run`.

### T1.6 — Heap allocator — M
Fixed-size heap region (e.g. 1 MiB) built from frames, linked-list free-block
allocator behind `#[global_allocator]`, `extern crate alloc`.

- **Acceptance:** boot self-test: `Box::new(42)`, a `Vec` pushed to 10_000
  elements (forces realloc), a `String`, drop everything, allocate again;
  prints `HEAP OK`. Milestone wrap: final line `M1 FUNDAMENTALS OK`, clean
  shutdown, `just test expect="M1 FUNDAMENTALS OK"` passes.

## Milestone acceptance test

```
$ just test expect="M1 FUNDAMENTALS OK"
```
passes, and the serial log from `just run` contains, in order: `TRAP OK`,
`tick 30`, `FRAME OK`, `PAGING OK`, the demonstration page-fault panic is
**not** present in the final run (it's a per-task check, commented out after
T1.5), `HEAP OK`, `M1 FUNDAMENTALS OK`, clean exit 0.

## Risks and likely failure modes

- **T1.2:** `stvec` low bits are a *mode* field — an unaligned handler address
  silently changes trap mode. A single clobbered register in save/restore
  causes corruption that surfaces far from the trap — diff the frame in GDB.
  Taking a trap before `stvec` is set (or inside the handler) = instant hang.
- **T1.3:** `sie.STIE` vs `sstatus.SIE` confusion (need both); absolute vs.
  delta timer value; interrupt storm if the pending bit is never cleared by
  re-arming.
- **T1.5:** the moment of `satp` write is the project's first cliff — if the
  currently-executing PC isn't mapped X, you get an instruction page fault
  whose handler also isn't mapped: silent hang, debuggable only via QEMU
  `-d int` / `info registers`. Missing A/D bits fault on some QEMU configs.
  Forgetting `sfence.vma`. Mapping sections with wrong granularity because the
  linker script didn't 4K-align them. Stack not mapped W. UART page unmapped
  (only matters if/when we bypass SBI console).
- **T1.6:** alignment handling in `GlobalAlloc` (must honor `Layout.align()`);
  heap region overlapping something else because it was carved by hand instead
  of from the frame allocator.

---

# M2 — Execution  `[TO BE DETAILED at milestone start]`

**Goal:** two or more kernel-defined tasks run in U-mode, invoke the 5 syscalls
(`write`, `exit`, `sbrk`, `gettime`, `yield`) via `ecall`, and are preemptively
scheduled round-robin off the timer interrupt.

**Task list (resolution deferred; expand + get sign-off before any code):**
- Task control block, per-task kernel stack + user stack, task states — M
- `sscratch`-based trap entry rework: distinguish trap-from-U vs trap-from-S,
  swap to the task's kernel stack — L
- First U-mode entry: craft `sstatus.SPP=U`, `sepc`, `sret` into a task — M
- Syscall ABI (numbers in `a7`, args `a0..a5`, return in `a0` — mirror the SBI
  convention we already speak) + dispatch on `ecall`-from-U — M
- Implement `write`, `exit`, `gettime`, `yield` — S each; `sbrk` (per-task
  break, backed by frame allocator + mapping) — M
- Context switch (save/restore callee-saved + `sepc`/`sstatus` per task) — L
- Round-robin scheduler driven by timer tick; idle loop (`wfi`) — M
- Two demo tasks (e.g. two counters `write`-ing at different rates) proving
  preemption without `yield` — S

**Key decisions to record when detailing:** syscall numbering scheme; time
slice length; what `exit` of the last task does (shutdown); whether user pages
are also identity-mapped with U-bit (single address space says yes — but then
S-mode access to U pages needs `sstatus.SUM` — a classic trap to document).

**Known cliffs:** `sret`-to-U with wrong `sstatus`/`sepc` (hangs or bounces
straight back with an illegal-instruction or page fault); forgetting the U bit
on user code pages (instruction page fault at the first user instruction);
kernel touching user buffers without `SUM` set (load/store page fault inside a
syscall).

**Acceptance sketch:** headless run shows interleaved output from 2 tasks with
no `yield` calls in either (proving preemption), correct `gettime` deltas,
`sbrk`-backed buffer use, and clean `exit`-driven shutdown; asserted via
`just test`.

---

# M3 — Unikernel  `[TO BE DETAILED at milestone start]`

**Goal:** the "kernel-defined demo tasks" are replaced by a single application
crate compiled into the image, running as the sole U-mode task; a virtio-net
driver and a minimal network path serve a tiny HTTP response over QEMU user
networking.

**Task list (resolution deferred; expand + get sign-off before any code):**
- App/kernel build integration: `app` as a separate crate/workspace member,
  linked into the image, entry registered as *the* task; tiny `usys` syscall-
  wrapper lib for the app — M
- virtio-mmio discovery: probe 0x1000_1000..0x1000_8000, match device ID 1
  (net), record the modern-vs-legacy device decision — M
- Virtqueue implementation (descriptor table / avail / used rings, memory
  barriers) — L (the hardest single artifact in the project)
- virtio-net device init (feature negotiation, MAC read, RX buffer posting) — M
- TX/RX path; polling first, PLIC external interrupt only if needed (decision
  to record) — M
- Minimal Ethernet/ARP/IPv4 + either hand-rolled minimal TCP or UDP-only demo,
  vs. `smoltcp` — **major decision entry required before code**; scope guard:
  the demo is "curl gets a valid HTTP response with a tiny static body", not
  "a correct TCP stack" — L either way
- Demo workload: HTTP responder in the app over the syscall interface (needs a
  `net`-ish syscall or `write`-multiplexing decision — record it) — M
- QEMU flags: `-netdev user,hostfwd=...` + `-device virtio-net-device`; extend
  `just run`/`just test` — S

**Acceptance sketch:** `just test-net` boots the image, host-side
`curl http://127.0.0.1:<port>/` returns 200 with the expected body, asserted
in-script.

---

# M4 — Evaluation  `[TO BE DETAILED at milestone start]`

**Goal:** scripted, reproducible benchmarks comparing this unikernel against a
minimal Linux VM under identical QEMU conditions — boot time, syscall latency,
memory footprint — plus a technical report.

**Task list (resolution deferred; expand + get sign-off before any code):**
- Define metrics + methodology *first* (what "boot time" means: QEMU start →
  first byte of app output; syscall latency: `gettime`-bracketed hot loops of
  a cheap syscall, median of N; memory: guest-reported + host RSS + image
  size); write it down before measuring — M
- Minimal Linux baseline: prebuilt kernel + busybox/initramfs (buildroot or
  distro kernel + hand-rolled initramfs — decision entry), same QEMU machine,
  cores, RAM, virtio devices — L
- Equivalent Linux-side workloads (init prints marker for boot-time; small
  static binary looping `write`/`clock_gettime` for latency; busybox httpd or
  equivalent for the net demo) — M
- Benchmark harness: scripts that run N trials of each metric on both guests,
  emit raw CSV + summary stats (median, IQR); pinned QEMU version recorded in
  output — M
- Report: architecture overview, methodology, results with tables/plots,
  honest threats-to-validity section (QEMU ≠ hardware, user-net overhead,
  Linux config choices), what I'd do next — L
- End-of-project: final GLOSSARY/DECISIONS pass, interview-prep quiz over the
  whole system — S

**Acceptance sketch:** `just bench` (or `scripts/bench.sh`) runs end-to-end on
a clean machine following SETUP.md, produces `report/results/*.csv` and the
numbers cited in the report are regenerated by it; report reviewed and
finalized.
