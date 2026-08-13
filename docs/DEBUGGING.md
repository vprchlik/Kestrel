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
    -kernel target/riscv64gc-unknown-none-elf/debug/kestrel \
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
- `guest_errors` is gold: it reports accesses to unmapped *physical* addresses
  (MMIO typos) that otherwise fail silently or as store faults.
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
| 3 | breakpoint | `ebreak` — ours (T1.2 test) or a debugger's |
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
2. `stvec` unset/misaligned when the first trap arrives (low 2 bits of `stvec`
   are a MODE field — a non-4-byte-aligned handler address silently corrupts
   both mode and address). Advancing `sepc` is a separate footgun: `ecall` is
   always 4 bytes, but the trapped instruction in general may be 2 (RVC).
   Hardcoding `sepc += 4` in our handler will skip a byte after a compressed
   trap. Decode width from the instruction at `sepc` (see GLOSSARY: RVC).
3. Trap handler itself faults (unmapped stack, clobbered register) →
   recursive trap → loop. `-d int`: alternating/nested causes.
4. Missing `sfence.vma` after editing PTEs → stale TLB → works, then faults
   "impossibly" — or works in QEMU and would fail on hardware. Fence after
   every PTE change (project rule).
5. Missing A/D bits in PTEs → cause 13/15 on first touch, on QEMU configs that
   don't set them in hardware. We set A|D on all kernel leaves (PLAN M1/T1.5).
6. Timer interrupt enabled before `stvec` points at a real handler.
7. First static introduced = first real exercise of the `.bss` zero loop
   (the section is empty until then, so the loop is a no-op). **Confirmed
   working as of T0.4:** `IN_PANIC` at `__bss_start` (`0x80203000`) read
   `false` at `kmain`, `__bss_end` moved to `0x80204000`, stack sat above
   it. Drop the loop off the M1 suspect list unless the bounds themselves
   change (new sections, a broken `__bss_end`). A later static that reads
   nonzero is then a bug in the reader, not in `_start`.

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

**M2 (expand at milestone start)**
1. `sret` to U-mode with `sstatus.SPP` still S, or `sepc` bogus.
2. User page lacking the U bit → instruction page fault at the first user
   instruction (cause 12, `sepc` = user entry).
3. Kernel dereferencing user memory without `sstatus.SUM` → cause 13/15 from
   *kernel* `sepc` at a user address in `stval`.
4. Trap entry using the wrong stack (sscratch dance wrong) → corruption two
   bugs away from the cause.

**M3 (expand at milestone start)**
1. Virtqueue memory not physically contiguous / wrong physical address given
   to the device → device silently does nothing (no trap at all — the worst
   kind; check with `-d guest_errors` and the device's status field).
2. Missing memory barrier between writing descriptors and ringing the
   doorbell.
3. Legacy vs modern virtio-mmio register layout mismatch.

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
3. **Rerun with `-d int,guest_errors -D qemu.log`**, grep away the `ecall`
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
