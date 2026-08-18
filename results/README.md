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

## Bench-host spec (D-0071) — schema; writer is the T4.8 harness

The schema below is the interface. The T4.8 trial-time commit lands
the writer in `scripts/bench.py`; the dedicated host executes it
(D-0055: this pod does not run `just bench`).
`scripts/pcap_http.py` is the shared extract (`extract_pcap`);
`scripts/d0070-pcap-pass.py` imports it. A third copy of the
filters is a fail.

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

The first new-schema pin exists (`ffb7ac7`, T4.8). Schema is
detected from the CSV header, per pin. A reader that falls back
to `e0_to_e3w_ns` on a new-schema file is fail-open (same rule as
D-0067's `--after-batches`).

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
- **T4.8 pin:** `ffb7ac71234e953ae51339a3e1f5e17ba8c3f1b3`,
  batches `20260818T073023Z-1` / `20260818T073023Z-2`, measured
  kernel `1005399`. Cross-system table:
  `report/exhibits/cross-system.md`. New-schema Whimbrel edges
  append to `report/exhibits/edges.md`. **Frozen as the
  pre-FTRACE before (D-0073).** Do not retarget to T4.8b.
- **Linux decomposition pin:** `d705ecb8c67350519f9ce4653a4685a89e20e1d4`
  (`results/serial/` T4.8 batch-1 trial 4). **D-0072 label pin:**
  `93ab617676672f6db7a1d076389f9a049678192a`
  (`linux-trimmed-ignore-loglevel-20260818T084831Z-initcalls.txt`).
  Generated `report/exhibits/linux-decomposition.md`. Not a
  cross-system table. Diagnostic durations are UART-inflated
  labels for the 327 ms cell; they do not replace it. These
  pins stay the pre-FTRACE exhibit. T4.8b gets a new pin and a
  before/after exhibit when CSVs exist; do not overwrite this
  one.
- **T4.8b:** not a pin yet. Spec under D-0073 below. Same
  generator must not invent T4.8b cells from the working tree.

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
   `Image-trimmed`. **Three "not in final .config" cases**, not
   one: (1) fragment unset → final y is a survival (`# merge-override
   SYM:` or abort); (2) fragment unset → final absent is a vanished
   menu (success); (3) stock =y, symbol not in the fragment, final
   absent is a dependent drop (success, no annotation). **"Redefined
   by fragment" is informational.** D-0062 keeps (serial, virtio,
   IPv4 TCP, initramfs, DEVTMPFS, FUTEX) are asserted y on the
   final `.config` as their own check. One buildroot tree plus
   one kernel build dir, not two buildroot trees. D-0073: reuse
   is gated on a sha256 stamp of the fragment, not merely on
   `Image-trimmed` existing. After D-0073, stock hash must still
   be the T4.8 pin and trimmed hash must have moved (see T4.8b
   spec).
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

### What `linux-build` prints (merge verification)

The build is only trustworthy if its output shows the merge did
what the fragment intended. The recipe prints, in order:

1. **Pin echo:** buildroot release, tarball sha256 with `verified
   OK` against `PIN`, and the kernel version the tree pins.
2. **Raw `merge_config.sh` output** for the trimmed build,
   unfiltered. `linux-build` classifies that log:
   - `Value of CONFIG_X is redefined by fragment` (and
     `redundant by fragment`) — informational. The fragment
     changed (or restated) stock. Every trim line that does work
     emits this. No annotation.
   - `Value requested for CONFIG_X not in final .config` —
     merge_config diffs concatenated stock+fragment against the
     final `.config`, so this is not "what the fragment asked."
     Three cases, discriminated on whether X is a kconfig line
     in `linux-trimmed.fragment`:
     1. Fragment requested unset → final =y — real survival.
        Abort unless `# merge-override SYM:`. (EFI)
     2. Fragment requested unset → final absent — menu vanished.
        Success.
     3. Requested =y → final absent, X **not** in the fragment —
        dependent drop from a parent we unset (SCSI_MOD after
        BLOCK, NFS_FS after NETWORK_FILESYSTEMS, USB_XHCI_HCD,
        SND_PCM, RTC_LIB, SECURITY_SELINUX, …). Success,
        informational, no annotation. Summarized as a count,
        not 300 FAIL lines.
     Requested =y → final absent, X **is** in the fragment: a
     keep we asked for is gone. Abort. The D-0062 keeps list is
     a separate check on the final `.config` (block 3c), not
     this cascade.
   Intent notes (`# FTRACE:`) are not merge-overrides and do
   not put a symbol in the fragment.
