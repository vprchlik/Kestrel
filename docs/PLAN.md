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
- M0, M1, and M2 are detailed to individual-task resolution below. **M3–M4 are
  intentionally kept at task-list resolution** and marked
  `[TO BE DETAILED at milestone start]` — the first action at each of those
  milestones is expanding its section to full resolution using what prior
  milestones taught us, and getting sign-off before code.

## Milestone overview

| Milestone | Name | One-line goal | Status |
|---|---|---|---|
| M0 | Boot | OpenSBI → kernel entry → UART "hello" → clean QEMU exit | done |
| M1 | Fundamentals | Traps, SBI timer interrupts, frame allocator, Sv39 paging, heap | done |
| M2 | Execution | U-mode, 5 syscalls, context switch, preemptive scheduling of 2+ tasks | detailed; not started |
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
  whimbrel: hello from hart 0, dtb at 0x<some address in RAM>
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
Make `just test` assert on the kernel marker, not the OpenSBI banner.
Timeout is a hang-guard (~3s), not the normal path. Verdict from serial +
QEMU status together (D-0017): `PANIC` → FAIL; timeout → HANG; marker +
exit 0 → PASS.

- **Acceptance:** `just test` prints `TEST PASS: found "M0 BOOT OK"` and
  exits 0. `just test-panic` prints `TEST FAIL: panic` and the panic line,
  exit 1. `just test-hang` prints `TEST HANG` and exit 2.

## Milestone acceptance test

