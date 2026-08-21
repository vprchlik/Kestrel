# DEBUGGING — rv64 / QEMU field guide

Bare-metal debugging has exactly three information channels: the serial
console, the GDB stub, and QEMU's own logging/monitor. This file is the map.
When a bug costs more than 30 minutes, add its symptom → cause here afterward.

## 1. GDB over QEMU

QEMU embeds a GDB server. `-s` opens it on `tcp::1234`; `-S` freezes the CPU
at power-on so you can set breakpoints before the first instruction.

```
# terminal 1 — QEMU frozen at reset, stub listening
just debug

# terminal 2 — gdb-multiarch, already pointed at the kernel ELF + stub
just gdb
```

(Or in the editor: run `just debug`, then F5 — see `.vscode/launch.json`.
CodeLLDB works for source-level stepping; drop to `gdb-multiarch` when you
need CSRs or disassembly-level truth. GDB reads CSRs on QEMU; LLDB mostly
doesn't.)

Bread-and-butter commands:

```
(gdb) break kmain            # symbol breakpoints work normally
(gdb) hbreak *0x80200000     # break on the very first kernel instruction
(gdb) continue
(gdb) info registers                     # all GPRs + pc
(gdb) info registers scause sepc stval sstatus satp   # CSRs — the trap story
(gdb) x/8i $pc               # disassemble at pc — what is it *actually* running?
(gdb) x/4gx 0x80200000       # read memory as 8-byte hex words
(gdb) stepi / nexti          # one instruction at a time (asm-level)
(gdb) backtrace              # works once sp is sane and frame pointers exist
(gdb) load                   # re-download the kernel after rebuild (with -S)
```

Debug builds merge `-C force-frame-pointers=yes` via
`scripts/cargo-debug.sh` (`just build` / `just debug`). Release does
not (finding 14); GDB on a measured image will not have frame pointers.

Notes:
- QEMU's stub implements software breakpoints internally — `break` works even
  in ROM-like conditions; reach for `hbreak` if a breakpoint mysteriously
  doesn't fire (e.g. across the M1 `satp` switch).
- If `continue` "hangs", the guest is probably in a `wfi` or a trap loop —
  hit Ctrl-C in gdb and look at `$pc` and `scause`. That *is* information.
- After enabling paging (identity map), symbols and addresses still match;
  if you ever see gdb showing nonsense source for a sane `$pc`, you're
  probably executing a stale binary — rebuild, `load`, or restart `just debug`.

## 2. QEMU trap/interrupt logging: `-d int`

When there's no output and GDB feels too slow, make QEMU narrate every trap:

```
qemu-system-riscv64 -machine virt -nographic -bios default \
    -kernel target/riscv64gc-unknown-none-elf/debug/whimbrel \
    -d int,cpu_reset,guest_errors -D qemu.log
```

(`just run '-d int,cpu_reset,guest_errors -D qemu.log'` does the same.)

Each trap logs a block like:

```
riscv_cpu_do_interrupt: hart:0, async:0, cause:000000000000000f,
    epc:0x0000000080200a4c, tval:0x0000000090000000, desc=store_page_fault
```

Read: `async:0` = exception (1 = interrupt), `cause`/`desc` = what happened,
`epc` = the faulting instruction, `tval` = the address involved. Caveats:

- **It's noisy.** Every SBI console byte is an `ecall` (`desc=supervisor_ecall`
  → M-mode and back). Filter: `grep -v supervisor_ecall qemu.log`.
- `guest_errors` is gold for MMIO typos and unmapped *physical* addresses
  that otherwise fail silently or as store faults. It is **not** where
  the virtio device model reports a broken ring — that is QEMU stderr
  (`Looped descriptor`, `in_num`/`out_num`). See M3 ladder item 1.
- A rapidly repeating identical trap block = trap loop (see §4).
- `-d in_asm` additionally logs every translated code block — extreme but
  definitive when you doubt the CPU is reaching your code at all.

## 3. Reading `scause` / `sepc` / `stval` after a trap

The hardware answers three questions on every trap. Learn to read them raw —
from our panic printout, from gdb, or from `-d int`, it's the same three:

| CSR | Question it answers |
|---|---|
| `scause` | *Why?* Top bit set = interrupt, clear = exception. Low bits = code. |
| `sepc` | *Where?* PC of the interrupted/faulting instruction. |
| `stval` | *What exactly?* Faulting address for memory faults; the instruction bits for illegal instruction; 0 when N/A. |

