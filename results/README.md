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

New batches after D-0071 drop `e0_to_e3w_ns` and record `w_ns` /
`d_ack_ns` / `d_fin_ns` at trial time; the generator keeps reading
old-schema git objects for freeze / T4.6 / D-0068 and does not
grow a new-schema path until those CSVs exist as git objects
(Bench-host spec (D-0071) below). Working-tree leftovers still
cannot become an exhibit.

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

Schema of those CSVs **through D-0068** is the historical table
below (`e0_to_e3w_ns` present). D-0071 amends the schema for
**new** batches: drop `e0_to_e3w_ns`, add `w_ns` / `d_ack_ns` /
`d_fin_ns`. Do not rewrite freeze / T4.4 / T4.6 / D-0068 git
objects. Per-batch copies of a given batch keep that batch's
schema. Mixed schema inside one `runs.csv` is a fail.

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

## Bench-host spec (D-0071) — implement there, not in this tree

Approved. Do **not** change `scripts/bench.py` in this repository;
the dedicated host owns the harness write path (D-0055). This
section is the interface. `scripts/d0070-pcap-pass.py` is the
reference extract (`extract_pcap`); the harness uses that
implementation — import it, or split the extract into a shared
module both call. A third copy of the filters is a fail.

The D-0070 pass over already-recorded pcaps is closed. New batches
record the intervals at trial time so a future campaign does not
depend on gitignored pcaps surviving on one disk.

### What changes in `runs.csv`