3. **Requested-vs-final table:** every line of
   `linux-trimmed.fragment` against the trimmed final `.config` —
   `CONFIG_X: requested y, final y` / `requested unset, final
   unset` — with per-line PASS/FAIL. Any FAIL whose symbol is not
   a `# merge-override SYM:` line (a dependency re-enable) aborts
   the build:
   `TEST FAIL: merge override not annotated: CONFIG_X requested
   unset, final y`. This is case 1 on the fragment lines (a
   symbol we asked to unset that came back). Dependent drops of
   symbols we never named are not this table.
3b. **D-0073 leftovers must not be y** (FTRACE, NFS, USB, …).
   Absent/unset is a pass.
3c. **D-0062 keeps must be y** on the final `.config`: serial
   (`TTY`, `SERIAL_8250`, `SERIAL_8250_CONSOLE`,
   `SERIAL_OF_PLATFORM`, `PRINTK`), virtio-mmio/net
   (`NETDEVICES`, `VIRTIO_MENU`, `VIRTIO_MMIO`, `VIRTIO_NET`),
   IPv4 TCP (`NET`, `INET`), initramfs (`BLK_DEV_INITRD`,
   `BINFMT_ELF`), `DEVTMPFS`, `FUTEX`. A cascade that removes
   one of these is a lost keep, caught here, not by annotating
   SCSI_MOD.
4. **Config diff summary:** the kernel tree's `scripts/diffconfig`
   between the stock final `.config` and the trimmed final
   `.config` — the complete end-to-end delta, intended trims plus
   dependency fallout, so the reviewer sees what the trim actually
   changed rather than what the fragment asked for.
5. **Artifact table:** path, byte size, sha256 for `Image-stock`,
   `Image-trimmed`, `rootfs.cpio`, `init`, then the MANIFEST
   contents just written.

A build that cannot print all five blocks is a failed build.

### Campaign-time rules

- **Hash verification, fail-closed shape:** at batch start, before
  the first warmup trial boots, the harness hashes every Linux
  artifact the batch will use and compares against the committed
  MANIFEST. On any mismatch:

  ```
  TEST FAIL: linux artifact mismatch: bench/linux/artifacts/Image-trimmed
    sha256=<actual> want <MANIFEST value>
  ```

  The batch does not start — no trial boots, no CSV row is
  written. Not a warning, not a per-trial skip, and **never** an
  automatic rebuild: a rebuild inside a batch is the silent-refresh
  path fail-open harnesses die of (finding 31). The operator reruns
  `just linux-build` (a new MANIFEST is a new commit) or restores
  the artifact. A missing MANIFEST, a missing artifact file, or an
  empty one fails the same way.
- Belt and braces at read time: `runs.csv` `kernel_sha256` must
  equal the MANIFEST value for that row's config; the summarizer
  and exhibit generator fail closed on disagreement, so a row from
  a stale artifact cannot survive into an exhibit even if it was
  somehow recorded.
- `runs.csv` `kernel_sha256` for Linux rows is the booted `Image`
  sha. The cpio sha and the `-append` string go in the batch header
  (`summary.txt`), like `s_ns`.
- Trial-time harness deltas live in **Bench-host spec (T4.8
  trial-time / Linux gate)** below. They are not part of
  `linux-build`.

### Budget

Cold: toolchain ~30–60 min on this host (boost off), each kernel
~5–15 min; ~25 GB for the buildroot tree, ~4 GB for the trimmed
kernel build dir, 1–2 GB of tarball cache. Warm fragment
iterations are minutes. Nothing about batches changes to
accommodate the build; it is not on any measured path.

## Bench-host spec (T4.8 trial-time / Linux gate)

Approved. Code lives in this tree (`scripts/bench.py`,
`scripts/bench-client.py`, `scripts/qemu-args.sh`,
`scripts/pcap_http.py`, `scripts/linux-boot-test.sh`,
`scripts/assert-pcap-syn-grid.sh`). The dedicated host executes
it. This pod does not run `just bench` or `just linux-build`.
`just test-linux` fail-closes here if `bench/linux/artifacts/`
and `bench/linux/MANIFEST` are missing — that is the correct
shape, not a skip.