**Exception codes (`scause` interrupt bit = 0):**

| code | meaning | typical cause here |
|---|---|---|
| 0 | instruction address misaligned | jump to odd address (corrupted function pointer / return address) |
| 1 | instruction access fault | PC in a PMP-protected region (OpenSBI RAM!) or outside RAM |
| 2 | illegal instruction | executing data; CSR access not permitted; FP use with FPU off |
| 3 | breakpoint | `ebreak` — ours (T1.2 test) or a debugger's. **Observed:** the T1.2 self-test assembled as `c.ebreak` (`0x9002`, 2 bytes) at `sepc=0x8020114a`. |
| 4 / 6 | load / store misaligned | should not happen (rv64gc allows misaligned in QEMU) — suspect wild pointer |
| 5 / 7 | load / store access fault | PMP violation (OpenSBI region) or nonexistent physical address |
| 8 | ecall from U-mode | a syscall (M2+) — not an error |
| 9 | ecall from S-mode | our own SBI call leaked into our handler? should be handled by M-mode — suspect delegation confusion |
| 12 | instruction page fault | PC unmapped / not X / U-bit mismatch — *the* M1-T1.5 and M2 entry-to-U failure |
| 13 | load page fault | read through unmapped/notR VA; `stval` = the address |
| 15 | store/AMO page fault | write through unmapped/notW VA; also: missing D/A bits on some QEMU configs |

**Interrupt codes (`scause` interrupt bit = 1):**

| code | meaning |
|---|---|
| 1 | supervisor software interrupt (unused until/unless we IPI — never, D-0007) |
| 5 | supervisor timer interrupt (M1 T1.3 onward) |
| 9 | supervisor external interrupt (PLIC — only if M3 opts into IRQ-driven net) |

Diagnosis recipe: `scause` picks the row; `sepc` → `addr2line`/`objdump` to
find the source line (`just addr2line 0x...`); `stval` tells you which address
or instruction. Then ask: *should* that address be mapped/permitted at that
point in boot? The answer is in the page-table code or the linker script.

## 4. Instant-hang causes, by milestone

"Instant hang" = no output, no trap printed, QEMU sits at 100% or idle.
The generic cause: the hart trapped somewhere with no (working) handler, or
never reached your code. In rough order of likelihood:

**M0**
1. Link address ≠ 0x8020_0000, or entry section not first → OpenSBI jumps into
   whatever bytes are there. Check: `readelf -l` (LOAD paddr, entry) and
   `just objdump | head`.
2. `sp` never set / set to a bogus address → first Rust function prologue
   store-faults with `stvec` unset → hang. Check with gdb `si` from `_start`.
   Observed `kmain` prologue (debug build): `addi sp,sp,-32`; `sd ra,24(sp)`;
   `sd s0,16(sp)`; **`addi s0,sp,32`** (frame pointer, after the saves);
   then `sd a0`/`sd a1` relative to `s0`. The `ra`/`s0` stores use `sp`, not
   `s0` — `s0` is only established after those stores.