Drop **`e0_to_e3w_ns`**. Its docstring assumption ("first-connect ≈
SYN/ACK") is false under hostfwd (D-0070). Keeping the column
would keep a number whose name still sounds like a wire edge.

Add three per-trial columns, all on **one pcap clock**
(`frame.time_relative`, `tcp.relative_sequence_numbers:FALSE`):

| column | definition | tshark |
|---|---|---|
| `w_ns` | t(first guest SYN/ACK) − t(first slirp ARP request for 10.0.2.15) | ARP: `arp.opcode==1 && arp.src.proto_ipv4==10.0.2.2 && arp.dst.proto_ipv4==10.0.2.15`. SYN/ACK: `tcp.srcport==80 && tcp.flags==0x012`. |
| `d_ack_ns` | t(first pure ACK from slirp of the 92 B payload+FIN) − t(HTTP frame) | HTTP: `tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.len > 0 && tcp.flags.syn == 0 && frame contains "HTTP/1.0 200 OK"`. ACK: `tcp && ip.src == 10.0.2.2 && ip.dst == 10.0.2.15 && tcp.dstport == 80 && tcp.flags.syn == 0 && tcp.flags.fin == 0 && tcp.flags.reset == 0 && tcp.flags.ack == 1 && tcp.len == 0 && tcp.ack == <HTTP tcp.nxtseq> && frame.number > <HTTP frame>`. |
| `d_fin_ns` | t(first client FIN toward :80) − t(HTTP frame) | `tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && tcp.flags.fin == 1 && frame.number > <HTTP frame>`. Upper bound on publish→client-recv; the bench client `close()`s after `recv`. |

Keep **`e0_to_first_connect_ns`**. It is a same-QEMU **control**,
not a comparison column. Under hostfwd it measures listener-up
during QEMU netdev init (~18.5 ms on this host, guest-independent).
A deviation flags a broken run, not a difference between systems.

Keep `e0_to_e4_ns` (headline), `e0_mono_ns`, `e0_wall_ns`
(diagnostic only; still never `pcap_epoch − e0_wall`), `attempts`,
`pcap_path`. `phases.csv` is unchanged.

Column order after the host-spec block:

```
e0_mono_ns
e0_wall_ns
e0_to_first_connect_ns
e0_to_e4_ns
w_ns
d_ack_ns
d_fin_ns
attempts
pcap_path
```

`e0_to_e3w_ns` is absent. A writer that still emits it, or a reader
that computes `e0_to_e4_ns − e0_to_e3w_ns` on a new-schema file, is
wrong.

### Fail closed

Every recorded trial (warmup included: a missing frame is a broken
boot, not a skip):

- pcap missing or empty
- any of ARP / SYN/ACK / HTTP / pure ACK / client FIN missing
- SYN/ACK before ARP, HTTP before SYN/ACK, ACK or FIN before HTTP
- HTTP `tcp.len` ≠ 92 (the committed 92-byte response). The Linux
  baseline serves the byte-identical response (D-0062 amendment),
  so the pin holds for every current system. A future system that
  legitimately serves different bytes gets its own stated pin,
  never a silent skip.
- for `system=whimbrel`: `d_fin_ns` ≥ 10 ms (D-0070 falsify line,
  now a harness invariant). Linux / Unikraft record `d_fin_ns`
  without that tripwire; a large value there is data.
- negative `w_ns` / `d_ack_ns` / `d_fin_ns`

Do not fall back to `e0_to_e3w_ns` construction. Do not substitute
a different pcap. Do not silently drop the trial.

**First-connect control.** After the batch, on recorded rows:

- `|median(e0_to_first_connect_ns)_safe − median(…)_fast| > 1 ms`
  → `TEST FAIL`: listener-up scaled with the guest profile; the
  control is broken.
- When a cross-system batch exists, the same 1 ms bound across
  `system` values. A miss fails the **run**. It does not become a
  table cell that looks like "Linux connects slower."

### S — batch header, not a `runs.csv` column

S is the pre-ARP QEMU-startup slice (listener-up → main-loop-live).
It is a **per-host, per-QEMU-build constant**. It does not scale
with the guest profile. It is never a report number.

**Do not add `s_ns` to `runs.csv`.** A per-trial column would invite
median / IQR / stability / rung-delta treatment — the exact path
by which "E3w→E4" acquired a host-sounding name (D-0071
methodology finding). S is not an input to any per-trial formula
the reader needs. Contrast `client_granularity_ns`, which *is*
copied onto every row because it interprets `attempts`.

**Record S in the batch header** (`results/summary.txt` and
`results/batches/<batch_id>/summary.txt`), next to `qemu_hash`:

```
s_ns=<median> iqr=<iqr> n=<recorded>
s_ns_fast=<median> s_ns_safe=<median>
```

Compute per recorded trial, internally, from stamps the harness
already has — not from a new clock:

```
s_trial_ns = (e0_to_e4_ns − e0_to_first_connect_ns)
           − (t_fin − t_arp)
           = (e0_to_e4_ns − e0_to_first_connect_ns)
           − (w_ns + synack_to_http_ns + d_fin_ns)
```

`synack_to_http_ns` is an extract internal (`extract_pcap` already
returns it). It is **not** a CSV column. `t_fin − t_arp` is one
pcap clock; `e0_to_e4 − e0_to_first_connect` is the client
monotonic clock. The mixed-clock remainder is S plus the µs
FIN-after-E4 tail. That is a diagnostic, not a first-class edge.

Fail closed on the header:

- `|s_ns_fast − s_ns_safe| > 1 ms` → `TEST FAIL` (S scaled with
  profile; D-0071 is reopened).
- Pool both configs for the headline `s_ns`. A QEMU or host change
  is allowed to move S on the *next* batch; that is the revisit
  trigger, and the header is the grain that shows it.

Do not copy S into `results/baseline-summary.txt` unless the freeze
is retaken on a new machine. The freeze object stays frozen.

### Stability and summary

`metric_table` / `just bench-summary` / two-batch stability:

- drop `e0_to_e3w_ns`
- add `w_ns` (tens of ms; participates in the ≥ 1 ms stability
  rule)
- add `d_ack_ns` and `d_fin_ns` (sub-ms on Whimbrel; the existing
  "skip if both medians < 1 ms" rule leaves them out of the
  stability pair, which is correct — they are not a host-drift
  check)
- keep `e0_to_first_connect_ns` and `e0_to_e4_ns`

Selftest fixtures that currently plant `e0_to_e3w_ns` plant the
new columns instead. A selftest row that still has `e0_to_e3w_ns`
must fail.

### Historical objects

Freeze (`baseline-t4.3`), T4.4 (`867e28f`), T4.6 (`c40945c`), and
both D-0068 CSV commits keep `e0_to_e3w_ns`. Do not rewrite them.
`just d0070-pcap-pass` remains the read-only reconstruction of
`w` / `d_ack` / `d_fin` from those campaigns' gitignored pcaps;
it is not the writer for new batches.

Schema detection (generator and summarizer):

- `e0_to_e3w_ns` present, `w_ns` absent → old schema
- `w_ns`, `d_ack_ns`, `d_fin_ns` present, `e0_to_e3w_ns` absent →
  new schema
- both, or neither → `TEST FAIL`

### Exhibit generator

Until a new-schema CSV exists as a git object, **do not** change
`scripts/report-exhibits.py`. A half-wired reader that falls back
to `e0_to_e3w_ns` is fail-open (same rule as D-0067's
`--after-batches`).

Once the first new-schema pin exists:

- Detect schema from the CSV header, per pin. Do not assume HEAD.
- **Old-schema pins** (freeze, T4.6, D-0068): keep generating
  dump-placement and the historical edges table, including
  E0→E3w / E3w→E4, with an explicit caption that those metrics
  are retired (D-0070 / D-0071) and retained only as the record
  of the mislabeling. Values still come from `git show` of those
  objects, never from the working tree.
- **New-schema pins:** the edges exhibit reports, per config,
  median / IQR / min of:
  - `E0→first-connect` — control, labeled as such
  - `E0→E4` — headline
  - `E2→E3g` — guest
  - `D_fin` (`d_fin_ns`) — delivery bound
  - `W` (`w_ns`) — guest-boot wait, Whimbrel-only decomposition
  - `D_ack` (`d_ack_ns`)
  - stamp overhead
  Never E0→E3w. Never E3w→E4. Never `e0_to_e4 − e0_to_e3w`.
- Working-tree `results/runs.csv` is still not an exhibit source.

### Cross-system tables (T4.8 / T4.9, and any table that has more
than one `system` value)

