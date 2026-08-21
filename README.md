# Whimbrel

A minimal RISC-V (rv64gc) unikernel written from scratch in Rust: QEMU's
`virt` machine, OpenSBI (or the project's own M-mode shim), Sv39 paging,
single hart, single address space, one application compiled into the
image and run in U-mode over a five-syscall interface (`write`, `exit`,
`sbrk`, `gettime`, `yield`). At boot it brings up a hand-rolled
virtio-net driver and network stack (Ethernet, ARP, IPv4, ICMP, UDP,
TCP; smoltcp deliberately rejected) and serves one pinned HTTP
response. The design rule everywhere: minimal and legible beats clever.

The project's claims live in its measurement work: campaigns are
pre-registered with falsifiers before they run, gates fail closed, and
every published number regenerates from pinned git objects, never from
the working tree.

**Status:** M0-M3 (boot, traps, paging, U-mode execution, virtio-net,
HTTP) are done and merged. M4 (evaluation) is in progress, and it is
one milestone carrying both the measurement apparatus (harness,
campaigns, generated exhibits) and kernel changes of its own: the
phase-stamp instrumentation, the T4.7 M-mode shim with its allowlisted
kernel seams, and the `check_dtb` boot assert. The campaigns below are
measured and pinned; the technical report lands last. Remaining M4 work
is tracked in [docs/PLAN.md](docs/PLAN.md).

## Results

All numbers are medians on **RISC-V under QEMU TCG software emulation
(no KVM)** on one pinned bench host (AMD Ryzen 7 7800X3D, QEMU 10.2.1;
[machine-spec](report/exhibits/machine-spec.md)), n=60 recorded per arm,
warmup excluded. Absolute times here are not comparable to published
x86+KVM figures, which run roughly 5-10× lower; the ratio is what
transfers, because the emulation penalty applies to both arms
([cross-system](report/exhibits/cross-system.md)).

Boot to first HTTP byte (E0→E4, QEMU process spawn to first response
byte at the client) from one campaign with all arms interleaved
([cross-system-t48b](report/exhibits/cross-system-t48b.md)):

| system | E0→E4 median | IQR |
|---|---:|---:|
| Whimbrel `release-fast-boot` | **51.87 ms** | 404.8 µs |
| Whimbrel `release-default` (boot-time verification on) | 139.31 ms | 690.7 µs |
| Linux, minimal, tuned in good faith (Buildroot 2026.02.3 / kernel 6.18.7, config published) | 284.68 ms | 1.66 ms |
| Linux, stock defconfig | 948.10 ms | 4.27 ms |

Under those conditions (QEMU TCG software emulation on RISC-V, no KVM,
same host and same QEMU for every arm), the trimmed Linux baseline takes
**5.5×** the unikernel's time to first HTTP byte and stock takes
**18.3×**, and the comparison carries a known measured bias toward
Whimbrel: the D-0075 `/init` neighbor-table round trip adds 2.87 ms to
every Linux row, published in the exhibit. This is what single-purpose
structure buys under stated conditions, not a "fastest OS" claim.

Two more measured results:

- **Firmware removal**
  ([t47-firmware](report/exhibits/t47-firmware.md)). Under the same
  QEMU TCG conditions, replacing OpenSBI with the project's 320-byte
  M-mode boot shim in the `-bios` slot cuts fast-boot E0→E4 from
  52.27 / 52.19 ms to 28.58 / 28.50 ms per batch (−23.69 ms, 1.8×), of
  which only −0.714 ms is guest-side work; the rest is the removed
  firmware window, a volatile quantity reported per batch and never
  pooled. This is its own campaign: per the repo's same-campaign rule
  its ratio is computed only against its own OpenSBI arm, never against
  the table above.
- **Guest kernel work** ([edges](report/exhibits/edges.md),
  [phase-decomposition](report/exhibits/phase-decomposition.md)).
  E2→E3g (first kernel instruction to the HTTP response published)
  went from 21.42 ms at the pre-optimization freeze to 6.43 ms after a
  ladder of pre-registered optimization rungs. Every phase in that
  interval is attributed; none exceeds 19% of the total.

**What this is not.** As an operating system, Whimbrel is not
significant: one workload, one emulated machine shape, one request
served, then exit. The significant artifact is the measurement
discipline and the reasoning around it. The decision log keeps the
misses next to the wins: a headline metric retired when analysis
showed its name promised something it did not measure, an aborted
campaign, a published diagnosis later refuted by data already on disk
and marked as such in place, an expectation model retired after three
consecutive misses in the same direction. Those records are deliberate:
they are what makes the numbers above checkable.

## What's in the repo

| path | what it is |
|---|---|
| `src/` | the kernel: boot, trap handling, Sv39 paging, scheduler, virtio-net driver, the network stack |
| `app/` | the single U-mode application (the HTTP responder), linked into the kernel image |
| `usys/` | the five syscall stubs |
| `linker.ld` | image layout, including the dedicated user sections `check-utext` enforces |
| `scripts/` | the harness: boot gates, pcap assertions, `bench.py`, the exhibit generator |
| `bench/linux/` | pinned inputs for the Linux baseline: Buildroot pin, kernel-config fragments, `/init` |
| `docs/` | `PLAN.md` (milestones, acceptance tests), `DECISIONS.md` (the 80-entry decision log), `DEBUGGING.md` (rv64/QEMU field guide), `GLOSSARY.md`, `SETUP.md` |
| `report/` | generated exhibits (`report/exhibits/`); the technical report itself is still in draft |
| `results/` | latest-run CSVs, overwritten per run; report data lives only in pinned git objects (see [results/README.md](results/README.md)) |
| `justfile` | every gate and campaign as a recipe |

## Build and run

Environment ([docs/SETUP.md](docs/SETUP.md)): `qemu-system-riscv64`,
`just`, `tshark` (the gates assert on packet captures), and the Rust
toolchain pinned by `rust-toolchain.toml`.

```bash
just build     # cross-compile the kernel
just test      # headless gate: boot markers, curl the HTTP demo, pcap assertions
just run       # boot the one-shot image: serve one GET, then exit
just run-http  # persist the image on host :8080 …
curl -v http://127.0.0.1:8080/   # … and fetch it from another terminal
```

`just --list` shows the rest: per-gate recipes (`test-panic`,
`test-net-tcp`, `check-utext`, …), `just debug` / `just gdb` for the
GDB stub, and `just report-exhibits`, which regenerates every report
table from pinned git objects; a local run's CSVs cannot become an
exhibit. The measurement campaigns (`just bench-*`) fail closed
anywhere but the dedicated bench host: bare metal, performance
governor, SMT off, boost off, zero steal (D-0055).

## Reading further

- [report/exhibits/](report/exhibits/): every generated table,
  captioned with the exact git objects it reads and the command that
  regenerates it
- The technical report (methodology, results, threats to validity) is
  still in draft; the exhibits stand alone as the results record until
  it lands.
- [docs/DECISIONS.md](docs/DECISIONS.md): every nontrivial choice
  (alternatives, rationale, consequences, and, where it happened, the
  refutation)
- [docs/DEBUGGING.md](docs/DEBUGGING.md): symptom → cause field guide
  for rv64/QEMU work
- [results/README.md](results/README.md): how measurement data is
  pinned and why the working tree is never a source

## License

`SPDX-License-Identifier: MIT OR Apache-2.0`. Dual-licensed under
[MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option.

---

Built in part with AI assistance.