### What `bench.py` changes

**Per-system QEMU argv** on top of the shared `qemu-args.sh` base
(finding 28: the base is the only copy of machine/netdev/device).
Whimbrel extra: `-kernel <elf>` only. Linux extra: `-kernel
<Image> -initrd <rootfs.cpio> -append <cmdline>`. Whimbrel takes
none of `-initrd` / `-append`. Cmdlines are the D-0062 pins,
required to match the committed MANIFEST:

- quiet: `console=ttyS0 quiet loglevel=0 rdinit=/init`
- instrumented: `console=ttyS0 loglevel=7 printk.time=1
  initcall_debug rdinit=/init`

**Per-system QEMU wait** (hang watchdog). Linux boots slower;
killing QEMU at the Whimbrel budget would abort a healthy stock
row. Floors: Whimbrel 12 s, Linux 60 s. The wait is
`max(campaign_timeout, system_floor) + 2`. It is not a
measurement window and not a client-recv knob.

**One uniform client recv timeout**, equal to the campaign
`BENCH_TIMEOUT_S`, **identical for every system**. Not a
per-system knob — that is exactly the asymmetry that hides a
confound (a 2 s Linux recv next to a 12 s Whimbrel recv would
censor slow Linux trials and leave the median looking fine).
`scripts/bench-client.py` uses `--timeout-s` for `recv` after
connect, not a hardcoded 2.0 s. Mixed T4.8 campaigns default
`BENCH_TIMEOUT_S=60` (stock orientation 2–20 s); Whimbrel-only
campaigns stay at 12. Raising the campaign timeout lengthens
every arm's recv budget together.

**PHASE-presence is gated on `system`.** `parse_phases` remains
fail-closed for Whimbrel (no rows / missing E3g / unset /
sum-to-E3g). Linux serial has no `PHASE` lines; an empty phase
list is success, not `TEST FAIL: no PHASE rows`. Linux trials
write no `phases.csv` rows. `LINUX INIT OK` (and `READY`, and no
`INIT FAIL:` / `Kernel panic`) is the Linux serial gate.

**D-0071 schema lands in the same writer.** Drop `e0_to_e3w_ns`.
Record `w_ns` / `d_ack_ns` / `d_fin_ns` via `pcap_http.extract_pcap`
(the D-0070 filters, one pcap clock). Mixed schema in one
`runs.csv` is a fail. Computing E3w for Linux would recreate the
D-0070 trap; omitting the column for Linux only would mix
schemas. `W` is recorded on every row that has the slirp ARP
(extract is fail-closed on a missing ARP for every system) and
is still not a cross-system table column.

