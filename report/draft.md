# Floor-finding: boot to first HTTP byte on a RISC-V unikernel

Draft-early skeleton (T4.3 / D-0064; T4.4 and T4.6 ladder rows
filled). Every quantitative claim in Results is generated from CSV
by `scripts/report-exhibits.py`. Regeneration: `just report-exhibits`.
Do not type table cells.

The harness overwrites `results/runs.csv` and `results/phases.csv` per
run. The exhibit generator therefore reads two git objects, not the
working tree: **baseline columns** from tag `baseline-t4.3` (measured
kernel `35861f3`), **after-ladder and Δ** from `HEAD` (superpages,
measured kernel `76830e13`). See [exhibits/phase-decomposition.md](exhibits/phase-decomposition.md)
caption and D-0067.

Conditions, stated once: QEMU TCG, `virt` machine, `-bios default`,
slirp as the TCP peer, dedicated Ubuntu 26.04 host, boost off. Not
hardware time.

---

## Abstract

*(stub — fill at T4.11)* Under QEMU TCG on a dedicated host, a
minimal rv64gc unikernel reaches first HTTP byte in a measured
E2→E3g median of the fast-boot configuration reported in
[exhibits/edges.md](exhibits/edges.md). After T4.6 the kernel
profile is flat: no phase exceeds 19% of 6.43 ms. The T4.3 freeze
had been two walks of the frame free list; T4.4 collapsed those;
T4.6 mixed-granularity paging took the next 42%. Safe vs fast is
still a large factor. Cross-system rows (Linux, Unikraft) are empty
until T4.8/T4.9. The largest single term in the honest E0→E4
number is host-side (E3w→E4; D-0066); dump placement is D-0068.

---

## Background

*(stub)* Whimbrel is a single-hart, single-address-space rv64gc
unikernel: OpenSBI, Sv39, one U-mode app, five syscalls, virtio-net,
HTTP/1.0 one-shot. Built to be explained, then measured. Related
work (unikernels, TCG vs silicon, boot-time literature) belongs here
at T4.11, not as a literature dump that outruns the apparatus.

---

## Architecture of the apparatus

*(stub — distill DECISIONS.md)* The measurement consequence of the
deliberate U/S split (D-0008/D-0020): a real `sret` and a real
syscall boundary are in the flagship number, unlike a pure M-mode
toy. TCP is HTTP/1.0 one-shot, no congestion control, no
reassembly beyond one segment (D-0053); that omission is invisible
at this workload because the client sends one GET and the response
is 92 bytes. One application compiled in; no POSIX, no FS, no
dynamic loading. The kernel is the apparatus; this section exists
so a reader can see what was *not* running when the byte left.

---

## Methodology

Protocol: D-0055. Edges: D-0043, with the E3w→E4 remainder diagnosed
in D-0066. Client: persistent process, retry started before E0;
measured granularity in the machine-spec block. Pinning: `taskset`,
QEMU and client on separate cores. Stamp overhead: two adjacent
stamps at boot (`stamp_a`, `stamp_b`), quoted against every
attributed delta. Statistics: median and IQR; min shown as the
observed floor bound; means never. Stability: two interleaved
30-trial batches, per-metric medians within max(2%, 200 µs) for
every metric ≥ 1 ms. The criterion passed on this host for both
configs on the freeze, T4.4, and T4.6; it failed on the KVM pod,
so that pod's numbers are not cited here.

**Baseline freeze.** Tag `baseline-t4.3` (CSV freeze commit `bce55a2`).
Measured kernel git SHA `35861f3`. Batches `20260817T041311Z-1` and
`20260817T041311Z-2`. The machine-spec baseline block is copied
verbatim from `git show baseline-t4.3:results/baseline-summary.txt`
into [exhibits/machine-spec.md](exhibits/machine-spec.md) by
`just report-exhibits`.

**T4.4.** Batches `20260817T052349Z-1` and `20260817T052349Z-2`,
measured kernel `83ca9f9`. Kept as the pre-superpage pin; not the
after-ladder columns.

**T4.6 after-ladder (superpages).** Batches `20260817T061753Z-1`
and `20260817T061753Z-2`, measured kernel `76830e13`, sourced from
`git show HEAD:results/{runs,phases}.csv`. Machine-spec fields come
from those CSV rows, not from `results/summary.txt`.

