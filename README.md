# Whimbrel

A minimal RISC-V (rv64gc) unikernel in Rust: QEMU `virt` machine, booted via
OpenSBI, Sv39 paging, single hart, single address space. One application is
compiled into the image and runs in U-mode over a 5-syscall interface
(`write`, `exit`, `sbrk`, `gettime`, `yield`). At boot it ARPs its slirp
gateway, listens on TCP/80, and serves HTTP. Built as a learning and
portfolio project: **minimal and legible beats clever, everywhere.**

**Status: M3 done.** M0–M3 are merged on `main` (traps, paging, U-mode,
virtio-net, HTTP). **M4 (evaluation) is in progress** on `m4-evaluation`:
the harness and attribution stamps have landed; the report-grade baseline
waits on a dedicated measurement host ([docs/SETUP.md](docs/SETUP.md) §7,
D-0055).

## Documentation map

| Document | Contents |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Milestones M0–M4: goals, acceptance tests, prerequisite concepts, risks, effort tiers |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decision log — every nontrivial choice, before its code |
| [docs/DEBUGGING.md](docs/DEBUGGING.md) | rv64/QEMU field guide: GDB stub, `-d int`, trap CSRs, hang causes, stuck-checklist |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Running definitions of every term of art |
| [docs/SETUP.md](docs/SETUP.md) | Host packages, Rust toolchain, editor extensions, dedicated bench host |
| [.cursor/rules/project.mdc](.cursor/rules/project.mdc) | Fixed constraints and standing workflow rules for AI-assisted sessions |

## Quickstart

Environment setup: [docs/SETUP.md](docs/SETUP.md). Then:

```bash
just test                 # headless PASS: M3 UNIKERNEL OK, curl 200, pcap, phases
just run-http             # persist HTTP image on host :8080 until you kill QEMU
# in another terminal:
curl -v http://127.0.0.1:8080/
```

`just run` boots the one-shot image (one GET, then guest exit). Other
recipes: `just test-panic`, `just test-hang`, `just test-stress`,
`just debug`, `just objdump`.