**Campaign kinds.** `just bench-whimbrel` is unchanged (two
Whimbrel arms). `just bench-t48` is the five-arm interleaved
campaign: `release-fast-boot`, `release-default`, `trimmed`,
`stock`, `trimmed-instrumented`. Linux artifacts are hashed
against `bench/linux/MANIFEST` at batch start, before any boot
(same fail-closed shape as linux-build's campaign-time rule).

**Summarize-time gates** (fail the run, not a fraction):

- First-connect control (D-0071): per batch, every arm's median
  `e0_to_first_connect_ns` within 1 ms. A miss is a broken
  control, not "Linux connects slower."
- Trimmed-vs-stock tripwire (D-0062): per batch, if median
  E0→E4(trimmed) ≥ median E0→E4(stock), `TEST FAIL` and the
  trimmed row is not published.
- S in the batch header, not a CSV column (D-0071). `|s_fast −
  s_safe| > 1 ms` fails. Computed at trial time from extract
  internals (`synack_to_http_ns` is not a CSV column).

### What `runs.csv` gains

No new per-trial columns beyond the D-0071 set. The Linux rows
fill columns the Whimbrel writer already had:

| field | Linux row |
|---|---|
| `system` | `linux` (Whimbrel rows stay `whimbrel`) |
| `config` | `trimmed` / `stock` / `trimmed-instrumented` |
| `kernel_sha256` | sha256 of the booted `Image` (not the cpio, not `/init`) |

**Batch header** (`summary.txt`), same grain as `s_ns` — not
CSV columns:

```
cpio=<path>
cpio_sha256=<64 hex>
linux_append_quiet=console=ttyS0 quiet loglevel=0 rdinit=/init
linux_append_instrumented=console=ttyS0 loglevel=7 printk.time=1 initcall_debug rdinit=/init
client_timeout_s=<BENCH_TIMEOUT_S>
```

A per-trial cpio or `-append` column would invite median / IQR
treatment of a campaign constant. Header only.

### Shared virtio-net-device args (`csum=off` / TSO-family off)

In `scripts/qemu-args.sh` only (finding 28), on every consumer
of that file:

```
-device virtio-net-device,netdev=net0,csum=off,guest_csum=off,gso=off,guest_tso4=off,guest_tso6=off,guest_ecn=off,guest_ufo=off,guest_uso4=off,guest_uso6=off,host_tso4=off,host_tso6=off,host_ecn=off,host_ufo=off,host_uso=off
```

A no-op for Whimbrel (never negotiates those features). Prevents
Linux TX checksum offload from leaving invalid checksums in the
capture, which would fail the HTTP checksum assert and silently
invalidate a pcap-based `D_fin`. The T4.6 / D-0068 pins stay on
their recorded objects and old argv. USO is grouped with the TSO
family: QEMU 10 defaults `guest_uso4` / `guest_uso6` / `host_uso`
on, and the same pcap-corruption path applies. `.cargo/config.toml`
runner and `scripts/measure-e2.sh` take the same device string
(they are not a fifth copy of the offload defaults).

### Linux boot gate (`just test-linux`)

Analogous to `just test` for Whimbrel. Not folded into `just test`
— this pod has no Images, and a missing-artifact skip inside the
sixteen-gate list would be fail-open. The recipe fail-closes.

Boot `Image-trimmed` + `rootfs.cpio` + quiet `-append` on the
shared QEMU argv. The measurement client starts before QEMU
(same `CLIENT_EARLY` shape), recv timeout = `TIMEOUT_S` (default
60), not curl `--max-time 2`.

Pass only if all of:

1. Serial contains `READY` and `LINUX INIT OK`; no `INIT FAIL:`;
   no `Kernel panic`; QEMU exits (poweroff), not a hang.
2. Client receives the byte-identical 92-byte `RESP` (same bytes
   as `app/src/lib.rs`).
3. `assert-pcap-http.sh`: HTTP 200, `Connection: close`, `tcp.len`
   92, checksums good, guest FIN, peer FIN, **zero RST**.
4. SYN-grid (confound A) and RST (confound B) below.

Missing MANIFEST / Image / cpio / hash mismatch: `TEST FAIL:
linux artifact missing` (or the campaign-time mismatch shape),
no QEMU, no skip.

### SYN-grid gate (confound A) — one gridded trial fails the batch

Pre-registered in the D-0062 amendment. **One failure fails the
batch**, not a reported fraction. A batch with 10% gridded trials
has a median that looks fine and a poisoned mean; bimodal
contamination must not hide behind a median. No Linux row
publishes from a batch with a gridded trial.

Per Linux trial (warmup included), from the pcap:

- Guest first TX: first frame whose `eth.src` is not slirp
  (`52:55:0a:00:02:02`). The invariant is first wire TX, not ARP
  (D-0062 announce).
- SYN into guest: first SYN to `10.0.2.15:80` with timestamp
  **≥** t(guest first TX) — the flush after the guest appears,
  not an earlier slirp probe that sat on the virtual wire.
- Gate: `0 ≤ t(SYN) − t(guest first TX) < 1 ms`.

If SYN arrival snaps to a ≥ 1 s grid, the trial is measuring
slirp's RTO. Response when fired: diagnose in the pcap (is the
announce present? did the SYN snap to a retransmit grid?) before
any rerun.

`scripts/assert-pcap-syn-grid.sh` is the per-pcap assert.
`bench.py` calls it on every Linux trial and aborts the campaign
on the first miss (no remaining trials, no partial CSV publish).

### RST gate (confound B)

Zero RST frames (`tcp.flags.reset==1`) in every Linux pcap, same
shape as the Whimbrel `assert-pcap-http.sh` RST check. Any RST
fails the run. Response: `/init` ordering regression or an
unexpected early connection; diagnose, fix, rerun.