```
$ just test
```
prints `TEST PASS: found "M0 BOOT OK"` and exits 0. And interactively:
```
$ just run
... OpenSBI banner ...
whimbrel: hello from hart 0, dtb at 0x87e00000
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
  QEMU never exits — the test recipe uses `timeout` as a hang-guard (exit 2),
  but the acceptance requires a real clean exit.

## M0 summary

**Produced:** a `no_std` rv64gc kernel that OpenSBI enters at `0x80200000` in
S-mode, zeros `.bss`, prints over DBCN, panics loudly with a reentrancy
guard, and shuts down via SRST so QEMU exits 0. `just test` is a CI-shaped
gate on that path.

**Acceptance proves:** `just test` finds `M0 BOOT OK` and exits 0; `just run`
prints the hello line (DTB in RAM) plus `M0 BOOT OK` and returns 0. The
harness also distinguishes panic (FAIL) from timeout (HANG).

**Decisions this milestone:** D-0015 DBCN `write_byte` (no legacy putchar);
D-0016 unmapped stack guard page (accepted, deferred to M1/T1.5); D-0017
SRST shutdown, no guest-controlled exit code, harness parses serial.

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

**9. Delegation is not universal — read `medeleg`.** OpenSBI chooses which
exceptions reach S-mode, and the boot log tells us exactly which. Observed on
OpenSBI v1.3 / QEMU `virt`: `MEDELEG = 0xf0b509` delegates exception codes 0
(instruction address misaligned), 3 (breakpoint), 8 (`ecall` from U-mode), 10
(`ecall` from VS-mode, H-extension; bit 10 is set in the observed mask), 12,
13, 15 (the three page faults), plus the H-extension guest-fault codes 20–23.
It does **not** delegate 1 (instruction access fault), 2 (illegal instruction),
4/6 (misaligned load/store), 5/7 (load/store access fault), or 9 (`ecall` from
S-mode — that one is how we call SBI). `MIDELEG = 0x1666` includes bit 5, so
supervisor timer interrupts do reach us. Two consequences: we cannot test our
own handler with an illegal instruction, and a wild pointer into OpenSBI's
PMP-protected RAM produces a *firmware* trap dump rather than ours. Knowing
which codes can possibly arrive is what makes the "unknown trap" panic arm
meaningful instead of decorative.

**10. The CLINT is closed to S-mode.** OpenSBI's PMP setup prints
`Region00: 0x02000000-0x0200ffff M: (I,R,W) S/U: ()`. The timer comparator
`mtimecmp` lives in that range, so S-mode cannot write it: the store raises an
access fault, and access faults are not delegated, so our handler would never
even see the failure. This single hardware fact reduces "how do we arm a
timer" to exactly two options — an SBI call into M-mode, or the Sstc
extension's `stimecmp` CSR (see D-0018).

**11. What makes the instruction after `csrw satp` fetch successfully.**
`satp` is written as `MODE(=8, Sv39) << 60 | ASID(=0) << 44 | PPN(root)`, where
PPN is the root table's physical address shifted right by 12. The write takes
effect for this hart immediately. The **next instruction fetch** — at the
address right after the `csrw`, and since we are identity mapped that is
numerically the same address it was a cycle ago — is no longer a direct
physical access. The hardware walker now performs, for that fetch: read the
root table at `satp.PPN << 12` and index it with virtual address bits [38:30];
if that entry is a pointer, read the level-1 table and index with bits [29:21];
if that entry is a pointer, read the level-0 table and index with bits [20:12];
take the leaf's PPN and concatenate the page offset from bits [11:0].

For that fetch to succeed, **all** of the following must already be true
before the `csrw` retires:

1. `satp.MODE` is 8. If it is 0, translation never turns on and the whole
   thing silently no-ops — you believe paging is live and it is not.
2. `satp.PPN` is the root table's physical address **shifted right 12**, not
   the address itself. Writing the address is a factor-of-4096 error that
   lands the walker on garbage.
3. The root table's entry for the PC's bits [38:30] has V=1.
4. Every intermediate entry on the walk has V=1 and R=W=X=0. A non-leaf with
   any of R/W/X set *is* a leaf; the walk stops early and either resolves to
   the wrong physical page or faults as a misaligned superpage.
5. The leaf has V=1 and X=1.
6. The leaf has U=0. In S-mode, a leaf marked U=1 is inaccessible for
   instruction fetch, and `sstatus.SUM` does not help — SUM affects loads and
   stores only, never fetches. This is a corner that bites people in M2.
7. The leaf has A=1, and D=1 for anything we will write. QEMU may fault rather
   than set these itself, depending on version and configuration, so we set
   them up front on every kernel leaf.
8. The leaf's PPN maps back to the same physical frame, so the bytes fetched
   are the instruction we compiled.
9. The page tables themselves are at physical addresses the walker can read,
   and PMP permits it. The walker uses physical addressing, so the tables do
   **not** need to be mapped for the walk to work — but *we* need them mapped
   to edit them afterwards, which identity mapping gives us free.
10. The stack is mapped R+W before the next prologue, and `ra` points into
    mapped X memory before the next `ret`.
11. `stvec`'s target page is mapped X and the handler's stack is mapped W —
    because if any of 1–10 is wrong, the resulting page fault vectors there,
    and if that page is also unmapped the fault faults, forever, with no
    output. That is the silent hang.
12. **No interrupt arrives during the transition.** If `sstatus.SIE` is set and
    the timer fires between the `csrw` and the `sfence.vma`, we take a trap
    through a mapping we have not yet validated. Since T1.3 turns on 10 ms
    ticks before T1.7 runs, this window is not hypothetical (see D-0022).

Then `sfence.vma` with both operands zero, flushing everything. QEMU flushes on
a `satp` write in practice, but the specification does not promise it, and a
kernel that depends on unpromised behavior is a kernel that works until it does
not.

This enumeration is also the strongest available defense of D-0006: identity
mapping means the PC, the stack pointer, and every return address keep their
numeric values across the switch. A higher-half kernel has to map both the old
and new views, jump, then drop the old one. That trampoline is why xv6 and
Linux look the way they do here, and being able to say why we did not need one
is a good interview answer.

**12. Instruction width and `sepc`.** For an exception we resume *past*
(`ebreak` in M1, `ecall` in M2), the handler must add the trapped
instruction's width, because `sepc` points *at* the offending instruction and
`sret` would re-execute it forever. Width comes from the low two bits of the
instruction halfword at `sepc`: `0b11` means 4 bytes, anything else means 2
(the C extension). For an **interrupt**, do not touch `sepc` — it already
points at the instruction that has not run yet, and advancing it skips an
instruction, silently, until the consequence is inexplicable. Two rules, one
CSR; conflating them is a classic bug (see D-0021).

## Kernel address space — the M1 target

Single root table, identity mapped (VA = PA), one address space for the
lifetime of the system (D-0006). Addresses below are after T1.5 inserts the
guard page, which shifts everything above `.bss` up by one page.

| Region | Range | Permissions | Why |
|---|---|---|---|
| OpenSBI firmware | `[RAM_START, __kernel_start)` | **not mapped** | PMP already denies S-mode. Unmapped means a stray access is a page fault we decode, not an access fault firmware absorbs. |
| Kernel `.text` | `[__kernel_start, __rodata_start)` | R + X | Executable, never writable. |
| `.rodata` | `[__rodata_start, __data_start)` | R | No X, so a jump into a string constant faults. |
| `.data` + `.bss` | `[__data_start, __bss_end)` | R + W | No X. |
| Stack guard | `[__bss_end, __boot_stack_bottom)` | **not mapped** | D-0016. Overflow becomes a store page fault instead of silent `.bss` corruption. |
| Boot stack | `[__boot_stack_bottom, __boot_stack_top)` | R + W | 64 KiB, grows down into the guard. |
| Heap | `[__heap_start, __heap_end)` | R + W | Carved before the free list (D-0024). |
| Free frames | `[__heap_end, RAM_END)` | R + W | Page tables and, later, task stacks come from here (D-0019). |
| MMIO (UART, virtio, sifive_test) | — | **not mapped** | D-0025. Console is an `ecall`; virtio is M3. |

Numeric bounds come from the linker and `frame::RAM_END`, not from literals in this table. T1.6 maps by those symbols. After T1.5: `__kernel_start = 0x8020_0000`, `__boot_stack_bottom = __bss_end + 0x1000`, `__heap_end = __heap_start + 1 MiB`, `RAM_END = 0x8800_0000`.

Every leaf carries A+D. Nothing outside these ranges is mapped at all, which is
what makes both T1.7 fault probes meaningful: `0x9000_0000` is past the end of
RAM, and the guard page is a hole inside it.

## Tasks

Ten tasks. T1.0 is harness plumbing every later acceptance line depends on.
T1.6 and T1.7 are deliberately split so the page tables are *validated in
software* before they are activated — the split exists because activation is
the one step in this project whose failure mode is a silent hang with no
output.

### T1.0 — Restore `expect` parameterization in the harness — S
T0.6 replaced the old `just test expect=…` interface with
`scripts/boot-test.sh` reading an `EXPECT` environment variable, so every
per-task acceptance line below needs a one-command form. Add an `expect`
parameter (and a `timeout_s` parameter) back to the `test` recipe, passing
through to the script's environment.

- **Acceptance:** `just test expect="M0 BOOT OK"` prints
  `TEST PASS: found "M0 BOOT OK"` and exits 0; `just test-panic` still exits 1
  and `just test-hang` still exits 2.

### T1.1 — CSR access module — S
`csr` module: read/write/set/clear helpers for `sstatus`, `sie`, `sip`,
`stvec`, `scause`, `sepc`, `stval`, `satp`, and `time` (macro or per-CSR
functions — keep it boring), plus named bit constants with spec citations.

- **Acceptance:** boot prints `sstatus`, `sie`, `stvec`, `satp` as hex;
  `satp` reads 0 (Bare mode, paging not yet on) and `stvec` reads whatever
  OpenSBI left. `just test expect="CSR OK"` passes.

### T1.2 — Trap entry, frame, and dispatch — L
Assembly `__trap_entry`: reserve 272 bytes on the current stack, save all 31
GPRs plus `sepc` and `sstatus` at register-indexed offsets, pass the frame
pointer to Rust `trap_handler(&mut TrapFrame)`, restore, `sret`. `stvec` set
to it in Direct mode (4-byte alignment is mandatory — the low two bits are the
mode field). Layout and the four M2-proofing constraints per D-0020; `sepc`
advance per D-0021. Rust dispatch on `scause`: known causes handled,
**everything unknown panics printing decoded `scause`, `sepc`, and `stval`**
per the fail-loudly rule.

- **Acceptance:** a deliberate `unsafe { asm!("ebreak") }` in `kmain` prints a
  trap report with `scause=3 (breakpoint)` and `sepc` equal to the `ebreak`
  address, then execution *continues* past it and prints `TRAP OK`.
  `just test expect="TRAP OK"` passes. Second check, now that `stvec` is
  installed: `just panic` still prints its `PANIC at …` line (a panic arriving
  from a trap context is a different situation than one from `kmain`).

### T1.3 — Timer interrupts via SBI TIME — M
Probe the TIME extension via BASE (same shape as the DBCN and SRST probes),
enable `sie.STIE` and `sstatus.SIE`, arm the first deadline, and on each
supervisor-timer interrupt increment a tick counter and re-arm at
`rdtime() + 100_000` (10 ms at 10 MHz). Print a line every 10 ticks. Keep the
arm in a single function so the M4 Sstc comparison is a one-site change
(D-0018).

- **Acceptance:** `just test expect="tick 30"` passes — 30 ticks is 0.3 s,
  comfortably inside the 3 s hang-guard. Serial shows `tick 10`, `tick 20`,
  `tick 30`.

### T1.4 — Physical frame allocator — M
Intrusive free-list allocator over `[heap_end, RAM_END)`, where the heap region
is carved first (D-0024) and `RAM_END` is the hardcoded `0x8800_0000` validated
per D-0023. Each free frame stores its successor in its own first 8 bytes, so
total metadata is one head pointer in `.bss`. `alloc_frame()` returns a zeroed
4 KiB-aligned frame; `free_frame()` pushes it back. Panics on exhaustion with
the frame count; panics on the cheap double-free check (freeing the current
head).

- **Acceptance:** boot self-test allocates two frames (distinct, aligned,
  zeroed), frees the first, reallocates and gets it back (LIFO); prints the
  total frame count and `FRAME OK`. `just test expect="FRAME OK"` passes.

### T1.5 — Linker script: guard-page hole and heap symbols — S
Insert an unmapped 4 KiB hole between `__bss_end` and `__boot_stack_bottom`
(implements D-0016 — today they are the same address, so overflow walks
straight into `.bss`), and export heap-region symbols for T1.8. Everything
above shifts up by one page.

- **Acceptance:** `nm` shows `__boot_stack_bottom == __bss_end + 0x1000`; the
  kernel still boots and `just test expect="M0 BOOT OK"` passes.

### T1.6 — Build the page tables *without* activating them — M
Page-table module: PTE type with flag constants, and `map(root, va, pa, flags)`
walking and creating intermediate tables from the frame allocator. Build the
kernel address space per D-0019, D-0025, and D-0026: `.text` R+X, `.rodata` R,
`.data`/`.bss` R+W, guard page absent, stack R+W, heap R+W, all remaining RAM
R+W, OpenSBI's region and all MMIO unmapped, A+D set on every leaf, 4 KiB
leaves only. Then walk the finished tables **in software** (raw PTE decode by
bit position — not the mapper's helpers) and print the resolved translation
and permissions for a list of probes: kernel entry, a `.text` address, a
`.rodata` address, a stack address, a heap address, the guard page, and
`0x9000_0000`. Print the root PA and the `satp` value we would write. Nothing
touches `satp`.

- **Acceptance:** the printed walk matches expected permissions for every
  probe, the guard page and `0x9000_0000` both resolve to "unmapped", and
  `just test expect="PAGETABLE OK"` passes. This is the task that makes T1.7
  survivable: you verify the map is right before betting the machine on it.

### T1.7 — Activate paging — L
Clear `sstatus.SIE` (D-0022 — timer ticks have been live since T1.3), write
`satp`, `sfence.vma`, restore `SIE`, and keep executing. Prerequisite concept
11 is the checklist for this task; DEBUGGING.md §4 has the first-response
procedure when it hangs.

- **Acceptance:** prints `PAGING OK` after the switch; a deliberate read of
  `0x9000_0000` panics with `scause=13 (load page fault)`,
  `stval=0x90000000`; a deliberate write into the guard page panics with
  `scause=15 (store page fault)` and `stval` inside the guard. All three
  observed in one `just run`. The two fault probes are then removed and
  `just test expect="PAGING OK"` passes.

### T1.8 — Heap allocator — M
Linked-list free-block allocator over the reserved heap region behind
`#[global_allocator]`, `extern crate alloc`. Honors `Layout::align()`. Panics
on exhaustion with the requested size and alignment.

