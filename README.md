# Kestrel

A minimal RISC-V (rv64gc) unikernel in Rust: QEMU `virt` machine, booted via
OpenSBI, Sv39 paging, single hart, single address space — one application
compiled into the image, running in U-mode over a 5-syscall interface
(`write`, `exit`, `sbrk`, `gettime`, `yield`), benchmarked against a minimal
Linux VM. Built as a learning and portfolio project: **minimal and legible
beats clever, everywhere.**

**Status: pre-M0.** Planning and scaffolding only — the kernel builds and
parks the hart; first output lands in milestone M0.

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
just run     # build + boot in QEMU (exit: Ctrl-a x)
just test    # headless boot, assert on serial output
just debug   # boot frozen with GDB stub; then `just gdb` or F5 in the editor
just objdump # disassemble the kernel image
```