### MANIFEST format (campaign-time reader)

`bench/linux/MANIFEST`, written by `just linux-build`, parsed by
the harness. Comments `#`. Lines:

```
artifact Image-stock <64 hex>
artifact Image-trimmed <64 hex>
artifact rootfs.cpio <64 hex>
artifact init <64 hex>
append quiet console=ttyS0 quiet loglevel=0 rdinit=/init
append instrumented console=ttyS0 loglevel=7 printk.time=1 initcall_debug rdinit=/init
```

Files live at `bench/linux/artifacts/<name>`. Append strings that
disagree with the D-0062 pins fail closed. This file is not
committed until linux-build has run on the bench host.

## Bench-host spec (D-0072): one `ignore_loglevel` boot to name the 327 ms hole

Not a campaign. Not a sixth arm. Not an E0→E4 row. A **labeling
pass** for gap 1 of
`report/exhibits/linux-decomposition.md`. The dedicated host
executes it (`just linux-initcall-label`). This pod fail-closes
without `bench/linux/artifacts/`. Results never enter
`runs.csv` / `phases.csv` / a cross-system table.

The T4.8 instrumented serial already measured the hole (327 ms
between `dns_resolver registered` and `clk: Disabling unused
clocks` under `loglevel=7`). This boot exists because
`initcall_debug` printed nothing there. Two factors, this order
(D-0072):

1. **`loglevel=7` filters `KERN_DEBUG`** — necessary and
   sufficient for zero initcall lines. Linux 6.18 emits
   `calling  %pS` / `initcall %pS returned %d after %lld usecs`
   at `KERN_DEBUG`. Console prints levels strictly below
   `console_loglevel`; debug is 7.
2. **`# CONFIG_KALLSYMS is not set`** affects **names only**.
   `%pS` still prints the pointer (`PM: Calling 0xffffffff800614ec`
   in the T4.8 log). Kallsyms would name it; it would not have
   created the missing lines.

A kallsyms-enabled Image is a different binary than the trimmed
row. Do not build one for this pass.

### Cmdline (exact)

Same `Image-trimmed` as T4.8 (MANIFEST `artifact Image-trimmed`).
The **only** delta from the instrumented MANIFEST append is
`ignore_loglevel`:

```
console=ttyS0 loglevel=7 printk.time=1 initcall_debug ignore_loglevel rdinit=/init
```

Do not drop `loglevel=7`. Do not substitute `loglevel=8` on the
host — `ignore_loglevel` is the stated knob (it ignores the
console loglevel entirely, which is what makes `KERN_DEBUG`
visible). Do not add this string to MANIFEST as a third `append`
line; it is not a campaign config.

### What to boot

One boot. `linux-boot-test.sh` shape so `/init` still runs the
measured path (client started before QEMU, SYN-grid, no RST,
`READY`, 92-byte RESP, `LINUX INIT OK`, QEMU exit 0):

- `-kernel bench/linux/artifacts/Image-trimmed`
- `-initrd bench/linux/artifacts/rootfs.cpio`
- `-append` the cmdline above
- shared `qemu-args.sh` (`csum=off` / TSO-family off)
- `TIMEOUT_S=60` (same floor as `just test-linux`); if this boot
  times out, raise once and record the new budget in the serial
  header — extra `KERN_DEBUG` UART is expected to inflate wall
  time. That inflation is why this boot's initcall **durations
  do not replace** the 327 ms.

Fail closed **before** QEMU:

- `sha256(Image-trimmed)` ≠ MANIFEST `artifact Image-trimmed`
- missing `rootfs.cpio` / `init` / MANIFEST
- missing `System.map` (see below)

Fail closed **after**:

- zero lines matching `initcall .* returned .* after .* usecs`
  (`ignore_loglevel` did not take, or this is the T4.8 log
  again)
- `INIT FAIL:` / `Kernel panic` / missing `LINUX INIT OK`
- any unresolved initcall address against `System.map`

Do not write `results/runs.csv`. Do not invent a `config=` value.
`just bench-t48` stays five arms.

### `System.map` — offline resolution

`System.map` is a **build sidecar**, not a boot artifact. It is
not in MANIFEST and not in git (`bench/linux/artifacts/` and
`build/` are gitignored). It must come from the same trimmed
`O=` build that produced this `Image-trimmed`.

Search order:

1. `bench/linux/artifacts/System.map-trimmed` (copied by
   `just linux-build` when it produces `Image-trimmed`)
2. `bench/linux/build/linux-trimmed/System.map` (the `O=` tree)

If neither exists: `TEST FAIL: System.map-trimmed missing`. Do
**not** rebuild the Image to recover the map — a new Image is a
new hash and is not the T4.8 binary. Recover the map from the
build tree that already hashed to MANIFEST, or stop.

`just linux-build` copies `O=.../System.map` to
`artifacts/System.map-trimmed` whenever it writes
`Image-trimmed`. The reuse-skip path copies it if the `O=` file
is still there and the artifact is missing.

### How the resolver works

`scripts/label-linux-initcalls.py` reads the diagnostic serial
and `System.map`. It does not read `runs.csv`.

**Parse** (CRLF-tolerant), Linux 6.18 `trace_initcall_finish_cb`:

```
initcall 0xffffffff8xxxxxxx returned <ret> after <usecs> usecs
```

`%pS` without kallsyms is a hex pointer, with or without `0x`.
Ignore `calling  … @ <pid>` for ranking; the `returned` line
carries the duration. `entering initcall level: …` is a
breadcrumb, not a cost.

**Resolve.** `System.map` lines are `<hex> <type> <name>`
(no `0x`). For each initcall address A, take the greatest map
address ≤ A (the same nearest-symbol rule kallsyms uses). Report
`name+0xOFF`. Offset ≥ 64 KiB is `TEST FAIL` (wrong map for this
Image). Any address below the map's first symbol is unresolved
and fails the run.

**Hole window.** The T4.8 anonymous region is the printk span
from `Key type dns_resolver registered` to
`clk: Disabling unused clocks`. Those two lines are `KERN_INFO`
and still appear under `ignore_loglevel`. Initcalls whose
**finish** timestamp falls in `(t_dns, t_clk]` are the labels
for gap 1. Extra debug UART will **widen** that span on this
boot; that is expected. Use the names and the ranking **inside
the window**. Do not copy this boot's microseconds into the
327 ms cell.

**Outputs** (committed later, not by this spec's first run
unless the operator chooses):

```
results/serial/linux-trimmed-ignore-loglevel-<UTC>.log
results/serial/linux-trimmed-ignore-loglevel-<UTC>-initcalls.txt
```

The `.txt` header records Image sha256, System.map sha256, the
exact cmdline, and QEMU version. The body is two tables: (1)
hole-window initcalls sorted by `usecs` descending, (2) all
initcalls the same way, captioned UART-inflated / not a report
number.

The exhibit generator reads the label pin
(`93ab617676672f6db7a1d076389f9a049678192a`) via `git show`.
Diagnostic microseconds annotate gap 1 of
`report/exhibits/linux-decomposition.md`; they do not replace the
327 ms T4.8 cell.

## Bench-host spec (D-0073 / T4.8b): fragment change + `just linux-build` + five-arm re-run

Approved. This pod does not run `just linux-build` or `just
bench-t48`. The dedicated host executes the sequence below. Read
the projection **before** the rebuild starts; do not invent a
quiet-row saving from the diagnostic microseconds after the fact.

T4.8 stays the before (`ffb7ac7`, serial `d705ecb`, labels
`93ab617`, `Image-trimmed` `fe821d1d…`, `Image-stock`
`fa0f4315…`). T4.8b is the after. PLAN T4.9 remains Unikraft.
Do not retarget the T4.8 pins. Do not overwrite
`report/exhibits/linux-decomposition.md` with T4.8b numbers; the
before/after *is* the finding.

### Pre-registered projection (quiet-row `trimmed` E0→E4)

T4.8 trimmed median is **759.79 ms** (IQR 2.61 ms).
`trace_eval_sync` is **222.6 ms UART-inflated** on the
`ignore_loglevel` boot.

**Named fixed component** mixed into that 222.6 ms: ignore_loglevel
UART (console drain of this initcall's `KERN_DEBUG` lines and any
nested printk) plus TCG occupancy from the rest of that noisy
boot. That component is **not** in quiet E0→E4 (`loglevel=0`).
The eval-map walk itself is real TCG compute; the split is
unmeasured.