- **Acceptance:** boot self-test: `Box::new(42)`, a `Vec` grown to 10_000
  elements (forces realloc), a `String`, drop everything, allocate again;
  prints `HEAP OK`. `just test expect="HEAP OK"` passes.

### T1.9 — Milestone wrap — S
Print the final marker, update the harness default marker to
`M1 FUNDAMENTALS OK`, update GLOSSARY and DECISIONS, and run the five-question
quiz.

- **Acceptance:** `just test` (no arguments) passes on the new default marker.

## Milestone acceptance test

```
$ just test
```
prints `TEST PASS: found "M1 FUNDAMENTALS OK"` and exits 0, and the serial log
from `just run` contains, in order: `CSR OK`, `TRAP OK`, `tick 10`, `tick 20`,
`tick 30`, `FRAME OK`, `PAGETABLE OK`, `PAGING OK`, `HEAP OK`,
`M1 FUNDAMENTALS OK`, then a clean exit 0. The deliberate page-fault probes
from T1.6 and T1.7 are per-task checks and are **not** present in the final
run.

## Risks and likely failure modes

- **T1.2:** `stvec`'s low two bits are a *mode* field, so an unaligned handler
  address silently changes trap mode instead of being rejected. A single
  clobbered register in save/restore causes corruption that surfaces far from
  the trap — diff the frame in GDB rather than reading the assembly again.
  Taking a trap before `stvec` is set, or inside the handler, is an instant
  hang. Note also that codes 1, 2, 4, 5, 6, and 7 are **not delegated** on
  this platform (prerequisite concept 9): if you are hunting a fault and our
  handler is silent while OpenSBI prints a dump, that is why — not a bug in
  our dispatch.