Host-control asserts (virt / governor / SMT / boost / steal) fail
closed at batch start. Boost-off is a dedicated-host override of
D-0055's original runs-anywhere alternative: peak clock 4.2 GHz vs
5.05 GHz (~17%), so absolute numbers are larger and boost-state /
thermal variance are removed. All compared systems run on this host
under the same policy; comparisons are unaffected; only the
absolute floor moves.

**E4 is not quantized by the 1 ms client cadence.** That cadence is
the connect-retry loop only. After `connect()` succeeds the client
`sendall`s the GET and blocks in `recv`; `first_byte_mono_ns` is the
first nonempty chunk. E3w is first-connect plus the pcap-relative
SYN/ACK→HTTP interval (filter-dump wall ≠ Python realtime, D-0043).
E3w→E4 is therefore the time from the HTTP frame appearing in the
filter-dump to Python `recv` — slirp/hostfwd + host TCP + client
read, plus any QEMU occupancy after the guest has already published
(D-0066). It is the largest term in honest E0→E4 after T4.4. The
same QEMU user-net and the same client are used for Linux and
Unikraft, so the shared conduit does not by itself distort
comparisons; Whimbrel additionally dumps the PHASE table over DBCN
after publish (`print_after_response`), which occupies TCG on the
same thread that pumps slirp and is a Whimbrel-only extra. That
dump is designed to move off the publish→E4 path (D-0068, yield
then dump, same boot) before the Linux baseline; it is not the
superpage rung.

Exhibit tables: [phase decomposition](exhibits/phase-decomposition.md)
(D-0064 centerpiece columns) and [edges](exhibits/edges.md).

---

## Results

The centerpiece is [exhibits/phase-decomposition.md](exhibits/phase-decomposition.md).
Host-observed edges: [exhibits/edges.md](exhibits/edges.md). Figures
in the findings below are those generated tables, in prose.

### Two walks, one root cause (T4.3 freeze)

On release+fast-boot, `frame_init` (7.20 ms, 34% of E2→E3g) and
`accounting` (4.79 ms, 22%) were 56% of boot-to-publish (11.99 ms of
21.42 ms). They were the same underlying cost: two separate walks of
~31k frames. The first linked remaining RAM into the free list at
`frame::init`; the second was `free_count()` before freeze. Bump /
lazy free-list (T4.4) subsumed O(1) accounting. D-0060 is
declined-by-subsumption. Paging was not the dominant kernel term
on the freeze; T4.4 made it 42% of what remained; T4.6 took that.

### Verification costs more than the thing verified

`page_verify` is 2.57 ms on the freeze, 2.39 ms after T4.4, 731 µs
after T4.6; `page_build` is 1.45 ms through T4.4 and 386 µs after
T4.6 — still ~1.9×. The second walk of the identity map, kept
deliberately (D-0043), is more expensive than constructing the map
at every rung that has measured it. Combined paging 3.84 ms = 42%
of T4.4 fast E2→E3g was the re-evaluation condition for superpages
(D-0059). After T4.6 the pair is 1.12 ms = 17% of 6.43 ms.

### Safe vs fast is still a large factor, concentrated

Release-default E2→E3g was 94.88 ms against 21.42 ms fast on the
freeze (4.4×). After T4.4 it is 78.25 ms against 9.17 ms. The delta
is still concentrated in the remaining walks (`frame_init` under
opt-level=0, `page_verify`) plus the safe profile's extra
`free_count()` in `freeze()`'s println — which T4.4 collapsed from
4.88 ms to 100.0 µs. The safe build is the control; it is not the
flagship number.

### T4.4 prediction outcome

Pre-registered against `baseline-t4.3` in D-0065 before the
dedicated-host rerun. Mechanism and magnitude were correct. The
headline arithmetic beat the ~9.5 ms projection. Three tight leftover
bounds were missed. Every falsification line (≥ 1 ms, or a third
phase vanishing) held. Point estimates on those leftovers were
~40% optimistic — a second data point on the same estimate bias
the audit recorded as finding 10 (`task_init` / `virtq_init` /
`stvec` predicted µs, measured sub-ms).

| metric | predicted | actual (pooled n=60) | verdict |
|---|---|---|---|
| fast `frame_init` | < 100 µs (expected ~10–50 µs) | 141.2 µs | bound missed; falsify-if ≥ 1 ms held |
| fast `accounting` | < 20 µs (expected ~5–15 µs) | 24.9 µs | bound missed; falsify-if ≥ 1 ms held |
| fast E2→E3g | ~9.5 ms | 9.17 ms | beat the projection |
| safe `freeze` | < 50 µs | 100.0 µs | bound missed; still collapsed from 4.88 ms |
| unnamed phase vanishes | would falsify | none vanished | held |