**Refuse** the point prediction `759.79 − 222.6 = 537.19 ms`.
D-0069: 222.6 ms is a diagnostic wall time, not a quiet-row
saving. Do not sum the other diagnostic usecs (pty 33.8, alsa
16.5, mousedev 13.8, watchdog 8.0, nfs, …) onto 222.6 and
subtract either.

**Orientation range (not a falsifier):** T4.8b trimmed E0→E4
**540–740 ms**. Low end = diagnostic usecs were almost all real
compute (D-0069-unpadded, likely too fast). High end = almost
all of 222.6 was UART and the other unsets are also small on
the quiet row. Expected: a clearly detectable drop vs 759.79,
not 222.6 ms on the nose.

**Falsifiers (load-bearing; stop, do not publish a saving):**

1. T4.8b trimmed E0→E4 ≥ 759.79 ms — no improvement. Diagnose
   `trimmed.config` (`FTRACE` still y?) first.
2. T4.8b trimmed ≥ T4.8b stock (existing D-0062 tripwire).
3. `Image-trimmed` sha256 still
   `fe821d1d5fcc0c8d4474504c48d3024e0991c37ba74d40c675a0158b61e44fa2`
   — rebuild skipped.
4. `Image-stock` sha256 ≠
   `fa0f4315766866e7ce02e15f7bda78fdb73da69d4b9c8ae4f156b769a25eaf62`
   — stock moved; T4.8 is no longer the before.
5. SYN-grid / RST / first-connect / `LINUX INIT OK` as T4.8.

### Fragment (already in tree)

`bench/linux/linux-trimmed.fragment` D-0073 section. Parents
only (`NETWORK_FILESYSTEMS`, `USB_SUPPORT`, `SOUND`, `MMC`,
`RTC_CLASS`, …). Do not also unset children in the fragment
(noise, and merge_config `grep -w` can match a parent prefix).
`linux-build` block 3b still asserts the children are not y.

Keeps: `SERIAL_8250`, `SERIAL_OF_PLATFORM`, virtio-mmio/net,
IPv4 TCP, initramfs, `DEVTMPFS`, `FUTEX`, `MODULES=y`,
`DEBUG_KERNEL` (cut the child `FTRACE`, not the parent default).

Likely merge-override stickers: **ACPI**, **HID**. Annotate if
they come back (`# merge-override SYM: …`). Do not silently
accept. `FORCE_TRIMMED_REBUILD=1` does not override that.

### `just linux-build` deltas vs T4.8

Reuse of `Image-trimmed` is gated on
`bench/linux/build/trimmed.fragment.sha256` matching
`sha256(linux-trimmed.fragment)`, plus the Image / `.config` /
`merge_config.out` existing. `FORCE_TRIMMED_REBUILD=1` forces
a rebuild. Existence of `Image-trimmed` alone is not enough
(that is the skip that would have left `fe821d1d…` in place).

**Do not rebuild `Image-stock`.** The T4.8 version string is
dated (`#1 Tue Aug 18 02:43:03 EDT 2026`). If
`bench/linux/artifacts/Image-stock` is missing, restore it from
the T4.8 artifact; a fresh stock compile will fail the hash
pin even with an identical config. `linux-build` reuses stock
when the file exists, then **FAIL**s if its sha256 is not
`fa0f4315…`, and **FAIL**s if trimmed is still `fe821d1d…`.

Block 3b (`D-0073 leftovers must not be y`) runs after
requested-vs-final. Absent/unset is a pass (menu vanished).

Do not rewrite MANIFEST hashes in this tree until this build
has run. After it runs, commit the new MANIFEST (trimmed hash
will move; stock must not). Artifacts stay gitignored.

### Second-look (after merge, before the campaign)

Walk the **actual** `bench/linux/build/trimmed.config`. This
pod reconstructed candidates from printk; kconfig is the
arbiter. If a T4.8-printk leftover is still y and unused by
`/init`, amend the fragment, annotate any merge-override, and
`FORCE_TRIMMED_REBUILD=1 just linux-build` **before**
`just bench-t48`. Finding leftovers one N-trial later is the
thing this pass exists to avoid.