- **T1.3:** `sie.STIE` and `sstatus.SIE` are both required and are easy to
  confuse. The SBI timer argument is an **absolute** counter value, not a
  delta. `sip.STIP` is not write-clearable from S-mode — re-arming *is* the
  acknowledgement, so "forgot to re-arm" presents as either one interrupt ever
  or an interrupt storm depending on which half you got wrong.
- **T1.4:** the free list lives *inside* the frames it manages, so it depends
  on all of RAM being addressable (D-0019). The DTB at `0x87e0_0000` sits
  inside the range handed to the allocator and will eventually be clobbered —
  which is fine, but only if the D-0023 sanity check runs *before* allocator
  init.
- **T1.5:** the guard page shifts the stack and `__kernel_end`; anything that
  hardcoded a stack address needs to follow.
- **T1.7:** the project's first cliff. If the currently-executing PC is not
  mapped X you take an instruction page fault whose handler is also unmapped:
  a tight trap loop with no output, debuggable only via QEMU `-d int` and the
  monitor. Missing A/D bits fault on some QEMU configurations. Forgetting
  `sfence.vma` works until it does not. Wrong `satp.PPN` shift is a
  factor-of-4096 error. A timer interrupt inside the transition window
  vectors through a mapping that has not been validated (D-0022).
- **T1.8:** `GlobalAlloc` must honor `Layout::align()`, not just size. The
  heap region must come from the reserved carve-out rather than being placed
  by hand, or it will overlap the frame allocator's range.

## M1 summary