Headline edges, generated: fast E2→E3g 21.42 → 9.17 ms (−57%);
fast E0→E4 67.05 → 54.52 ms; safe E2→E3g 94.88 → 78.25 ms.

Two unnamed phases moved without vanishing, so they do not falsify.
`page_verify` 2.57 → 2.39 ms (−7%). `E3g` 1.42 → 1.24 ms (−13% in
the pooled CSV; not the same 7%). The removed `frame_init` was
touching ~125 MiB to link ~31k nodes (`total_frames` = 31823 × 4 KiB).
Subsequent phases now run against a warmer cache and TLB. That is a
secondary effect of the rung, recorded in the ladder row, not a
second hypothesis.

### T4.6 prediction outcome

Pre-registered against T4.4 in D-0059 as ranges, not optimistic
bounds, because T4.4 leftovers were ~40% optimistic. Mechanism
(mixed granularity, grain-aware verify) was correct: `tables_used`
= 5, and `page_verify` 731 µs is far from the 1.5–2.2 ms
4K-stepping band. Headline E2→E3g landed in range. Both paging
phase ranges overran (D-0069). Every falsification line held.

| metric | predicted | actual (pooled n=60) | verdict |
|---|---|---|---|
| `page_build` | 50–300 µs | 386 µs | over range; falsify-if ≥ 0.8 ms held |
| `page_verify` | 80–400 µs grain-correct; 1.5–2.2 ms if 4K-stepping | 731 µs | over range; grain-correct confirmed; ≥ 1.0 ms / < 30 µs held |
| combined paging | 0.15–0.70 ms | 1.12 ms | over range |
| fast E2→E3g | 5.5–8.0 ms | 6.43 ms | **in range** |
| `tables_used` | 5–8 | 5 | hit |
| unnamed phase vanishes | would falsify | none vanished | held |

Headline edges: fast E2→E3g 9.17 → 6.43 ms (−30%); cumulative from
`baseline-t4.3` 21.42 → 6.43 ms (3.3×); fast E0→E4 54.52 → 51.67 ms.
Arithmetic remainder if only paging moved: 9.17 − 2.72 = 6.45 ms;
actual 6.43 ms.

`freeze` 7.3 → 12.2 µs (+67%) in a rung that does not touch
`freeze()`. Cause: TCG/I-cache secondary of deleting the
32k-iteration verify loop (D-0059). Opposite sign from T4.4's
data-cache warming. Extra ~5 µs is stamp-overhead class. Named in
the ladder row; not a rung.

Linear extrapolation of `page_verify` was ~40 µs; the registered
range was already 2–10× that and still undershot at 731 µs
(~18× linear). Per-leaf cost went from ~75 ns over ~32k leaves to
~1.3 µs over ~580. Fixed per-call overhead and a colder TCG trace
do not scale down with the count (D-0069).

### Ladder

| rung | hypothesis | E2→E3g after | Δ vs `baseline-t4.3` | disposition |
|---|---|---|---|---|
| bump / lazy free-list | stop linking ~31k virgin frames; `free_count()` is bump arithmetic | 9.17 ms | −12.25 ms (−57%) | landed T4.4 (D-0065); batches `20260817T052349Z-1`/`-2`; subsumes D-0060. Secondary: `page_verify` −7%, `E3g` −13% from a warmer cache/TLB after the ~125 MiB link disappeared |
| D-0060 allocated counter | `free_count = TOTAL − allocated` on the current list | — | — | declined-by-subsumption |
| 2 MiB superpages (D-0059) | mixed-granularity identity map; level-aware verifier; grain-aware `assert_range` | 6.43 ms | −15.00 ms (−70% vs freeze); −2.74 ms (−30% vs T4.4) | **landed T4.6**; batches `20260817T061753Z-1`/`-2`; `tables_used`=5; paging 3.84 → 1.12 ms. Phase ranges over (D-0069); E2→E3g in range. Secondary: `freeze` 7.3→12.2 µs, TCG trace, not a walk |
| `virtq_init` skip discarded program+verify | first pass wiped by `net::init` reset; `fill_descriptors` stays | — | 842 µs = 13% of 6.43 ms; 5% bar is 322 µs | **eligible, not next.** Ceiling on the gain. D-0068 then Linux take the honest number |