Grep at least: `FTRACE`, `NFS`, `9P`, `USB`, `SOUND`/`SND`,
`MMC`/`SDHCI`, `MOUSEDEV`, `HUGETLB`, `AUDIT`, `SUNRPC`,
`ACPI`, `PNP`, `RTC`, `WATCHDOG`, `BPF_SYSCALL`. The deferred
list in the fragment (VT, DEBUG_FS, FB, PINCTRL, I2C/SPI/GPIO,
thermal, cpuidle, LSM) is named, not forgotten: unset in this
pass only if the second-look shows y *and* obviously unused
*and* not a no-boot risk.

The T4.8 hole window (`dns_resolver` → `clk-disable`) may move
or vanish once NFS / DNS_RESOLVER go away. That is T4.8b
labeling, not a retarget of the T4.8 exhibit.

### Sequence on the bench host

1. Confirm `sha256sum bench/linux/artifacts/Image-stock` is
   `fa0f4315…`. Restore, do not rebuild, if it is not.
2. `just linux-build` (fragment stamp change rebuilds trimmed;
   `FORCE_TRIMMED_REBUILD=1` if an old Image is sitting next to
   a hand-written stamp). Read the five verification blocks
   plus 3b. `TEST PASS: linux-build` is required.
3. Confirm `Image-trimmed` ≠ `fe821d1d…` and `FTRACE` is not y
   in `trimmed.config`.
4. Second-look `trimmed.config` (above). Amend+rebuild if needed.
5. `just test-linux image=trimmed` and `just test-linux image=stock`.
   Same boot gate as T4.8 (`LINUX INIT OK`, 92-byte RESP,
   SYN-grid, no RST, QEMU exit 0).
6. Commit the new `bench/linux/MANIFEST` (and only that hash
   change for Linux artifacts). Do not retarget exhibit pins.
7. `just bench-t48` — same five arms as T4.8
   (`release-fast-boot`, `release-default`, `trimmed`, `stock`,
   `trimmed-instrumented`). Interleaved, n=60 recorded, two
   batches, same host controls. This *is* T4.8b; the recipe
   name is unchanged.
8. Commit T4.8b CSVs / serials. New pin. Do **not** change
   `T48_REV` / `SERIAL_REV` / `LABEL_REV` in
   `scripts/report-exhibits.py`. T4.8b gets its own pin and a
   before/after exhibit when those objects exist. Do not
   generate T4.8b table cells by hand.
9. Optional: one `ignore_loglevel` boot of the **new**
   `Image-trimmed` to confirm `trace_eval_sync` is gone. New
   label pin, new System.map, still not a sixth arm, still not
   a replacement of `93ab617`.

### What this pod already did

Fragment + `linux-build` reuse/hash gates + D-0073 + this spec
+ the T4.8 exhibit frozen as the before. No Image, no T4.8b
CSV, no MANIFEST rewrite.

## `runs.csv` — one row per trial


| column | meaning |
|---|---|
| `batch_id` | UTC stamp + batch index (`20260816T090000Z-1`) |
| `trial` | per-config 1-based index (warmup 1..W, recorded W+1..W+N). Wall-clock order is `run_order`, not this column. |
| `warmup` | `1` for the first 3 trials of that config in the batch (round-robin warmup), else `0` |
| `system` | `whimbrel` or `linux` |
| `config` | Whimbrel: `release-default` / `release-fast-boot` / `release-fast-boot-nofp`. Linux: `trimmed` / `stock` / `trimmed-instrumented`. |
| `git_sha` | `git rev-parse HEAD` |
| `dirty` | `1` if `git status --porcelain` is non-empty |
| `kernel_sha256` | SHA-256 of the guest binary this trial booted (Whimbrel ELF; Linux `Image`) |
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
| `system` | `whimbrel` (Linux trials write no phase rows) |
| `config` | same as runs |
| `phase` | name from the `PHASE` line (`_start`, `stamp_a`, `activate`, `net_init_done`, `syn_rx`, `E3g`, …) |
| `ticks` | guest `rdtime` |
| `ns_since_e2` | ns since `_start` (E2); 100 ns/tick |
| `delta_ticks` | ticks since the previous stamp in serial order |
| `delta_ns` | that delta × 100 |
| `source` | `serial` (room for a future instrument without new columns) |

A `PHASE` line that does not match the machine-shaped regex, or `PHASE
<name> unset`, fails the trial. Linux trials write **no** phase
rows; the PHASE-presence check is gated on `system=whimbrel`
(T4.8 trial-time spec).