3. `.bss` not zeroed → statics contain junk → weird behavior *later* (this one
   defers, it doesn't usually instant-hang — which is worse).
4. Touched 0x8000_0000–0x8020_0000 (OpenSBI's PMP-protected RAM) → access
   fault → hang. `-d int` shows cause 5/7 with a telltale `tval`.

**M1**
1. The `satp` write with the current PC not identity-mapped X → instruction
   page fault → handler also unmapped → tight trap loop. `-d int` shows
   repeating cause 12 with `epc` = instruction after the `csrw satp`.
   Full first-response procedure for T1.7 below this list.
2. **Before T1.2 installs `__trap_entry`:** OpenSBI leaves `stvec =
   0x80200000` (our `_start`, Direct mode). Any *delegated* exception jumps
   there, so `_start` re-runs: `gp`/`sp` reset, `.bss` zeroed, `kmain` again.
   Symptom is a repeating OpenSBI banner plus `whimbrel: hello` line — not a
   hang, not a firmware dump, not a trap report. T1.2's `stvec` write
   eliminates this. Codes 1/2/4/5/6/7 are still not delegated and still
   produce a firmware dump if they fire.
3. `stvec` unset/misaligned when the first trap arrives (low 2 bits of `stvec`
   are a MODE field — a non-4-byte-aligned handler address silently corrupts
   both mode and address). Advancing `sepc` is a separate footgun: `ecall` is
   always 4 bytes, but the trapped instruction in general may be 2 (RVC).
   Hardcoding `sepc += 4` in our handler will skip a byte after a compressed
   trap. Decode width from the instruction at `sepc` (see GLOSSARY: RVC).
   **Observed T1.2:** the self-test `ebreak` was emitted as `c.ebreak`
   (encoding `0x9002`, width 2) at `sepc=0x8020114a`. A hardcoded `+4` would
   have `sret`'d into the middle of the next instruction. D-0021's decode is
   load-bearing, not theoretical.
4. Trap handler itself faults (unmapped stack, clobbered register) →
   recursive trap → loop. `-d int`: alternating/nested causes.
5. Missing `sfence.vma` after editing PTEs → stale TLB → works, then faults
   "impossibly" — or works in QEMU and would fail on hardware. Fence after
   every PTE change (project rule).
6. Missing A/D bits in PTEs → cause 13/15 on first touch, on QEMU configs that
   don't set them in hardware. We set A|D on all kernel leaves (PLAN M1/T1.5).
7. Timer interrupt enabled before `stvec` points at a real handler.
8. **Post-T1.3, `park()` is not quiescence.** `park` is `wfi`. With `sie.STIE`
   set, the hart wakes every 10 ms, runs the timer handler, and `sret`s into
   the next `wfi` — a "parked" panic prints `PANIC` and then keeps emitting
   `tick N` lines. Leave `park` as `wfi`. True quiescence (a panic that must
   not be interrupted, or the T1.7 `satp` window) clears `sie.STIE` first
   (T1.7 also clears `sstatus.SIE`, D-0022 — same effect on ticks).
9. First static introduced = first real exercise of the `.bss` zero loop
   (the section is empty until then, so the loop is a no-op). **Confirmed
   working as of T0.4:** `IN_PANIC` at `__bss_start` (`0x80203000`) read
   `false` at `kmain`, `__bss_end` moved to `0x80204000`, stack sat above
   it. Drop the loop off the M1 suspect list unless the bounds themselves
   change (new sections, a broken `__bss_end`). A later static that reads
   nonzero is then a bug in the reader, not in `_start`.
10. **T1.5 guard page is inert until T1.7.** The linker leaves a 4 KiB hole
    between `__bss_end` and `__boot_stack_bottom`. Paging is still off, so
    that hole is ordinary RAM: a stack overflow still stores into `.bss`.
    T1.6 omits the page from the tables; T1.7 makes the omission a
    store page fault (`scause=15`). T1.6 prints `root_pa` and
    `satp_would_write` without writing `satp` — T1.7 writes that same
    value, `MODE=8 << 60 | PPN`.

**T1.7 (activating paging) — first response when it hangs**

The symptom is total silence right after the `satp` write, because the fault
handler's own page is unmapped and the fault faults. There is no panic to read,
so both channels here are outside the guest:

1. **`-d int`.** A repeating cause-12 block whose `epc` equals the instruction
   *after* the `csrw satp` is the signature: the PC is not mapped executable
   under the new tables. Read the *first* block, not the hundredth — the rest
   are the loop.
2. **Monitor `info mem`.** This dumps the decoded Sv39 tables. Compare it
   against the linker map and against the expected permissions in PLAN.md's
   M1 memory-map table. Answering "did I map what I think I mapped" takes one
   command and beats an hour of re-reading the mapping code.
3. **Work prerequisite concept 11 as a checklist** (PLAN.md, M1). It
   enumerates the twelve conditions that must hold before the `csrw` retires,
   in roughly the order they break: `MODE`, the `PPN` shift, the root entry,
   non-leaf `R=W=X=0`, leaf `X`, leaf `U=0`, `A`/`D`, identity, walker
   reachability, stack and `ra`, the `stvec` page, and the interrupt window.
4. **Suspect the two silent ones first.** `satp.MODE=0` means paging never
   turned on at all (everything "works", nothing is translated), and writing
   the root table's address instead of its address-shifted-right-12 is a
   factor-of-4096 error that points the walker at garbage. Neither announces
   itself.
5. **If it hangs only sometimes, it is the interrupt window** (D-0022): a tick
   landing between the `csrw` and the `sfence.vma`. Confirm by checking that
   `sstatus.SIE` is clear across the switch.

If T1.6's software walk passed and T1.7 still hangs, the disagreement is
between what the tables say and what the hardware is doing with them — which
narrows it to the `satp` value itself (items 1, 2) or the fence.

**What actually made T1.7 work.** T1.6 walked a *sample* of the map: kernel
entry, the last byte of `.text`, stack top, and so on. Interior `.text`
pages were filled by the same `map_range` loop but never probed. The
instruction after `csrw satp` (`sfence.vma` at `0x802036d0`) sat on one of
those interior pages (`0x80203000`, L0[3] of the `0x80200000–0x80400000`
slot). `require_leaf` on that function’s PC, on `__trap_entry`, and on the
live `sp`, immediately before the SIE window, closed preconditions 5–8, 10,
and 11 for the entries the transition actually uses. The general lesson:
verify the specific translations the cliff depends on, not a representative
sample of the range.

**Mixed-granularity superpages (D-0059) — first response**

RAM interior `[0x80400000, RAM_END)` is 2 MiB L1 leaves; everything the
map distinguishes at 4 KiB grain stays L0 (W^X, guards, user slots and
sections, virtio-mmio, the `[__heap_end, 0x80400000)` fragment). Wrong
level is a panic, not a pass. `require_leaf` on the `satp` cliff stays
L0 because those VAs remain in the 4 KiB region.

- **Monitor `info mem`.** After paging is on, the decoded map should
  show 2 MiB pages from `0x80400000`. 4 KiB throughout that range means
  `map_range_2m` never ran (or `assert_range` never asked for L1).
- **Panic `walk: misaligned 2 MiB PPN` / `map_2m: unaligned`.** This is
  D-0026's named failure mode: a superpage PPN with nonzero low bits.
  The mapper and the walker both refuse it.
- **Panic `… L0 want L1` or `… L1 want L0`.** Mapper and verifier
  disagree on the region's expected leaf level. The printed probe row
  already has `L{}`; the panic names both.
- **`page_verify` still ~2 ms, or `assert_range: L1 leaf … overruns
  end`.** `assert_range` is still stepping 4 KiB against L1 leaves —
  the named failed co-edit. Grain-correct verify is hundreds of
  iterations, not ~32k.
- **`tables=` not 5, or `tables_used=N want 5`.** L1 leaves were not
  installed (still 67), or the image grew a table the derivation does
  not count. Do not patch the assert; recompute `EXPECTED_TABLES`.
- **`heap_end=… crossed RAM_L1_START`.** `__heap_end` reached the 2 MiB
  region. That would add an L0 and invalidate the constant. Shrink or
  recompute; do not raise `RAM_L1_START` to paper over it.

**M2**
1. `sret` to U-mode with `sstatus.SPP` still S, or `sepc` bogus.
2. User page lacking the U bit → instruction page fault at the first user
   instruction (cause 12, `sepc` = user entry). Reaches further than expected:
   a string literal left in kernel `.rodata` faults on the task's *load*
   (cause 13), and a compiler-emitted `memcpy` call into kernel `.text` faults
   on the *fetch* (cause 12, `sepc` inside kernel text).
3. Kernel dereferencing user memory without `sstatus.SUM` → cause 13/15 from
   *kernel* `sepc` with a user address in `stval`.
4. Trap entry using the wrong stack (`sscratch` protocol wrong, D-0029) →
   corruption two bugs away from the cause. If `sscratch` is nonzero while
   executing in S-mode, a kernel exception is misclassified as a trap from
   U-mode and the entry pushes its frame over a live frame.
5. **Kernel `gp` not reloaded on the trap-from-U path** (D-0029) → kernel
   statics read through the *user's* `gp`. No fault, no proximity: the symptom
   is an impossible value in a static, seen inside the handler.
6. **Kernel stack overflow — a hang with no output. Known unrecoverable.**
   See below.
7. **Undelegated illegal instruction from U — firmware dump, hart parked,
   no `task N killed` line.** Cause 2 is not in `MEDELEG` (PLAN M1 concept 9,
   D-0034). A task that executes `unimp`, or an FP op with `FS=Off`, traps
   to OpenSBI, not to us. Symptom: an OpenSBI "unhandled trap" dump
   (`mcause=2`, `mepc` in `.utext`) and a parked hart; QEMU may sit there
   until the hang-guard. There is no kernel `PANIC`, no `task N killed`,
   and no reschedule — containment never ran. This is not a scheduler bug
   and not a missed kill in `trap_handler`. Confirm with `-d int`: `async:0
   cause:2` going to M-mode. The user-fault selftest uses a load page fault
   (cause 13, delegated) for exactly this reason.

**Kernel stack overflow (M2 onward) — known unrecoverable failure**

**Signature: a hang with no output at all, shortly after entering a deep
kernel call path** (a syscall that formats a lot, or the nested-panic path).
No `PANIC` line, no trap report, no `tick N`.

Why nothing prints, step by step:

1. The overflowing store lands in the 4 KiB unmapped guard hole below that
   task's kernel stack (D-0030) and raises a store page fault (cause 15) from
   S-mode.
2. The fault came from S-mode, so `sscratch` is 0 (D-0029). Trap entry's
   `csrrw`/`bnez`/`csrrw` sequence therefore *keeps the faulting `sp`* — which
   is already inside the guard hole.
3. Block 2 does `addi sp, sp, -272` and starts storing the frame through that
   same hole. Those stores fault too.
4. That fault re-enters `__trap_entry` in exactly the same state. Tight,
   silent loop.
5. Rust is never reached, so `IN_PANIC` and the panic printer never run. This
   is the fault-the-fault-forever case from M1 item 4, arriving by a different
   road.

So the guard page converts silent corruption of a neighbouring task's stack
into a silent hang. The damage stops; nothing is reported. It also only
guarantees that much while the overflowing stack frame is *smaller* than the
4 KiB hole — a single frame larger than the guard can step over it entirely
and put the entry's 272-byte push into mapped memory below, which is silent
corruption again.

Confirming it from outside the guest (the only channels that work):

1. **`-d int`.** A repeating cause-15 block whose `tval` sits in a guard hole
   — compare against the `nm` addresses of `__kstack*_bottom` — and whose
   `epc` is inside `__trap_entry`'s store sequence. Read the *first* block.
2. **Monitor `info registers`.** `sp` below the current task's
   `kstack_bottom` is the whole diagnosis.
3. **GDB**, `hbreak __trap_entry`, then check `sp` against the linker symbols.

Fixes, in the order to try them: shrink the offending call path (usually
`println!` formatting or a large local buffer on a syscall path); raise the
per-task kernel stack from 8 KiB; and only then consider the double-fault
stack D-0030 declines to implement — a range check of `sp` against the current
kernel stack bounds on every trap-from-S plus a switch to a reserved emergency
stack, which costs a load and a comparison in the hottest path because RISC-V
S-mode has only one `sscratch` and no hardware IST equivalent.

**M3 (expand at milestone start)**

Silent-device ladder — work **in this order**. A dead virtqueue produces
no trap; the first channel that can name the bug is the one to read.

1. **QEMU stderr** — a distinct diagnostic channel from `-d guest_errors`.
   The device model prints structural virtqueue complaints there and
   nowhere else: `Looped descriptor`, `virtio-net receive queue contains
   no in buffers`, `in_num`/`out_num` via `virtio_error` → `error_vreport`
   (`qemu-system-riscv64: …` on the host). `-d guest_errors` is MMIO and
   physical-access noise (write-only register reads, unmapped GPAs).
   **`-D qemu.log` captures only `-d` items; it does not capture stderr.**
   `just test` redirects stderr into `serial.log` (`2>&1`) — grep
   `qemu-system-riscv64:`. This named the T3.5 WRITE-vs-NEXT bug
   directly; `guest_errors` did not.
2. Virtqueue memory not physically contiguous / wrong physical address
   given to the device → device silently does nothing (no trap at all —
   the worst kind; check with `-d guest_errors` and the device's status
   field).
3. Missing memory barrier between writing descriptors and ringing the
   doorbell.
4. Legacy vs modern virtio-mmio register layout mismatch.
5. Reading QueueDesc/QueueDriver/QueueDevice Low/High returns 0 on QEMU.
   Those six registers are write-only (virtio 1.2 §4.2.2); QEMU logs
   `read of write-only register` under `-d guest_errors`. A zero read is
   not proof the write stuck. QueueReady (0x044) *is* readable — if it is
   already 1, the device owns the ring and `verify()` is too late.
   **A wrong register offset remains undetectable at init on this
   transport** (the readback cannot distinguish it from a correct write).
   If the ring is dead in T3.3, re-derive the offsets against the spec
   table first, not last.
6. A Status=0 soft reset (`virtio_mmio_soft_reset`) clears the queue
   address registers. Any re-init path must rewrite them before
   QueueReady. A driver that writes them once at startup and resets later
   has a dead ring with no diagnostic — `used.idx` never moves, the pcap
   stays empty.
7. `net::dump` showing `isr=0x1` after the first used-ring update is
   expected under polling. Virtio-mmio InterruptStatus bit 0
   (`VIRTIO_MMIO_INT_VRING`) stays set until a write to InterruptACK
   (0x064). We never ACK: the PLIC is unmapped (D-0040) and the bit is
   not how we notice work — `used.idx` is. Do not "fix" this by ACK-ing
   in the dump path; that would hide a later accidental interrupt enable.
8. **Harness assertions fail closed, and every new one must have its
   failure modes exercised before it is trusted.** A missing file, an
   empty file, a well-formed file with zero matching frames, and a
   missing tool (`tshark`, `llvm-objdump`) are four different bugs; if
   you only run the happy path, a vacuous pass is indistinguishable from
   a real one. This is the same shape as `check-utext` (unknown objdump
   lines are a hard error, not a skip) and as the T3.4 `just test` hole:
   that recipe is `set -u` without `set -e`, so a bare
   `bash scripts/assert-….sh` returning 1 was ignored and the recipe
   still printed PASS. Wrap every new assert with `if ! …; then exit 1;
   fi` (or give the recipe `set -e`). It will recur in each new test
   script — treat an untested failure mode as an unwritten assert.
9. RX `used.idx` stuck, status gains `0x40` (`DEVICE_NEEDS_RESET`), QEMU
   **stderr** prints `Looped descriptor` and `virtqueue_pop … in_num 0
   out_num 1`: `VIRTQ_DESC_F_WRITE` is **2**, `NEXT` is **1**. Flagging
   RX buffers with 1 makes the device follow `next` (often 0) around the
   table. TX still works — those descriptors are device-readable
   (`flags=0`). Read stderr (item 1) before `-d guest_errors`.
10. A hostfwd connect after our GARP does not produce an ARP request.
    slirp caches the GARP (or already learned us from our request for
    `10.0.2.2`) and sends IPv4 (TCP SYN) unicast. After D-0054 we ARP
    the gateway ourselves; slirp often never ARPs us, so `TX ARP reply`
    is not a boot event. The net-init watcher fires one connect after
    `gateway 10.0.2.2 MAC learned` so the SYN is not dropped as noarp
    (D-0046). The live pcap assert is our request then slirp's reply
    (`assert-pcap-gateway-arp.sh`), not the T3.5/T3.6 slirp-asked-first
    chain. **Converse (client-early runs):** a connect *before* any
    guest TX makes slirp broadcast `who has 10.0.2.15 tell 10.0.2.2`
    and queue the SYN; that ARP request as pcap frame 1 timestamps the
    accept, and the queued SYN flushes ~µs after the guest's first
    frame teaches slirp our MAC (D-0070). A pcap that starts with that
    slirp request is a client that connected before the guest was
    reachable, not a broken boot.
11. `ipv4 drop_proto` non-zero on a happy boot used to be the hostfwd
    TCP SYN (protocol 6) hitting a stack that did not yet parse TCP.
    That exception **expired at T3.10** (D-0049). TCP exists, so
    `drop_proto != 0` is a real drop. Do not "fix" a non-zero by
    grepping it away or by stopping the hostfwd watcher.
12. Archive section matching does **not** catch LLVM-generated anonymous
    rodata symbols. rustc emits string literals (and similar constants)
    as unique `.rodata..Lanon.*` sections inside `app-HASH.*.rcgu.o`
    members of `libapp-HASH.rlib`. A linker rule that only names
    `*libapp-*.rlib:(.rodata)` misses them; LLD orphans them into kernel
    `.rodata`. The symptom is the silent-wrong case: `app_main` sits in
    `.utext` at a user address, an `auipc` from that function into
    kernel `.rodata` (`0x8022xxxx`) **passes the link**, and the first
    use (the load of `UDP ECHO READY`) faults. `check-utext` catches
    the `auipc` after the fact; it does not place the bytes.
    `#[link_section = ".urodata"]` on the constant is the mechanism
    that works (same as `#[link_section = ".utext"]` on the function),
    plus matching the `*.rcgu.o` member names. Do not iterate
    `EXCLUDE_FILE` wildcards (D-0051).
13. A drop-first-TX retransmit selftest that **posts** the first data
    segment (so the pcap has two copies) but **ignores ACKs** until
    one RTO must also defer the peer FIN. If you ACK their FIN while
    still pretending you have not seen the ACK of yours, slirp goes
    CLOSED and the 200 ms copy meets RST — `rexmit=1` can still pass
    via RST clearing the TCB, and the capture will not show an ACK of
    the second segment. Symptom: `HTTP RETRANSMIT OK` on serial,
    pcap has two copies ~200 ms apart, then RST, assert "no ACK of
    nxtseq after second copy". Cause: simultaneous-close FIN-ACK
    while `hold_acks` is true (D-0053). Truncated TIME_WAIT also must
    not clear the app's EOF, and `recv` 0 must wait until inflight is
    gone, or the app exits before the timer can fire.
14. `cargo build --release` panics in `check_layout` with addresses that
    already look adjacent (`__kernel_end` and `__heap_start` both
    `0x80272000`). LLVM treats distinct `extern static`s as non-aliasing,
    so `addr_of!(a) == addr_of!(b)` constant-folds to false under LTO
    even when the linker placed them at the same VA. `!=` of two linker
    symbols (`.utext must follow boot stack`) is the same bug with the
    opposite fold. `core::hint::black_box` on the address keeps the
    comparison as a runtime load. Debug `opt-level=0` never hits this.
15. **HTTP 200 then silence before `M3 UNIKERNEL OK` / PHASE (D-0068).**
    After first-HTTP `wait_tx` the kernel `wfi`s so QEMU can deliver the
    frame before DBCN occupies TCG. Wake source is the next tick
    (`sie.STIE` plus a future deadline). If a later rung drops tick
    arming, `timer::assert_ticks_armed` panics with finding 13 rather
    than hanging. If that assert is gone and STIE is clear, the symptom
    is: client has the body, serial has no PHASE dump, QEMU idle.
    First response: gdb Ctrl-C, `$pc` on `wfi` in `timer::yield_once`,
    `info registers sie` (STIE = bit 5). Do not "fix" it by moving the
    dump back onto the publish→E4 path.

## 5. QEMU monitor — inspect a hung machine *without* GDB

With `-nographic`, the monitor is multiplexed on the console:
**Ctrl-a c** toggles console ↔ monitor; **Ctrl-a x** kills QEMU.

```
(qemu) info registers        # pc, all GPRs, and privilege/CSR state right now
(qemu) info mem              # DECODED Sv39 page tables — vaddr → paddr + flags
(qemu) xp /4gx 0x80200000    # read PHYSICAL memory (works regardless of satp)
(qemu) info mtree            # the machine's physical memory map (find MMIO)
```

`info mem` after enabling paging is the fastest way to answer "did I actually
map what I think I mapped" — compare it against the linker map. `xp` vs gdb's
`x`: the monitor reads physical addresses, gdb reads virtual through the
current translation; disagreement between them is itself a diagnosis.

## 6. When stuck > 30 minutes — checklist

Work the list in order; each step either finds it or shrinks the search space.

1. **State the symptom in one sentence** with the three CSR values if any
   trap printed. If you can't, that's the first problem: get `scause`/`sepc`/
   `stval` via panic print, gdb Ctrl-C, monitor `info registers`, or `-d int`.
2. **`git stash` / diff against the last green commit.** What changed since
   the acceptance test last passed? (Commit at every green state precisely to
   make this step cheap.)
3. **QEMU stderr, then `-d int,guest_errors`.** Structural virtqueue
   failures (`Looped descriptor`) print on stderr, not under `-d`. Then
   rerun with `-d int,guest_errors -D qemu.log`, grep away the `ecall`
   noise, read the *first* abnormal trap block — later ones are usually
   fallout.
4. **Verify the binary, not the source:** `just objdump` — is the entry where
   you think? Does the faulting `sepc` disassemble to what the source says?
   (Stale build / linker-script drift hides here.)
5. **GDB from reset:** `just debug`, `hbreak *0x80200000`, `si` forward.
   Watch `sp`, watch the first CSR writes. Ten instructions of ground truth
   beat an hour of theory.
6. **Interrogate the paging state:** monitor `info mem`; is the current `$pc`
   mapped X? Is the stack mapped W? Is the trap handler mapped?
7. **Write down three hypotheses ranked by likelihood and the observation
   that would kill each.** Test the cheapest first. (This step exists because
   steps 1–6 done angrily produce nothing.)
8. **Reduce:** comment out until it boots, reintroduce until it breaks. With
   sub-second boots, bisection is cheap.
9. **Explain it out loud from hardware up** — to the rubber duck or to the
   agent ("here's what the hardware should do at this instruction; here's
   what I observe"). If a step in your explanation is fuzzy, that step is the
   bug's home. Then take a real break.
10. **Found it?** Add symptom → cause to §4, and if the fix embodies a choice,
    log it in DECISIONS.md.

## 7. Host-side gate failures (not guest bugs)

1. **`just test` boots to `M3 UNIKERNEL OK`, then every pcap assert
   dies with `tshark: You don't have permission to read the file`.**
   Ubuntu 26.04 ships an enforcing AppArmor profile for `/usr/bin/tshark`
   that denies reads of pcaps under `$HOME`. The harness writes
   `whimbrel.pcap` in the repo. A copy in `/tmp` reads fine — that is
   the diagnostic, not the fix. Local AppArmor override: SETUP.md §7.
   Confirm with the audit log (`apparmor="DENIED"` on the pcap path).
2. **`check-utext: no kernel at target/riscv64gc-unknown-none-elf/…`
   after a build that seemed to succeed.** Cursor's agent shell injects
   `CARGO_TARGET_DIR=/tmp/cursor-sandbox-cache/…`, so cargo writes the
   image outside the tree and `check-utext.sh` looks at the in-tree
   default. Unset the variable, or run from a plain login shell
   (SETUP.md §7). Not a distro issue.

## Variant ELF 2 MB larger than the kernel / variant S inflated (D-0079)

Symptom: the `bios-none` donor ELF's first LOAD segment spans
`0x8000_0000–0x8021_xxxx` with ~2 MB filesz — zero padding between the
shim and `.text` — and any startup-slice (S) measurement of that image
runs milliseconds long.

Cause: LLD assigns sections to PT_LOADs in address order and pads
same-flag gaps in the file; script placement does not change it, and
`PHDRS` would rewrite the default image's headers. The 2 MB gap between
the shim (`0x8000_0000`) and `BASE_ADDRESS` is filled with file bytes
QEMU then loads.

Fix (D-0079): never boot the donor ELF. Extract the blob
(`objcopy -O binary --only-section=.mshim`) and pass it as
`-bios mshim.bin` with the **default** kernel ELF. The donor exists
only to be objcopied.

## A "no-op" refactor moved the kernel hash (D-0079)

Symptom: adding a `#[cfg]`-gated module declaration (or any line) to a
source file changes the release binary's sha256 even though the feature
is off and no generated code should differ.

Cause: `panic!`/`assert!` capture `core::panic::Location` — file and
**line** — into `.rodata`. Any edit that shifts line numbers below it
rewrites those strings. The binary is different because its panic
messages are.

Fix / practice: additions that must not move the default hash go at the
end of the file (`mod mshim;` sits last in `main.rs` for exactly this
reason). When comparing hashes across a refactor, a moved hash with
identical `.text` is this, not a codegen change — `readelf -x .rodata`
diff shows the line-number strings. Campaigns are unaffected: each
records its kernel sha per trial row.

## A diagnostic against QEMU boots ran 50× faster than designed (D-0080)

Symptom: a script that wraps QEMU in `timeout N` (or budgets wall time
assuming boots last seconds) completes almost instantly; sampling loops
built around those boots fire at millisecond cadence instead of their
designed spacing. D-0080's drift probe registered ~35–40 min at ~1.5 s
resolution and ran in **30 seconds** at 30.7 ms cadence — every gate it
had still passed, and the session was too short by 60× to see the
minutes-scale effect it was built to measure.

Cause: the default image **self-terminates**. After the app serves its
response and exits, the scheduler finds no ready task
(`task.rs`: "no ready task; shutting down") and calls `sbi::shutdown()`
— SBI SRST, honored by OpenSBI on `-bios default` and by the D-0079
shim's sifive_test seam on `-bios none` — so QEMU exits ~270 ms after
spawn. A `timeout 15` wrapper never binds; it is not pacing, it is a
dead man's brake that never engages. Only `http-persist` builds
(`just run-http`) keep serving.

Fix / practice: never derive pacing, duration, or cadence from QEMU
process lifetime. Enforce cadence explicitly (sleep to the next tick)
and gate the *achieved* duration and cadence fail-closed at the end of
the run; have the analyzer compute window spans from recorded
timestamps rather than assuming them. Related trap from the same
incident: the runner logged `date -u` while file mtimes showed local
time, and the 30-second run was read as four hours — label every
logged clock UTC explicitly.