### Superpage outcome (D-0059; T4.6)

The projection table below is the pre-registered prediction. Measured
values are the T4.6 prediction-outcome table above and the generated
exhibits. Mixed granularity as decided: 2 MiB L1 leaves for the
aligned KERNEL_RW RAM interior, 4 KiB for W^X image, guards, user
slots/sections, virtio-mmio window, and alignment fragments.
`EXPECTED_TABLES` is 5. `assert_range` steps by leaf grain — the
4K-stepping co-edit failure did not occur.

### Ladder read after T4.6

The profile flattened. No phase exceeds 19%. Seven clear the 5%
bar (322 µs): `E3g` 1.24 ms, `virtq_init` 842 µs, `page_verify`
731 µs, `task_init` 582 µs, `DRIVER_OK` 543 µs, `page_build`
386 µs, `serving_ready` 357 µs. Seven-above-bar is not seven
rungs:

| phase | why it is or is not a candidate |
|---|---|
| `E3g` 19% | the byte; removable only if `syn_rx`→`E3g` shows kernel waste |
| `virtq_init` 13% | discarded first pass — **the remaining E2→E3g candidate** |
| `page_verify` 11% | D-0043 paranoia; keep |
| `task_init` 9% | four U-mode slots; structurally necessary |
| `DRIVER_OK` 8% | live NIC pass; not bundled with virtq_init |
| `page_build` 6% | leftover after mixed granularity |
| `serving_ready` 6% | ARP wait; not kernel compute |

By D-0058's letter the ladder is not closed: `virtq_init` still
clears 5% of E2→E3g. By the report's claim, D-0068 is next.
E0→E4 is 51.67 ms; skipping the discarded virtqueue pass is
~0.8 ms of that (1.6%). Dump placement is tens of milliseconds
and unblocks an unbiased Linux row. virtq_init stays
recorded-eligible. The floor is not declared.

Leaf-count estimate from T4.4 exhaust `total=31823` → `__heap_end`
≈ `0x803B1000`: 62 × 2 MiB leaves on `0x80400000..0x88000000`;
~520 4 KiB leaves for `0x80200000..0x80400000` plus the virtio
window; `tables_used` 67 → 5 (landed).

The pre-registered ranges, kept as the prediction record:

| metric | now | projected range | falsify if |
|---|---|---|---|
| `page_verify` | 2.39 ms | 80–400 µs if grain-correct; 1.5–2.2 ms if still 4 KiB-stepping (failed co-edit) | ≥ 1.0 ms (walk did not shrink) or < 30 µs (something else dropped) |
| `page_build` | 1.45 ms | 50–300 µs | ≥ 0.8 ms |
| combined paging | 3.84 ms (42% of 9.17) | 0.15–0.70 ms | — |
| fast E2→E3g | 9.17 ms | 5.5–8.0 ms | still > 8.5 ms, or a phase this hypothesis does not name vanishes |
| `tables_used` | 67 | 5–8 | still 67 |

Co-edit checklist, walked in the same change:
`walk()` accepts aligned L1, panics on 1 GiB and on a misaligned
2 MiB PPN (D-0026); `assert_range` expected level **and** grain
(not `level == 0` + 4 KiB step); `require_leaf` L0; virtq
`require_identity_rw*` untouched; `EXPECTED_TABLES` and
`held = tables + leftover`; D-0036 / D-0039 prose (7 = 5 + 2);
justfile virtio probe greps (row format unchanged, greps did not
move); DEBUGGING.md superpage first-response note.

### Cross-system

*(stub — T4.8 / T4.9)* Comparisons ride only on E0→first-connect and
E0→E4. Whimbrel's row is [exhibits/edges.md](exhibits/edges.md).
Linux and Unikraft cells stay empty until those tasks land. E3w→E4
is investigated before the Linux baseline (D-0066) so that section
does not treat 34 ms of host-side remainder as guest work.

---

## Threats to validity

Each item is mitigated-and-measured or stated. Seed: T4.0 list as
maintained in [threats-to-validity.md](threats-to-validity.md).

1. **TCG is not hardware.** Compute-dense phases (free-list walk,
   page-table build) and MMIO-dense phases (virtio) are taxed
   differently than on silicon. Every claim carries "under QEMU TCG".