**No cross-system table may carry an E3w-derived column.** That
means none of: `e0_to_e3w_ns`, E0→E3w, E3w→E4, or any cell
computed from them. Under hostfwd those quantities are each
system's boot-to-listening time in disguise (hundreds of ms of
Linux, not delivery).

**`W` is not E3w-derived but is the same trap** — it is the
accepted connection waiting for the guest. It is a Whimbrel
decomposition column only. It does not appear next to a Linux or
Unikraft row.

**`E0→E4` is the comparison.** Two direct client-clock stamps.
Each system's boot is counted once, correctly.

**`e0_to_first_connect_ns` is a control, not a comparison.** If
Linux's value differs from Whimbrel's on the same QEMU/hostfwd
shape, that flags a broken run (listener-up is no longer
guest-independent, or the batch mixed QEMUs). It is not "Linux
connects slower." The generator omits it from comparison columns
and, if it is printed at all, labels it control.

`D_fin` may appear on a cross-system table only as the same pcap
definition (client FIN − HTTP frame) on both rows, never derived
from E3w. If a system's pcap shape does not have that FIN, the
cell is empty, not guessed.

## Bench-host spec (D-0062 / T4.8): `just linux-build` — implement there, not in this tree

Approved. The cloud pod has neither the disk nor the toolchain for
a buildroot build; the recipe runs on the dedicated host only and
never inside a batch. This section is the interface, same pattern
as D-0067 / D-0071.

### Inputs (committed under `bench/linux/`)

- `PIN` — buildroot point release, tarball sha256, and the kernel
  version that release pins. Committed before any build output is
  used (D-0062 amendment); the recipe verifies, it never records.
- `buildroot.fragment` — BR2 options on top of
  `qemu_riscv64_virt_defconfig` (musl toolchain, no busybox, no
  rootfs images).
- `linux-trimmed.fragment` — the kernel-config trim, one delta per
  line, each commented.
- `server.c` — `/init`.
- `initramfs.spec` — `gen_init_cpio` file list (deterministic:
  fixed mtime/uid/gid; `/init` plus `dev/console` c 5 1).

### Steps

1. Preflight, fail closed: bench host only, ≥ 35 GB free, host
   gcc/make present, network reachable. cpufreq boost stays off —
   a slower build is not worth toggling measurement discipline.
2. Download the pinned buildroot tarball; sha256 must match `PIN`.
   No trust-on-first-use inside the recipe: the recorded hash comes
   from the pin commit.