**Produced:** an S-mode kernel that takes delegated traps (Direct `stvec`,
register-indexed `TrapFrame`), arms 10 ms ticks through SBI TIME, owns RAM
as 4 KiB frames above a 1 MiB heap carve-out, identity-maps that RAM in Sv39
with W^X and an unmapped stack-guard hole, and serves `Box`/`Vec`/`String`
from a coalescing first-fit heap. Paging is validated in software (T1.6)
before `satp` is written (T1.7). Page-fault probes and the T1.6 walk table
are per-task checks and are not in the final image.

**Acceptance proves:** `just test` finds `M1 FUNDAMENTALS OK` and exits 0.
`just run` prints, in order, `CSR OK`, `TRAP OK`, `tick 10`, `tick 20`,
`tick 30`, `FRAME OK`, `PAGETABLE OK`, `PAGING OK`, `HEAP OK`,
`M1 FUNDAMENTALS OK`, then QEMU exits 0. `just test-panic` / `just test-hang`
still distinguish FAIL from HANG.

**Decisions this milestone:** D-0018 SBI TIME not Sstc; D-0019 map all of
RAM R+W, intrusive frame list; D-0020 `TrapFrame` / Direct `stvec`; D-0021
instruction width from the trapped bits, never on interrupts; D-0022 clear
`sstatus.SIE` across `satp`; D-0023 hardcoded `RAM_END`, DTB header check
then clobber; D-0024 1 MiB heap carve-out before the free list; D-0025 no
MMIO in M1; D-0026 4 KiB leaves only, no superpages; D-0027 address-sorted
heap free list, coalesce on free, first-fit.

---

# M2 — Execution

**Goal:** two or more kernel-defined tasks run in U-mode, invoke the 5 syscalls
(`write`, `exit`, `sbrk`, `gettime`, `yield`) via `ecall`, and are preemptively
scheduled round-robin off the timer interrupt.

**Decisions recorded before any code:** D-0029 `sscratch` protocol; D-0030
static per-task stacks with guard holes; D-0031 separate user sections, no
PTE edits after activation; D-0032 switch at trap exit, the trap frame *is*
the task context; D-0033 syscall ABI; D-0034 user-pointer validation and the
`SUM` window; D-0035 slice = one tick, no idle loop; D-0036 resolution of
D-0028.

## Prerequisite concepts

**1. There is no "enter user mode" instruction.** Privilege only drops via
`sret` (or `mret`). `sret` sets the privilege level from `sstatus.SPP`, sets
`sstatus.SIE` from `SPIE`, sets `SPP` back to U, and jumps to `sepc`. Every
one of those inputs is a register we control, which means entering U-mode for
the first time is *returning from a trap that never happened*: the kernel
fabricates the state a trap would have left behind (`sepc` = the task's entry
point, `SPP` = 0, a user `sp`) and executes `sret`. There is no separate
mechanism, and this is why the same assembly that returns from a syscall also
starts a brand-new task.

**2. Interrupts for higher privilege levels are always enabled.** The
privileged spec's global-enable rule is asymmetric: while executing at level
*x*, interrupts for levels *y > x* are always globally enabled regardless of
that level's `yIE` bit, and interrupts for levels *w < x* are always globally
disabled. Concretely: in U-mode, S-mode interrupts fire whether or not
`sstatus.SIE` is set (only `sie.STIE` and pending state matter), so user code
is *always* preemptible; in S-mode, `sstatus.SIE` gates them, and hardware
cleared it on trap entry and we never set it (D-0020), so kernel code is
*never* preempted. That single asymmetry is what makes M2's scheduler small:
there is exactly one point in the system where a task can lose the CPU.

**3. A pending timer during a syscall is deferred, not lost.** `sip.STIP` is
not write-clearable from S-mode; re-arming is the acknowledgement (D-0018). If
the deadline passes while a syscall is running, `STIP` stays set, `SIE=0`
suppresses the trap, and the interrupt is taken *immediately* after the `sret`
back to U-mode, because U-mode cannot mask S-mode interrupts (concept 2).
Expect preemption to land right after a syscall returns. Nothing is lost, and
the arm-at-`rdtime()+PERIOD` rule (D-0018) means a long syscall cannot leave
the comparator in the past.

**4. The `U` bit, and what `sstatus.SUM` does and does not buy.** A leaf PTE's
`U` bit says the page is reachable from U-mode. In S-mode, a load or store to
a `U=1` page raises a page fault *unless* `sstatus.SUM` is set; an
**instruction fetch** from a `U=1` page faults in S-mode always, `SUM` or not
(`SUM` covers loads and stores only; `MXR` only makes execute-only pages
readable). Two consequences shape the whole milestone. First, user code cannot
live in kernel `.text`: one page cannot be both S-fetchable and U-fetchable, so
user code needs its own sections. Second, the kernel cannot read a user buffer
without `SUM`, and with paging on there is no physical back door — every S-mode
load goes through the same translation and the same `U` check.

**5. Hardware does not validate pointers the kernel dereferences.** The `U`
bit protects *user → kernel*: a U-mode access to a `U=0` page faults with no
software involvement. It says nothing about *kernel-on-behalf-of-user*. With
`SUM=1` the kernel may read `U=1` pages, and it could always read `U=0` pages,
so a copy loop will faithfully read kernel `.bss` if the task passes that
address. In a single identity-mapped address space (D-0006) there is no
hardware check to switch on: validating a user pointer is software or it does
not happen.

