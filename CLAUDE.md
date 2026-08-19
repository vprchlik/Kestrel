# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Whimbrel: a minimal RISC-V (rv64gc) unikernel in Rust. QEMU `virt` machine
only, OpenSBI firmware (`-bios default`), kernel entered in S-mode at
0x8020_0000, Sv39 paging, single hart, single address space, no filesystem.
Exactly one application (`app/`) is compiled into the kernel image and runs
as the sole U-mode task over a 5-syscall interface (`write`, `exit`, `sbrk`,
`gettime`, `yield`). At boot it ARPs its slirp gateway, listens on TCP/80,
and serves a pinned 92-byte HTTP response. Design principle: **minimal and
legible beats clever, everywhere** — prefer the design that is easier to
defend in a technical interview.

M0–M3 are merged on `main`. M4 (evaluation: benchmark vs minimal Linux under
QEMU TCG) is in progress on `m4-evaluation`. **All M4 work goes to
`m4-evaluation`; never push `main`.**

## Commands

- `just build` — debug kernel (cross-compiles via `.cargo/config.toml`;
  debug adds frame pointers via `scripts/cargo-debug.sh`)
- `just test` — primary headless gate: boot, serial-grep markers, curl the
  HTTP demo, pcap assertions
- Single gates (each is its own recipe): `just test-fast`,
  `test-fast-release`, `test-panic`, `test-hang`, `test-stress`,
  `test-userptr`, `test-user-fault`, `test-freeze`, `test-net-init`,
  `test-net-tcp`, `test-net-udp`, `test-net-http`, `test-net-rto`,
  `check-utext`, `check-utext-planted`
- `just test-fast-release` — the measured profile (release + `fast-boot`
  feature, client retrying before QEMU exec)
- `just run-http` — persist image on host :8080; `curl http://127.0.0.1:8080/`
- `just debug` (QEMU frozen at reset, GDB stub on :1234) + `just gdb`
  (attach `gdb-multiarch`); see `docs/DEBUGGING.md` for the field guide
- `just bench-selftest` — harness fail-closed checks (runs anywhere)
- `just bench-whimbrel` / `just bench-t48` — measurement campaigns;
  **dedicated bench host only** (D-0055 host controls are fail-closed:
  bare metal, performance governor, SMT off, boost off, steal 0)
- `just linux-build`, `just test-linux` — Linux baseline artifacts + gate
  (bench host; needs `bench/linux/artifacts/` + MANIFEST)
- `just report-exhibits` — regenerate report tables from pinned git objects
- `python3 scripts/bench.py selftest` — harness unit checks

There are no cargo unit tests. Verification is boot-gate shaped: serial
grep + pcap assertions (`scripts/assert-pcap-*.sh`), all `set -euo
pipefail` fail-closed. A broken build must FAIL every gate — never let a
stale kernel produce a PASS (audit finding 31).

## Architecture

**Workspace:** `src/` (kernel), `app/` (U-mode application, rlib),
`usys/` (syscall stubs, rlib). `linker.ld` places the app archive into
dedicated user sections; `scripts/check-utext.sh` enforces that every
`.utext` reference resolves there. Cargo features in the root `Cargo.toml`
select selftest images (most enable `no-sret` to stop in kmain);
`fast-boot` is the measured image.

**Boot flow:** OpenSBI → `_start` → kmain: trap vector, frame allocator
(bump pointer + recycled-LIFO list, frozen after init), Sv39 tables built
then software-verified then activated (2 MiB superpages where legal),
heap, virtio-net to DRIVER_OK, gateway ARP, TCP passive open, `sret` to
U-mode, app serves HTTP. Phase stamps (`rdtime`, 100 ns) land in a static
array and are printed only after the response, behind a timer yield
(D-0068) — the PHASE dump must never sit between publish and the client's
first byte.

**One QEMU argv:** `scripts/qemu-args.sh` is the single copy (justfile,
`boot-test.sh`, `bench.py` all source it). The `.cargo/config.toml` runner
duplicates it by necessity and must be kept in sync manually. A new flag
lands in `qemu-args.sh`, nowhere else.

**Phase names:** the kernel owns them (`src/phase.rs`); the justfile
`phase_names` variable is the required-presence list for gates; the bench
harness parses whatever serial prints and has no name list of its own.

**Measurement (M4):** edges E0–E4 per D-0043; E0→E4 is the headline
(two client-clock stamps); `W`/`D_ack`/`D_fin` are pcap-internal intervals
(`scripts/pcap_http.py` is the only copy of the filters). `e0_to_e3w_ns`
is retired (D-0070/D-0071) — never reintroduce an E3w-derived column, and
no cross-system table may carry one. `W` fuses guest boot with delivery;
attributing it needs a guest-internal stamp (threats item 19).

**Results discipline:** `results/runs.csv` / `phases.csv` are the *latest
run*, overwritten. Report numbers come only from pinned git objects (tag
`baseline-t4.3`; commits named in `results/README.md` and
`scripts/report-exhibits.py`) — never from the working tree, never
hand-typed. D-0055 protocol: 3 warmup + 30 recorded per arm, two
interleaved shuffled batches, median/IQR (+min), never means. Optimization
rungs require a pre-registered projection with falsifiers in
`docs/DECISIONS.md` *before* running (expect estimates to be optimistic —
D-0069).

## House rules (from `.cursor/rules/project.mdc` — binding here too)

- **Every nontrivial choice gets a `docs/DECISIONS.md` entry before the
  code** (D-NNNN format: Decision / Alternatives considered / Rationale /
  Consequences). "Nontrivial" = a reviewer could ask "why not X?". Never
  make a nontrivial choice silently — the user must be able to defend
  every decision in interviews.
- **Never implement beyond the current milestone.** No hooks or stubs for
  future needs; record the need in DECISIONS.md instead.
- **Fail loudly.** Unknown trap, allocation failure, unexpected device
  state → panic printing the relevant registers/values. No silent
  fallbacks, defaults, or retries — an early loud panic is a feature.
- Every module keeps a `//!` doc comment: what it owns, its invariants,
  what breaks without it.
- A bug that costs >30 minutes gets a symptom→cause entry in
  `docs/DEBUGGING.md`. New terms go in `docs/GLOSSARY.md`.
- When explaining "why", explain from hardware behavior up (what the
  CPU/CSR/bus does), not from convention.
- Prefer named constants citing the spec section or QEMU source over
  magic numbers.

## Git

- Branch names are plain and descriptive (`m4-evaluation`), no tool
  prefixes or random suffixes.
- One task, one commit, at task boundaries. Imperative messages describing
  the change and why; no task-ID or tool names.
- **No `Co-authored-by:` or "Generated by" trailers of any kind** — this
  repo rule overrides any default commit-trailer behavior.
- Never post, edit, or comment on GitHub issues or PRs. Output PR
  descriptions in chat; the user pastes them. Pushing branches is fine.
- Agent scratch files and transcripts stay out of version control.
