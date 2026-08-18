# results/ — T4.1 harness output (D-0055)

Long/tidy CSV, not wide. T4.2 adding stamps is **more rows in
`phases.csv`**, not more columns and not a schema migration.

The bench harness **does not** contain a phase-name list. It parses
whatever `PHASE <name> ticks=...` lines serial prints (`src/phase.rs`
`NAMES`). That is how it avoids becoming a fourth copy of the justfile
list (audit finding 26 / D-0057). The three HTTP gate greps share one
`phase_names` justfile variable as of T4.2; they still cannot merge with
`NAMES` without a generator. Collapsing the remaining copy is declined:
the kernel owns the printed names, the gates own the required-presence
set.

`client_granularity_ns` is per-batch metadata duplicated onto every run
row (C1): median inter-attempt interval from a 200-try calibration of
the persistent client, target 1 ms. `shuffle_seed` is the same kind of
batch-level field. Recorded trials of every config in a batch are
interleaved and shuffled so monotonic host drift cannot masquerade as a
config effect.

The stability criterion still compares **two interleaved batches**
(batch 1 vs batch 2 medians, same N=30 recorded per config), not the two
arms inside one batch. Within-batch default vs fast-boot is the
price-of-paranoia contrast; it is supposed to differ.

**The top-level CSVs are the latest run, overwritten, not appended.**
`just bench` replaces `results/runs.csv` and `results/phases.csv`.
T4.6 after-ladder pin: batches `20260817T061753Z-1` / `-2`
(measured kernel `76830e13`, CSV commit `c40945c`). D-0068
yield-then-dump batches (`20260818T013740Z-*` at `59e0703`,
`20260818T014549Z-*` at `4755fa3`) are not the after-ladder pin.
T4.4 rows live in git history (`867e28f`) until D-0067 per-batch
files exist. The T4.3 freeze rows live in tag `baseline-t4.3`
(commit `bce55a2`, measured kernel `35861f3`, batches
`20260817T041311Z-1` / `-2`).

**Exhibit generator:** `just report-exhibits` runs
`scripts/report-exhibits.py`, which `git show`s
`baseline-t4.3:results/{runs,phases}.csv` for the safe/fast/IQR/min
columns and the T4.6 CSV commit for after-ladder and Δ. D-0068
dump-placement is a third exhibit from those plus the two
yield-then-dump CSV commits. It does not read the working tree, so
a local `just bench` leftover cannot become an exhibit.
`just d0070-pcap-pass` generates the fourth exhibit
(`report/exhibits/d0070-pcap.md`): CSVs via `git show` of the three
campaign commits, per-trial pcaps from `results/trials/` — those are
gitignored and exist only on the bench host, so the exhibit is
generated and committed from there. It fails closed anywhere else. Machine-spec
baseline header comes from
`git show baseline-t4.3:results/baseline-summary.txt`; the
after-ladder (superpages) block comes from the T4.6 CSV fields, not
from `results/summary.txt`.

Do not treat `results/summary.txt` as a report artifact; it is
gitignored and may be a leftover from a local run.

## Bench-host spec (D-0067) — implement there, not in this tree

Approved. Do **not** change `scripts/bench.py` in this repository;
the dedicated host owns the harness write path (D-0055). This
section is the interface.

### Directory layout

```
results/batches/<batch_id>/runs.csv
results/batches/<batch_id>/phases.csv
results/batches/<batch_id>/summary.txt
results/runs.csv          # latest run only (overwrite, as today)
results/phases.csv        # latest run only (overwrite, as today)
results/summary.txt       # latest run only; stays gitignored
results/baseline-summary.txt   # freeze-era machine spec; unchanged
```

`<batch_id>` is the existing UTC-stamp form (`20260817T052349Z-1`).
A two-batch stability run produces two directories.

At the end of each batch — and again at the end of a two-batch
stability run — **copy** that batch's rows into
`results/batches/<batch_id>/` *before* the next run can overwrite
the top-level files. Do not append to the top-level CSVs.

Track `results/batches/` in git (unlike `results/trials/`
serial/pcap). The freeze tag `baseline-t4.3` remains the baseline
pin; per-batch files are how later rungs accumulate without
retagging and without `git show HEAD~N`.

### What stays at `results/{runs,phases}.csv`

