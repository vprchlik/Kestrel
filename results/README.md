# results/ — T4.1 harness output (D-0055)

Long/tidy CSV, not wide. T4.2 adding stamps is **more rows in
`phases.csv`**, not more columns and not a schema migration.

The bench harness **does not** contain a phase-name list. It parses
whatever `PHASE <name> ticks=...` lines serial prints (`src/phase.rs`
`NAMES`). That is how it avoids becoming a fourth copy of the triplicated
justfile gate list (audit finding 26 / D-0057). The three justfile lists
stay until T4.2, when the names actually change and D-0057's co-edit
checklist (`phase.rs` N/NAMES + those three recipes) lands in one commit.
Collapsing them now would make that checklist stale mid-milestone.

`client_granularity_ns` is per-batch metadata duplicated onto every run
row (C1): median inter-attempt interval from a 200-try calibration of
the persistent client, target 1 ms.

## `runs.csv` — one row per trial

| column | meaning |
|---|---|
| `batch_id` | UTC stamp + batch index (`20260816T090000Z-1`) |
| `trial` | 1-based index in the batch (warmup first) |
| `warmup` | `1` for the first 3 trials, else `0` (summarizer drops warmup) |
| `system` | `whimbrel` |
| `config` | `release-default` / `release-fast-boot` / `release-fast-boot-nofp` |
| `git_sha` | `git rev-parse HEAD` |
| `dirty` | `1` if `git status --porcelain` is non-empty |
| `kernel_sha256` | SHA-256 of the ELF this trial booted |
| `qemu_version` | first line of `qemu-system-riscv64 --version` |
| `qemu_hash` | SHA-256 of the QEMU binary |
| `host_kernel` | `uname -r` |
| `cpu_model` | `/proc/cpuinfo` model name |
| `governor` | `scaling_governor`, or `unavailable` |
| `loadavg_1m` | 1-minute load average at batch start (copied onto each row) |
| `qemu_cpu` | `taskset` core for QEMU |
| `client_cpu` | `taskset` core for the client (must differ) |
| `client_granularity_ns` | C1: measured client cadence (batch-level) |
| `e0_mono_ns` | `time.monotonic_ns()` immediately before QEMU exec |
| `e0_wall_ns` | `time.time_ns()` at the same moment (diagnostic; not used for E3w) |
| `e0_to_first_connect_ns` | first successful `connect` − E0 (monotonic) |
| `e0_to_e3w_ns` | first-connect + pcap-relative (HTTP − SYN/ACK). QEMU dump wall ≠ Python realtime. |
| `e0_to_e4_ns` | first response byte − E0 (monotonic) |
| `attempts` | client connect attempts until first-connect |
| `pcap_path` | repo-relative filter-dump path |

The summarizer refuses to aggregate if `dirty=1`, if `qemu_version` is not
unique, if `git_sha` is not unique, or if there are zero recorded rows.

## `phases.csv` — one row per trial × phase

| column | meaning |
|---|---|
| `batch_id` | join to `runs.csv` |
| `trial` | join to `runs.csv` |
| `warmup` | same as runs |
| `system` | `whimbrel` |
| `config` | same as runs |
| `phase` | name from the `PHASE` line (`_start`, `E3g`, `E3g_doorbell`, …) |
| `ticks` | guest `rdtime` |
| `ns_since_e2` | ns since `_start` (E2); 100 ns/tick |
| `delta_ticks` | ticks since the previous stamp in serial order |
| `delta_ns` | that delta × 100 |
| `source` | `serial` (room for a future instrument without new columns) |

A `PHASE` line that does not match the machine-shaped regex, or `PHASE
<name> unset`, fails the trial.
