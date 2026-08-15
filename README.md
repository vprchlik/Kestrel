# Whimbrel

A minimal RISC-V (rv64gc) unikernel in Rust: QEMU `virt` machine, booted via
OpenSBI, Sv39 paging, single hart, single address space — one application
compiled into the image, running in U-mode over a 5-syscall interface
(`write`, `exit`, `sbrk`, `gettime`, `yield`), benchmarked against a minimal
Linux VM. Built as a learning and portfolio project: **minimal and legible
beats clever, everywhere.**

**Status: M1 done.** Traps, 10 ms ticks, frames, Sv39 identity map, heap.
`just test` gates on `M1 FUNDAMENTALS OK`. M2 (U-mode, syscalls, scheduling)
is next and is not started.

## Documentation map

| Document | Contents |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Milestones M0–M4: goals, acceptance tests, prerequisite concepts, risks, effort tiers |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decision log — every nontrivial choice, before its code |
| [docs/DEBUGGING.md](docs/DEBUGGING.md) | rv64/QEMU field guide: GDB stub, `-d int`, trap CSRs, hang causes, stuck-checklist |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Running definitions of every term of art |
| [docs/SETUP.md](docs/SETUP.md) | Host packages, Rust toolchain, editor extensions, verification |
| [.cursor/rules/project.mdc](.cursor/rules/project.mdc) | Fixed constraints and standing workflow rules for AI-assisted sessions |

## Quickstart

Environment setup: [docs/SETUP.md](docs/SETUP.md). Then:

```bash
just run       # build + boot; QEMU exits 0 after M1 FUNDAMENTALS OK
just test      # headless PASS (exit 0) on M1 FUNDAMENTALS OK
just test-panic
just test-hang # FAIL (1) / HANG (2) — same harness, diverted builds
just test-stress # allocator storm + 1 ms ticks; frame-exhaust panic
just debug   # boot frozen with GDB stub; then `just gdb` or F5 in the editor
just objdump # disassemble the kernel image
```