Exactly what they are today: the **latest** run, overwritten in
place. `just bench-summary` keeps reading them. They are not the
ladder archive. After a superpage N-trial they will hold that
trial's two batches; the T4.4 rows live in
`results/batches/20260817T052349Z-{1,2}/` once copied, and the
freeze rows remain in tag `baseline-t4.3`.

Schema of those CSVs does not change (columns above). Per-batch
copies are the same schema, one `batch_id` per directory.

### Generator interface (once the files exist)

```
python3 scripts/report-exhibits.py \
  --baseline-tag baseline-t4.3 \
  --after-batches 20260817T061753Z-1,20260817T061753Z-2
```

- `--baseline-tag` (default `baseline-t4.3`): `git show
  <tag>:results/{runs,phases}.csv` and
  `<tag>:results/baseline-summary.txt`, same as today.
- `--after-batches <id>,<id>`: read
  `results/batches/<id>/{runs,phases}.csv` and concatenate. Two
  ids, the stability pair. Fail closed on mixed `git_sha`, mixed
  QEMU, dirty rows, recorded n≠60 per config, steal≠0 — the same
  checks as today.

Until those files exist, the generator stays two `git show`
objects (`baseline-t4.3` vs `HEAD`) and does not grow argparse.
Do not implement the flags against missing directories; a
half-wired `--after-batches` that falls back to HEAD is fail-open.

This pod does not run `just bench`. The bench host runs N-trials
and is the first writer of `results/batches/`.

## `runs.csv` — one row per trial

| column | meaning |
|---|---|
| `batch_id` | UTC stamp + batch index (`20260816T090000Z-1`) |
| `trial` | per-config 1-based index (warmup 1..W, recorded W+1..W+N). Wall-clock order is `run_order`, not this column. |
| `warmup` | `1` for the first 3 trials of that config in the batch (round-robin warmup), else `0` |
| `system` | `whimbrel` |
| `config` | `release-default` / `release-fast-boot` / `release-fast-boot-nofp` |
| `git_sha` | `git rev-parse HEAD` |
| `dirty` | `1` if `git status --porcelain` is non-empty |
| `kernel_sha256` | SHA-256 of the ELF this trial booted |
| `qemu_version` | first line of `qemu-system-riscv64 --version` |
| `qemu_hash` | SHA-256 of the QEMU binary |
| `host_kernel` | `uname -r` |
| `cpu_model` | `/proc/cpuinfo` model name |
| `governor` | `scaling_governor`, or `unavailable`. Asserted `performance` at batch start. |
| `smt_control` | `/sys/devices/system/cpu/smt/control`, or `unavailable`. Asserted `off`. |
| `cpufreq_boost` | `/sys/devices/system/cpu/cpufreq/boost`, or `unavailable`. Asserted `0`. |
| `virt` | `systemd-detect-virt` stdout, or `unavailable`. Asserted `none`. |
| `steal_start_ticks` | `/proc/stat` aggregate steal at batch start. Asserted `0`. Copied onto every row. Distinct from per-trial `steal_ticks`. |
| `loadavg_1m` | 1-minute load average at batch start (copied onto each row) |
| `qemu_cpu` | `taskset` core for QEMU |
| `client_cpu` | `taskset` core for the client (must differ) |
| `client_granularity_ns` | C1: measured client cadence (batch-level) |
| `shuffle_seed` | RNG seed for recorded-trial shuffle (batch-level; `seed + batch_index`) |
| `run_order` | 1-based wall-clock sequence across the whole run (warmup included) |
| `steal_ticks` | `/proc/stat` aggregate `cpu` steal column, delta across the trial |
| `steal_ns` | `steal_ticks * 1e9 / SC_CLK_TCK` (10 ms/tick when USER_HZ=100) |
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
| `phase` | name from the `PHASE` line (`_start`, `stamp_a`, `activate`, `net_init_done`, `syn_rx`, `E3g`, …) |
| `ticks` | guest `rdtime` |
| `ns_since_e2` | ns since `_start` (E2); 100 ns/tick |
| `delta_ticks` | ticks since the previous stamp in serial order |
| `delta_ns` | that delta × 100 |
| `source` | `serial` (room for a future instrument without new columns) |

A `PHASE` line that does not match the machine-shaped regex, or `PHASE
<name> unset`, fails the trial.