**6. `sscratch` exists because every register belongs to the interrupted
context.** On a trap from U-mode all 31 GPRs still hold user values, `sp`
included. Pushing a trap frame at the trapped `sp` would let a task point `sp`
at kernel memory and have the kernel spill 272 bytes into it — permitted,
because S-mode stores to `U=0` pages are legal. `sscratch` is the one
architectural scratch slot the trap handler owns, and swapping it with `sp` is
the only way to obtain a trustworthy stack pointer without first having a free
register to compute one. The corollary is that the *discrimination* between a
trap from U and a trap from S must also ride on that swap: reading
`sstatus.SPP` needs a destination register, and at the first instruction of
the handler there is none.

**7. A task's whole context is the trap frame.** With no floating-point state
in scope (D-0002 defers FPU context switching; `sstatus.FS` stays `Off`), a
task's complete user context is 31 GPRs plus `sepc` and `sstatus` — exactly the
`TrapFrame` D-0020 already builds. Nothing else needs saving. So the task
control block stores no register state at all, only *where* its frame lives,
and a context switch is a change of which frame the trap epilogue restores.

**8. `gp` and `tp` are ABI registers with kernel meaning.** `_start` loads `gp`
with relaxation disabled precisely so the linker may relax kernel absolute
loads into cheaper `gp`-relative ones; kernel Rust code therefore *depends* on
`gp`. A trap from U-mode arrives with the user's `gp` still in the register, so
the entry must restore the kernel's before calling Rust or every relaxed static
access reads the wrong address — a bug that surfaces far from its cause. `tp`
is the thread pointer, used only for thread-locals, which `no_std` without TLS
never emits: the kernel never reads it, so it is saved and restored like any
other GPR and needs no reload.

**9. `ecall` has no compressed encoding.** RVC defines `c.ebreak` but no
`c.ecall`, so the syscall path advances `sepc` by the constant 4 with that
citation and never reads user memory to decide. This is D-0021 constraint 3
paying off: the alternative — decoding width from the instruction at `sepc` —
is a *load* from a user virtual address, which needs `SUM` in the hottest path
in the kernel and a fault story for a task that jumped somewhere unmapped.

**10. Kernel stack overflow is not a recoverable fault.** Once each task has
its own kernel stack, an overflow must be caught by an unmapped guard page or
it silently corrupts a neighbour. But trace what the guard actually produces:
the store page fault arrives from S-mode, so `sscratch` is 0, so trap entry
keeps the faulting `sp` — already inside the guard hole — and immediately
pushes 272 bytes through it, which faults again, forever, with no output. Rust
is never reached, so the panic printer and its `IN_PANIC` guard never run. The
guard converts silent neighbour corruption into a silent hang. That is a real
improvement (the damage stops) but it is not a diagnostic, and M2 does not fix
it (D-0030).

## Kernel + user address space — the M2 target

Every new region is **static and linker-placed**, inserted between the boot
stack and `__kernel_end` so the 1 MiB kernel-heap carve-out (D-0024) and the
frame free list keep their existing structure and simply start higher.
`MAX_TASKS` is a compile-time constant (4; the demo uses 2).

| Region | Permissions | Why |
|---|---|---|
| `.utext` | R + X + **U** | User code cannot live in kernel `.text`: S-mode cannot fetch from a `U=1` page and U-mode cannot fetch from a `U=0` one (concept 4). |
| `.urodata` | R + **U** | Literals a task passes to `write` must be user-readable. A literal left in kernel `.rodata` faults on the task's own load. |
| `.udata` / `.ubss` | R + W + **U** | User statics. `.ubss` is NOLOAD. |
| Per-task user stack ×`MAX_TASKS` | R + W + **U** | 8 KiB each, NOLOAD, 4 KiB unmapped guard hole below each. |
| Per-task break window ×`MAX_TASKS` | R + W + **U** | 64 KiB each, NOLOAD. `sbrk` moves a pointer inside this; it never allocates or maps (D-0036). |
| Per-task kernel stack ×`MAX_TASKS` | R + W, **U=0** | 8 KiB each, NOLOAD, 4 KiB unmapped guard hole below each. `sscratch` holds the top (D-0029). |

Everything else is the M1 map unchanged. **No PTE is edited after
`page::activate`** (D-0031): the user map is built at boot beside the kernel
map, so M2 adds no `sfence.vma` site and no mapping work in the trap path.

## Tasks

Thirteen tasks. T2.2 is the T1.6-shaped safety net for T2.3 — verify the
`U` bits in software before betting the machine on the first `sret` to U.
T2.3 and T2.9 are the L tasks.

### T2.0 — `sscratch` accessor and boot-context assertions — S
Add `csr::sscratch`. Extend the boot CSR snapshot to print `sscratch` as
OpenSBI left it, then zero it in `trap::install()` **before** `stvec` is
written: a trap taken while `sscratch` holds firmware garbage would be
misread as a trap from U-mode and would push a frame at that address.