2. **slirp is the TCP peer**, not a wire. E3w−E3g prices virtio+slirp.
   E3w→E4 prices hostfwd delivery plus client recv, not guest compute
   (D-0066).
3. **Client retry granularity is 1.000 ms measured** (persistent
   process; see machine-spec `client_granularity_ns`). That cadence
   does not apply after `connect()`. Fork-per-attempt curl was
   5–15 ms (finding 32) and is not in this dataset.
4. **Boost-off costs ~17% peak clock** (4.2 vs 5.05 GHz on this
   7800X3D). Absolute numbers are larger; boost-state and thermal
   variance are removed, which is what the stability criterion
   measures. All systems measured identically; only the absolute
   floor moves (D-0055).
5. **The pre-M4 harness was fail-open on build failure** (finding 31).
   A failed `cargo build` left the previous ELF in place and printed
   PASS. **No report number derives from that harness.** T4.0b closed
   it; this baseline is `scripts/bench.py` on the fail-closed tree.
6. **Single hart and fixed RAM.** The floor is for this machine shape.
7. **Debug-era history is not evidence.** Regeneration from CSV;
   appendix [appendix-regenerate.md](appendix-regenerate.md).
8. **Linux-tuning fairness** (D-0062) — stated when that row exists.
9. **Unikraft pin** (D-0063) — stated when that row exists.
10. **Instrumentation observer effect.** Stamp overhead is a generated
    row in [exhibits/edges.md](exhibits/edges.md) (5.5 µs on
    fast-boot). `print_after_response` is a second observer: DBCN
    after E3g can delay E4 without moving E3g (D-0066). Dump
    placement is D-0068 (designed; not yet landed).
11. **Host variance.** Dedicated native host, performance governor,
    SMT off, boost off, steal 0 on all recorded trials of the freeze,
    T4.4, and T4.6, two interleaved batches that met max(2%, 200 µs).
    The KVM pod failed this criterion and is not cited.
12. **E3w fidelity.** filter-dump timestamps are a QEMU realtime clock
    that does not match Python `time.time_ns()`. `e0_to_e3w_ns` is
    first-connect plus the pcap-relative SYN/ACK→HTTP interval.
    E3w→E4 therefore inherits that construction (D-0066).
13. **Reservation vs working set** (D-0030).
14. **Estimate bias (D-0069).** Three-for-three, all optimistic
    (predicted too fast): finding 10 (µs predicted, sub-ms measured),
    T4.4 leftover bounds (~40%), T4.6 both paging phases over range.
    We scale as if cost were linear in operation count; fixed
    per-call overhead and TCG-trace warmup do not scale down with N.
    Headline E2→E3g ranges that pad for this have held; unpadded
    phase ranges have not.
15. **Cache/TLB / TCG-trace secondary effects.** Removing a ~125 MiB
    walk *warmed* later phases a few percent (T4.4 `page_verify`,
    `E3g`). Deleting a 32k-iteration `walk()` loop *cooled* the next
    few instructions (`freeze` 7.3 → 12.2 µs at T4.6). Same class,
    opposite sign; named in the ladder row; not a second rung.
16. **Guest work after a guest-side stamp can still move a
    host-observed edge.** QEMU's TCG and slirp share an execution
    loop. Instrumentation that runs *after* E3g occupies TCG and
    delays hostfwd, so E4 moves without E3g moving. This generalizes
    beyond PHASE/DBCN (D-0068). Leaving the dump on the publish→E4
    path biases the flagship cross-system metric against Whimbrel;
    Linux and Unikraft have no equivalent dump. If measured runs
    stopped printing PHASE, the decomposition and E0→E4 would come
    from different boots — that would be its own line.

---

## Future work

Next: D-0068 dump placement (same-boot yield then dump), then the
Linux baseline (D-0062). `virtq_init` remains eligible at 13% of
6.43 ms and is not the next action. D-0060 is declined-by-subsumption.
`-bios none` (D-0061). Unikraft spike (D-0063). T4.3b audit cleanup.
Harness per-batch result files (D-0067) — spec in
`results/README.md`; the bench host implements the write path.

---

## Appendices

- [Numbers that must be regenerated](appendix-regenerate.md) (audit
  findings 16–23).
- [Phase decomposition exhibit](exhibits/phase-decomposition.md)
- [Edges exhibit](exhibits/edges.md)
- [Machine-spec block](exhibits/machine-spec.md)