3. Buildroot tree (stock): `qemu_riscv64_virt_defconfig` +
   `buildroot.fragment`, **no kernel fragment**. Builds the
   toolchain and the stock-config kernel → `Image-stock`. Record
   which kernel config the pinned board uses; that config *is* the
   stock row's definition. If it ships virtio-net as `=m`, the
   one-line `=y` fragment is applied and the row is labeled
   "stock + virtio built-in" (D-0062 plan caveat).
4. Trimmed kernel from the same pinned kernel source, out-of-tree
   `O=` build with the tree's SDK cross-toolchain,
   `linux-trimmed.fragment` merged via `merge_config.sh` →
   `Image-trimmed`. **Fail on any merge override/redundancy warning
   not annotated in the fragment** — a symbol kconfig re-enables
   through dependencies is recorded in a fragment comment, never
   silently accepted. One buildroot tree plus one kernel build dir,
   not two buildroot trees.
5. `server.c` → static musl binary with the SDK toolchain; strip.
6. Build `usr/gen_init_cpio` from the kernel tree; assemble
   `rootfs.cpio` from `initramfs.spec`. Uncompressed.
7. Emit `bench/linux/MANIFEST` (committed): sha256 of
   `Image-stock`, `Image-trimmed`, `rootfs.cpio`, and the `/init`
   binary, plus the exact `-append` strings:
   - quiet: `console=ttyS0 quiet loglevel=0 rdinit=/init`
   - instrumented: `console=ttyS0 loglevel=7 printk.time=1
     initcall_debug rdinit=/init`
   The artifacts themselves are not committed (size); the MANIFEST
   is.

### Campaign-time rules

- The harness verifies MANIFEST sha256s before a batch and fails
  closed on mismatch. No rebuild inside a batch, ever — the
  Whimbrel analogue: build once per campaign, hash, verify.
- `runs.csv` `kernel_sha256` for Linux rows is the booted `Image`
  sha. The cpio sha and the `-append` string go in the batch header
  (`summary.txt`), like `s_ns`.
- Trial-time harness deltas (per-system argv/append/timeouts,
  per-system PHASE-line policy, the uniform client recv timeout,
  the SYN-grid and RST gates, the trimmed-vs-stock tripwire at
  summarize time — all pre-registered in the D-0062 amendment) are
  their own spec block, added with the gates step. They are not
  part of `linux-build`.

### Budget

Cold: toolchain ~30–60 min on this host (boost off), each kernel
~5–15 min; ~25 GB for the buildroot tree, ~4 GB for the trimmed
kernel build dir, 1–2 GB of tarball cache. Warm fragment
iterations are minutes. Nothing about batches changes to
accommodate the build; it is not on any measured path.

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
| `e0_wall_ns` | `time.time_ns()` at the same moment (diagnostic; never `pcap_epoch − e0_wall`) |
| `e0_to_first_connect_ns` | first successful `connect` − E0 (monotonic). Same-QEMU **control**, not a comparison: listener-up during netdev init. A deviation fails the run (D-0071). |
| `e0_to_e3w_ns` | **historical schema only** (through D-0068). first-connect + pcap-relative (HTTP − SYN/ACK). Retired: the anchor is false under hostfwd (D-0070). Absent from D-0071 batches. |
| `e0_to_e4_ns` | first response byte − E0 (monotonic). Headline. |
| `w_ns` | **D-0071.** pcap: guest SYN/ACK − first slirp ARP for 10.0.2.15. Guest-boot wait. Not a cross-system column. |
| `d_ack_ns` | **D-0071.** pcap: slirp pure ACK of payload+FIN − HTTP frame. |
| `d_fin_ns` | **D-0071.** pcap: client FIN − HTTP frame. Delivery bound. |
| `attempts` | client connect attempts until first-connect |
| `pcap_path` | repo-relative filter-dump path |

The summarizer refuses to aggregate if `dirty=1`, if `qemu_version` is not
unique, if `git_sha` is not unique, or if there are zero recorded rows.
New-schema batches are also refused if `e0_to_e3w_ns` is present, if any
of `w_ns` / `d_ack_ns` / `d_fin_ns` is missing, or if the first-connect
control or S profile-independence check fails (D-0071 spec above).

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