- **Acceptance:** boot prints `sscratch` in the CSR block and `sscratch 0`
  after install; `just test expect="CSR OK"` passes.

### T2.1 — Linker script: user sections, per-task stacks, guard holes — M
The regions in the table above, with per-task-index symbols. `MAX_TASKS` is
mirrored in Rust with a `const _: () = assert!` against the linker-derived
region count so the two cannot drift.

- **Acceptance:** `nm` shows each guard hole exactly 4 KiB, each stack 8 KiB,
  each break window 64 KiB, every user region 4 KiB-aligned, and
  `__heap_start` still immediately above `__kernel_end`; the kernel still
  boots to `M1 FUNDAMENTALS OK`.

### T2.2 — Map the user address space; verify `U` bits in software — M
Extend `page::build` with `LEAF_URX` / `LEAF_UR` / `LEAF_URW`. Today's
`flags_match` requires `U=0` on every probe; make the expected `U` bit a
per-probe field and probe every new region plus every guard hole.

- **Acceptance:** the printed walk shows `U=1` on user regions, `U=0` on
  kernel regions, and unmapped for every guard hole;
  `just test expect="PAGETABLE OK"` passes.

### T2.3 — Trap entry/exit rework — L
Fill D-0020 block 1 with the `sscratch` swap and the branch-on-zero
discrimination (D-0029); reload the kernel's `gp` on the U path; factor block 4
into a `__trap_return` symbol shared by the epilogue and the first U-mode
entry; change `trap_handler` to *return* the frame to resume (D-0032), which
costs block 4 one `mv sp, a0`.

- **Acceptance:** the whole existing suite still passes unchanged — every M1
  trap is a trap-from-S, so `just test`, `just test-panic`, `just test-hang`,
  and `just test-stress` exercise the S path and the `sscratch`-stays-zero
  invariant. The deliberate `ebreak` still reports and continues (`TRAP OK`).

### T2.4 — Task control block and the static task table — M
`Task { id, state: {Ready, Running, Exited}, frame, kstack_top, ustack_top,
brk_base, brk, brk_wall, exit_code }` in a static array. `create(id, entry)`
fabricates the initial frame (`sepc` = entry, `SPP`=0, `SPIE`=1,
`x[2]` = `ustack_top`, `gp` = `tp` = 0 per D-0032). No allocation anywhere in
this path.

- **Acceptance:** boot prints the table — ids, stack tops, break windows — and
  asserts `frame == kstack_top - 272` for every task.

### T2.5 — First U-mode entry — M
`sret` into task 0, whose body immediately `ecall`s back; the dispatcher
answers "not implemented" for now. Prints `USER OK`.

- **Acceptance:** `just test expect="USER OK"` passes. Separately,
  `just objdump '-d --section=.utext'` shows no call leaving `.utext` and no
  `gp`- or `tp`-relative access: no compiler-builtin `memcpy`, no kernel
  `.rodata` literal, no TLS.

### T2.6 — Syscall ABI and `ecall`-from-U dispatch — M
Dispatch on `EXC_ECALL_U` per D-0033: number in `a7`, arguments `a0`–`a5`,
return pair `a0` = error / `a1` = value, written **into the frame**.
`sepc += 4` with the no-`c.ecall` citation. An unknown number kills the task
(D-0034) rather than panicking the kernel. Prints `SYSCALL OK`.

- **Acceptance:** `just test expect="SYSCALL OK"` passes; a task calling
  number 99 is killed with a printed diagnostic and the system stays up.

### T2.7 — User-pointer validation and `SUM`-windowed copies — M
`user_range_ok(task, ptr, len)` against that task's static intervals
(overflow-checked, containment in user stack / live break / `.udata`+`.ubss` /
`.urodata` for read-only sources). `copy_from_user` / `copy_to_user` raise
`SUM` only around an already-validated `memcpy`, with a 4 KiB per-call cap.

- **Acceptance:** a task passing a kernel address to `write` gets an error
  return and is killed; the kernel neither faults nor panics. `SUM` is clear
  again on the next trap (asserted at entry to the dispatcher).

### T2.8 — The five syscalls — M (S each)
`write(ptr, len) -> count` (console only, no `fd`), `exit(code)`,
`sbrk(delta) -> old_break`, `gettime() -> raw counter`, `yield()`. Semantics
and error codes per D-0033. A task grows its break and uses the memory;
prints `SBRK OK`.

- **Acceptance:** `just test expect="SBRK OK"` passes; `sbrk` past the wall
  returns `NO_MEM` and leaves the break unchanged; `gettime` deltas across a
  `write` are positive and smaller than a tick period.

### T2.9 — Round-robin scheduler at trap exit — L
The timer handler ends the slice (slice = one tick, D-0035) and returns the
next `Ready` task's frame. `yield` does the same immediately. No idle loop:
with no blocking states the ready set is empty only when every task has
exited, which shuts down.

The demo tasks make the test deterministic rather than timing-dependent: each
spins on `gettime` until **its own** observed counter has advanced by
`2 × PERIOD` (20 ms at 10 MHz), printing a progress line every 5 ms, then
exits. Because `rdtime` is wall-clock at a fixed 10 MHz, a task that refuses
to exit before 20 ms of counter advance is preempted at least twice at a 10 ms
tick on any host, and a slower host produces *more* switches, never fewer.

The kernel asserts the property itself and panics with the counts if it fails:
both tasks `Exited`, `yields == 0` for both, and at least one switch in each
direction. Then it prints, before `SCHED OK`:

```
task 1 done writes=4 yields=0
task 2 done writes=4 yields=0
sched switches 1->2=3 2->1=2 yields=0
```

- **Acceptance:** `just test expect="SCHED OK"` passes, and the recipe also
  greps the serial log for all four of:
  `task 1 done writes=[0-9]* yields=0`, `task 2 done writes=[0-9]* yields=0`,
  `sched switches 1->2=[1-9]`, `2->1=[1-9]`; plus an `awk` check that the
  first `^task 2 ` line precedes the `^task 1 done` line. Exact interleaving
  is never asserted.

### T2.10 — Contained user faults — M
A U-mode page fault, misaligned instruction address, or bad syscall kills the
task with `task N killed: <cause> sepc=… stval=…` and reschedules. New
`user-fault-selftest` feature and `just test-user-fault`, in the same shape as
`panic-selftest` and `frame-exhaust-selftest`.

- **Acceptance:** `just test-user-fault` shows the faulting task killed, the
  other task continuing to completion, and a clean shutdown — the kernel does
  **not** panic and the recipe expects exit 0, not the inverted status the
  panic recipes use.

### T2.11 — Freeze the frame allocator — S
`frame::freeze()` immediately before the first `sret` to U (D-0036). After it,
`alloc_frame` / `free_frame` panic printing the request. Enforces by assertion
what T2.1/T2.8 arrange by construction: nothing in the trap path allocates.

- **Acceptance:** boot prints `frames frozen: free=N`; a deliberate
  `alloc_frame` after freeze panics with that message. `just test-stress`
  still passes (the storm runs before the freeze).

### T2.12 — Two demo tasks and milestone wrap — M
Two counters writing at different rates, one of them using `sbrk`, both
exiting; the last `exit` shuts down. Marker `M2 EXECUTION OK`, harness default
updated, GLOSSARY and DECISIONS updated, five-question quiz.

- **Acceptance:** `just test` (no arguments) passes on the new default marker.

## Milestone acceptance test

```
$ just test
```
prints `TEST PASS: found "M2 EXECUTION OK"` and exits 0, and the serial log
from `just run` contains, in order: the M1 markers, `frames frozen`,
`USER OK`, `SYSCALL OK`, `SBRK OK`, the two tasks' progress lines, both
`task N done … yields=0` lines, the `sched switches` line, `SCHED OK`, two
`task N exit 0` lines, `M2 EXECUTION OK`, then a clean exit 0.
`just test-panic`, `just test-hang`, `just test-stress`, and the new
`just test-user-fault` all still hold their verdicts.

## Risks and likely failure modes

- **T2.3, `gp` clobber.** A trap from U arrives with the user's `gp`; relaxed
  kernel static accesses inside the handler then read the wrong addresses.
  Symptom is impossible static values far from the cause, not a fault.
- **T2.3, `sscratch` nonzero while in S-mode.** Any window where `sscratch`
  is nonzero during S-mode execution turns a kernel exception into a
  frame-clobbering mess. D-0029 shrinks that window to the single `csrrw`
  immediately before `sret`, which touches no memory and cannot fault.
- **T2.5, wrong side of the `U` bit.** User code in a `U=0` page is an
  instruction page fault at the task's first instruction (cause 12,
  `sepc` = the entry point). The same mistake reaches further than expected:
  a string literal left in kernel `.rodata` faults on the task's *load*, and a
  compiler-emitted `memcpy` call into kernel `.text` faults on the *fetch*.
- **T2.6, `sepc` not advanced before a switch.** If a syscall reschedules and
  the advance happens after, the task re-executes its `ecall` on resume — an
  infinite syscall loop that presents as a hang.
- **T2.7, `SUM` left set.** A `SUM` window spanning anything but the validated
  `memcpy` is ambient authority over user memory inside kernel code. Raise it
  after validation, drop it before any formatting or dispatch.
- **`sstatus.FS` is likely `Off`.** An FP instruction from U-mode is then an
  illegal instruction, and code 2 is **not delegated** on this platform
  (M1 concept 9): OpenSBI dumps it and our handler never sees it. Demo tasks
  stay integer-only; check the boot `sstatus` snapshot before blaming the
  scheduler.
- **Kernel stack overflow is a silent hang, not a fault report.** 8 KiB per
  task, a debug build, and `println!` formatting on the nested-panic path.
  See concept 10 and D-0030; the signature is in DEBUGGING.md §4.
- **A pending timer during a syscall is not a lost tick** (concept 3).
  Preemption lands right after the `sret`; that is the design, not a bug.

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
