# Floor-finding: boot to first HTTP byte on a RISC-V unikernel

Draft-early skeleton (T4.3 / D-0064; T4.4 and T4.6 ladder rows
filled; T4.8 cross-system and Linux decomposition filled). Every quantitative claim in
Results is generated from CSV by `scripts/report-exhibits.py`.
Regeneration: `just report-exhibits`. Do not type table cells.

The harness overwrites `results/runs.csv` and `results/phases.csv` per
run. The exhibit generator therefore reads git objects, not the
working tree: **baseline columns** from tag `baseline-t4.3` (measured
kernel `35861f3`), **after-ladder and Δ** from the T4.6 superpage CSV
commit (measured kernel `76830e13`), **D-0068 dump-placement** from
that commit plus the two yield-then-dump CSV commits, **T4.8
cross-system** from `ffb7ac7` (measured kernel `1005399`, batches
`20260818T073023Z-1` / `20260818T073023Z-2`), **Linux decomposition**
from the T4.8 serial pin `d705ecb` (batch-1 trial 4). See
[exhibits/phase-decomposition.md](exhibits/phase-decomposition.md)
caption and D-0067. `HEAD` may hold a later batch; it is not
the after-ladder pin. T4.8 Linux decomposition pins stay
`d705ecb` + `93ab617` after D-0073; T4.8b is a new pin, not a
retarget.

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
still a large factor. T4.8 fills the Linux row: on RISC-V under
QEMU TCG software emulation, same host, fast-boot E0→E4 is 14.5×
the trimmed Linux baseline and 18.1× stock
([exhibits/cross-system.md](exhibits/cross-system.md)). Unikraft
is empty until T4.9. The honest E0→E4 number counts QEMU startup,
guest boot wait, and sub-ms delivery once each (D-0070/D-0071);
E3w→E4 is retired.

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
configs on the freeze, T4.4, T4.6, and both D-0068 invocations, and
for all five arms of T4.8; it
failed on the KVM pod, so that pod's numbers are not cited here.

**Reproducibility beyond interleaved batches.** D-0055's stability
check is two shuffled halves of one campaign. D-0068 ran twice:
four batches, two independent invocations, different shuffle seeds,
kernels a CSV-commit apart. [exhibits/dump-placement.md](exhibits/dump-placement.md)
reports the pairwise relative disagreement. Two campaigns
reproducing is a stronger claim than one campaign splitting, and
the generated figure is inside max(2%, 200 µs) on every compared
median.

**T4.8 campaign shape.** The five-arm run interleaved two Whimbrel
profiles with three Linux arms (trimmed, stock, trimmed-instrumented),
two shuffled batches, steal 0 on all 300 recorded trials, stability
PASS on every arm. Whimbrel's own guest number held:
`release-fast-boot` E2→E3g is 6.43 ms in that campaign, matching
the T4.6 after-ladder pin (Δ +550 ns;
[exhibits/cross-system.md](exhibits/cross-system.md)). That is
reproducibility across a different campaign shape — three extra
systems in the shuffle — not a new rung. Host-observed T4.8
Whimbrel edges (new schema: E0→E4, W, D_fin, D_ack, no E3w) are
the T4.8 section of [exhibits/edges.md](exhibits/edges.md).
The Linux guest decomposition is the T4.8 instrumented serial
plus D-0072 labels of the same Image
([exhibits/linux-decomposition.md](exhibits/linux-decomposition.md)),
not a sixth arm.

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
`git show c40945c:results/{runs,phases}.csv` (the T4.6 CSV commit,
not necessarily `HEAD`). Machine-spec fields come from those CSV
rows, not from `results/summary.txt`.

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
comparisons.

**Linear scaling is the wrong model for small phases.**
Pre-registered phase projections in this project have a systematic
bias: they treat cost as linear in operation count. That model is
right when N is large enough that per-call work amortizes, and wrong
as soon as a rung reduces N enough for the fixed component to
dominate. T4.4's `page_verify` ran at about 75 ns per leaf over about
32k 4 KiB leaves. Linear extrapolation to T4.6's ~580 mixed-granularity
leaves predicted ~40 µs. The registered range was already 2–10× that
(80–400 µs) and still undershot: measured 731 µs, about 1.3 µs/leaf,
roughly 17× the linear number. The extra is not a slower walk. It is
the cost that does not scale down with N — software-walk decode,
level and grain asserts, TCG trace warmup. Finding 10 was the same
error in miniature (µs on paper, sub-ms on the stamp table). T4.4
leftover bounds (~40% optimistic) were the second data point. T4.6
paging was the third. Three-for-three, all in the same direction.

This is a transferable lesson about optimizing emulated systems, not
a note about our paging arithmetic. Any rung that reduces an
operation count will disappoint relative to a linear projection,
because the fixed per-call cost becomes the dominant term once N
drops. Headline E2→E3g ranges that pad for this bias have held;
unpadded phase ranges have not. Future phase projections either pad
more than a linear remainder or treat "over range" as the expected
miss and keep only the falsify-if line load-bearing. The 5%
eligibility bar is measured, not estimated, and is unaffected
(D-0069).

**The apparatus and the system share state.** Measuring inside an
emulator means TCG's data cache, its instruction-translation cache,
and the main loop that pumps slirp are host state that guest work
writes as a side effect of existing. T4.4 and T4.6 are a matched
pair. After T4.4 stopped linking ~31k free-list nodes (~125 MiB),
later phases ran against a warmer data cache and TLB: `page_verify`
−7%, `E3g` −13%. After T4.6 deleted the 32k-iteration verify loop,
`freeze` — which the rung does not call — went 7.3 → 12.2 µs (+67%)
because the next instructions met a colder TCG translation cache.
Same cause, opposite signs. Both deltas are sub-instrumentation-noise
in absolute terms (stamp overhead is ~5.5 µs on fast-boot; the
`freeze` extra is ~5 µs). They are not second hypotheses and not
co-edit misses. Together they illustrate threats item 16.

The occupancy case of the same threat is the PHASE dump. Until
D-0068, `print_after_response` ran immediately after first-HTTP
`wait_tx`. The hypothesis was that DBCN occupied TCG on the thread
that pumps slirp and that E0→E4 therefore measured instrumentation.
The mechanism landed: after `wait_tx` / `E3g_doorbell`,
`timer::yield_once` asserts ticks are armed (finding 13), re-arms,
`wfi`s once, then prints. Two N-trials produced no improvement.
The dump stays after the yield: instrumentation off the measured
path is correct even when the measured cost is zero. E3w→E4 remains
open — see Results.

Exhibit tables: [phase decomposition](exhibits/phase-decomposition.md)
(D-0064 centerpiece columns), [edges](exhibits/edges.md),
[dump placement](exhibits/dump-placement.md), and
[cross-system](exhibits/cross-system.md).

---

## Results

The centerpiece is [exhibits/phase-decomposition.md](exhibits/phase-decomposition.md).
Host-observed edges: [exhibits/edges.md](exhibits/edges.md). D-0068
dump placement: [exhibits/dump-placement.md](exhibits/dump-placement.md).
Cross-system: [exhibits/cross-system.md](exhibits/cross-system.md).
Figures in the findings below are those generated tables, in prose.

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
the pooled CSV; not the same 7%). That movement is the warm-cache
half of the matched TCG pair in Methodology, not a second hypothesis.

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
`baseline-t4.3` 21.42 → 6.43 ms (3.3×); fast E0→E4 54.52 → 51.66 ms.
Arithmetic remainder if only paging moved: 9.17 − 2.72 = 6.45 ms;
actual 6.43 ms.

`freeze` 7.3 → 12.2 µs is the cold-translation half of the matched
TCG pair in Methodology. Linear-vs-measured `page_verify` (~40 µs
extrapolated, 731 µs measured, ~75 ns/leaf over ~32k becoming
~1.3 µs/leaf over ~580) is the D-0069 worked example there.

### Matched TCG secondaries (T4.4 and T4.6)

T4.4 made later phases faster: warm data cache and TLB after not
touching ~125 MiB. T4.6 made `freeze` slower: cold instruction
translation after the hot loop's removal. Same cause, opposite
signs, both sub-instrumentation-noise in absolute terms. Together
they illustrate threats item 16 — measuring inside an emulator
means the measurement apparatus and the measured system share
state. Named in the ladder rows; not rungs.

### D-0068: dump placement did not move E3w→E4

Pre-registered: the PHASE dump between publish and the client's
first byte occupied TCG on the loop that pumps slirp, so E0→E4
measured instrumentation, and that occupancy was the natural
account of tens of milliseconds of E3w→E4 — including why safe
is ~3× worse than fast on that term. The mechanism landed (one
`wfi` after `wait_tx`, then dump, same boot). The generated
comparison is [exhibits/dump-placement.md](exhibits/dump-placement.md).

E2→E3g unchanged is correct: the stamps did not move. E0→E4 did
not improve in either invocation. E3w→E4 is untouched in both
profiles. The pre-registered claim is refuted for this
implementation.

Before keep / extend / revert, two possibilities.

**The yield is ineffective, not the hypothesis wrong.** One `wfi`
returns on the next armed tick (~10 ms). If dump occupancy D is
serialized with remaining host time H, the observed gap is D+H.
A yield Y < H reorders the same work: Y of delivery, then D of
dump, then H−Y of delivery. Total still D+H. No change is what
both a too-short yield and a dump-irrelevant gap predict. The
discriminating test is a yield long enough to bracket the entire
fast gap (~31 ms). If that collapses E3w→E4, the mechanism was
right and one tick under-shot. That test is not this change and
is not a kernel rung.

**The gap is host-side but not host *work*.** The E3w construction
anchors the pcap-relative SYN/ACK→HTTP interval to first-connect,
assuming connect-success coincides with the guest's SYN/ACK
(`scripts/bench.py`, "first-connect (≈ SYN/ACK)"). With hostfwd
that fails: `connect()` succeeds at QEMU's host-side accept — the
listener is up during QEMU startup, guest-independent — while the
guest SYN/ACK happens only after firmware plus boot to net-init.
Everything between accept and SYN/ACK lands in "E3w→E4". Three
pieces of recorded evidence fit: first-connect is
profile-independent (18.53 vs 18.55 ms) though serving-readiness
differs by ~70 ms; rung savings partition exactly as anchoring
predicts (pre-net-init savings moved "E3w→E4": `frame_init`
−7.06 ms predicted vs −7.37 observed, paging −2.72 vs −2.83;
post-net-init savings moved E0→E3w: `accounting` −4.77 vs −5.08);
and the affine form const + boot-to-net-init reproduces the 3×
safe/fast ratio against the ~12× boot ratio. Under this reading
the D-0068 null is expected: there was never tens of ms of
post-publish host work to reorder — true delivery is
sub-millisecond. This is D-0070, pre-registered with a
falsifiable pcap-internal test (below) and **confirmed** by the
bench-host pcap pass.

The yield stays. Instrumentation off the measured path is
defensible on principle even when it costs nothing (the flagship
edges moved by a fraction of a millisecond, inside stability). A
`wfi` does not occupy TCG the way a post-publish spin would.
Reverting would put DBCN back on the interval under test.

**The discriminating test (D-0070)** was read-only over per-trial
pcaps that already existed for T4.6 and both D-0068 campaigns — no
new boots, no kernel or harness code. On one pcap clock, per
trial: `W` = guest SYN/ACK − first slirp ARP request for the
guest (accept-to-handshake wait); `D_ack` = ACK-of-response −
HTTP frame; `D_fin` = client FIN − HTTP frame (the client closes
after `recv`, so this bounds publish→client). Predictions:
`D_fin` ≤ 5 ms, profile ratio < 2; `W` ≈ E3w→E4 − `D_fin`.
Falsify lines: `D_fin` ≥ 10 ms, or scaling ≥ 2× with profile.

**Outcome** (generated: [exhibits/d0070-pcap.md](exhibits/d0070-pcap.md);
n=60 per config per campaign): `D_fin` 63–155 µs in all six
campaign-configs; safe/fast ratio 0.41–0.91 — delivery does not
scale with profile at all; `D_ack` 24–40 µs; `W_safe − W_fast`
61.40 / 61.84 / 61.65 ms against the predicted ≈ 61.5 ms. One
pre-registered line failed as written: `W + D_fin` under-reconstructs
E3w→E4 by a constant −6.70 to −7.00 ms in every cell, profile-
independent, IQRs under ~1 ms. That constant was diagnosed before
anything was amended (D-0071): it is the slice of QEMU startup
between the hostfwd listener coming up — where first-connect stamps,
because the host kernel completes the client handshake into the
listen backlog the moment `listen()` exists — and the main loop
going live, when slirp first services the queued connection and
emits the ARP that starts `W`'s clock. A one-clock mechanism check
(polling filter-dump's incremental pcap writes against the client's
own stamps) closed the accounting per boot to +0.05…+0.32 ms, and a
late-connect control showed slirp forwards an accepted connection's
SYN in 60–160 µs once startup is over. So the former "E3w→E4"
decomposes with nothing left over: **QEMU startup (~6.8 ms) +
waiting for our own guest to boot (`W`) + sub-millisecond delivery
(`D_fin`)** — the first two already counted once, correctly, in
E0→E4. E3w→E4 is retired as a reported metric; delivery is
reported as `D_fin`.

### Ladder

| rung | hypothesis | E2→E3g after | Δ vs `baseline-t4.3` | disposition |
|---|---|---|---|---|
| bump / lazy free-list | stop linking ~31k virgin frames; `free_count()` is bump arithmetic | 9.17 ms | −12.25 ms (−57%) | landed T4.4 (D-0065); batches `20260817T052349Z-1`/`-2`; subsumes D-0060. Secondary: later phases faster (warm data cache; matched pair) |
| D-0060 allocated counter | `free_count = TOTAL − allocated` on the current list | — | — | declined-by-subsumption |
| 2 MiB superpages (D-0059) | mixed-granularity identity map; level-aware verifier; grain-aware `assert_range` | 6.43 ms | −15.00 ms (−70% vs freeze); −2.74 ms (−30% vs T4.4) | **landed T4.6**; batches `20260817T061753Z-1`/`-2`; `tables_used`=5; paging 3.84 → 1.12 ms. Phase ranges over (D-0069); E2→E3g in range. Secondary: `freeze` slower (cold I-translation; matched pair) |
| `virtq_init` skip discarded program+verify | first pass wiped by `net::init` reset; `fill_descriptors` stays | — | 842 µs = 13% of 6.43 ms; 5% bar is 322 µs | **eligible, not next.** Ceiling on the gain. Linux takes the honest number |

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
clears 5% of E2→E3g. D-0068 was the next *action* and has been
measured: it did not move E0→E4. Linux is next. Fast E0→E4 is
51.66 ms on the T4.6 batches; skipping the discarded virtqueue
pass is ~0.8 ms of that (1.6%). virtq_init stays
recorded-eligible. The floor is not declared. The former ~31 ms
"E3w→E4" of that 52 ms is resolved: QEMU startup + guest boot
wait + sub-ms delivery (D-0070/D-0071), each counted once in
E0→E4 — there is no separate host term to take.

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

Generated from `ffb7ac7` (batches `20260818T073023Z-1` /
`20260818T073023Z-2`, n=60 recorded per arm):
[exhibits/cross-system.md](exhibits/cross-system.md). No E3w-derived
column. W is not next to a Linux row. E0→first-connect is a control
(medians 18.78–18.83 ms, span 56.3 µs). Pre-registered gates held:
no SYN-grid failure, no RST, first-connect bound, trimmed-vs-stock
tripwire did not fire. Linux `trimmed` W is 718.53 ms with a 2.95 ms
IQR — smooth, not snapped to a 1 s grid — so confound A's announce
mitigation did what it was registered to do.

On RISC-V under QEMU TCG software emulation, same host, same QEMU,
`release-fast-boot` reaches first HTTP byte 14.5× faster than
trimmed Linux and 18.1× faster than stock
([exhibits/cross-system.md](exhibits/cross-system.md)). Published
unikernel figures (2–3 ms) and Firecracker's ~125 ms Linux boot are
x86 with KVM hardware virtualization, where absolute times run
roughly 5–10× lower. Those absolute numbers are not comparable to
52.28 ms or 759.79 ms; the ratio is, because the emulation penalty
applies to both arms on the same host.

Instrumentation cost is measured, not caveated:
trimmed-instrumented − trimmed = 23.70 ms for
`loglevel=7 printk.time=1 initcall_debug` on the same
`Image-trimmed` binary (identical `kernel_sha256`).

S is reported per system, never pooled across systems. D-0071
pools Whimbrel safe and fast because S is profile-independent on
one ELF (`s_ns_fast=6.87 ms`, `s_ns_safe=6.98 ms` in this
campaign's batch header — the ~6.8 ms constant of
[exhibits/d0070-pcap.md](exhibits/d0070-pcap.md)). A five-arm pool
is 13.51 ms with a 7.95 ms IQR, dragged by Linux's 20.8 MB Image
(that load lands in S, D-0062). The wide IQR is two populations,
not noise.

This is floor-finding, not a trophy. The result is what a
single-purpose VM's structure buys under those stated conditions.
Whimbrel's guest work in this campaign is E2→E3g 6.43 ms; the
phase decomposition
([exhibits/phase-decomposition.md](exhibits/phase-decomposition.md))
shows where that interval goes. No "fastest" without its conditions
attached.

The trimmed row's good-faith claim is now backed by measurement: it
beats stock by 188.32 ms, so the trim removed real work rather than
hobbling Linux. The published config is
`bench/linux/linux-trimmed.fragment` on `qemu_riscv64_virt_defconfig`
(Buildroot 2026.02.3, kernel 6.18.7). A Linux boot-time specialist
could likely do better — we claim *a* minimal Linux, not *the*
minimal Linux (D-0062). The D-0072 labels named one miss: `FTRACE`
defaults y from `DEBUG_KERNEL`, the fragment never unsets it, and
`trace_eval_sync` is 68% of the 327 ms hole. That is the T4.8
Image. D-0073 acts on the miss (`# CONFIG_FTRACE is not set` plus
the printk leftovers); T4.8b is the after. The T4.8 exhibit stays
the before — a diagnostic pass named a cost, we removed it, and
the campaign will show what it bought. Do not treat 222.6 ms as a
quiet-row saving (D-0069); the pre-registered range is 540–740 ms.

### Linux boot decomposition

Generated from the T4.8 instrumented serial plus the D-0072
`ignore_loglevel` labels
([exhibits/linux-decomposition.md](exhibits/linux-decomposition.md);
`d705ecb` + `93ab617`, same `Image-trimmed`). The finding inverts
the intuition: the virtio path Linux actually needs is 4.9 ms
(UART-inflated; `virtio_net_driver_init` + `virtio_mmio_init`).
The 327.24 ms hole is not that path. `trace_eval_sync` is 222.6 ms
UART-inflated on the diagnostic boot — 68.0% of the T4.8 cell,
which those microseconds label and do not replace. Full-file,
`of_platform_serial_driver_init` is 163.0 ms UART-inflated
(generic DT serial-bus probe, not in the hole). Kind, not
magnitude: named subsystems a single-purpose kernel never runs,
not "Linux is slower."

`initcall_debug` produced nothing on the T4.8 pin. Two factors,
this order (D-0072): `loglevel=7` filters `KERN_DEBUG` (necessary
and sufficient for the missing lines); `# CONFIG_KALLSYMS is not
set` affects names only (`PM: Calling 0xffffffff800614ec` in the
same log). A kernel trimmed this hard cannot be fully
instrumented by its own debug facility. No sixth arm.

`trace_eval_sync` is the tracing subsystem's enum-to-string sync
pass (`late_initcall_sync` flushing `eval_map_work_func`). No
tracing consumer is running (`/init` is a static musl HTTP
server; `PROC_FS`/`SYSFS` unset). `FTRACE` defaults y when
`DEBUG_KERNEL=y`; the T4.8 fragment never unsets it. That is a
missed trim on **this** Image, not a documented keep. D-0073
rebuilds trimmed and re-runs as T4.8b; this decomposition stays
the before. `CONFIG_SERIAL_OF_PLATFORM` stays a keep:
`serial8250_init` is 2.0 ms UART-inflated (core register, "4
ports"); `of_platform_serial_driver_init` is the DT probe of
`10000000.serial` (82.5×, UART-inflated).

The printk-visible kernel to `Run /init` is 617.58 ms. The top
ten gaps are 84.1% of that span; gap 1 is 327.24 ms / 53.0%.
`/init` is 627.03 ms of kernel then 26.18 ms of server work.
Printk `Run /init` → shutdown = 43.31 ms = 9.45 + 26.18 + 7.68.
Unmeasured prefix: 63 untimed OpenSBI lines, then 39 kernel lines
at `0.000000` until `sched_clock` at 38 µs. No E2 constructed.

---

## Threats to validity

Each item is mitigated-and-measured or stated. Seed: T4.0 list as
maintained in [threats-to-validity.md](threats-to-validity.md).

1. **TCG is not hardware.** Compute-dense phases (free-list walk,
   page-table build) and MMIO-dense phases (virtio) are taxed
   differently than on silicon. Every claim carries "under QEMU TCG".
2. **slirp is the TCP peer**, not a wire. E3w−E3g prices virtio+slirp.
   True hostfwd delivery plus client recv is bounded by `D_fin` at
   63–155 µs ([exhibits/d0070-pcap.md](exhibits/d0070-pcap.md)).
   The former "E3w→E4" is retired: QEMU startup (D-0071) plus the
   accepted connection waiting for the guest to boot (D-0070),
   mislabeled by E3w's anchoring construction.
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
8. **Linux-tuning fairness** (D-0062) — measured: trimmed beats
   stock by 188.32 ms
   ([exhibits/cross-system.md](exhibits/cross-system.md)), so the
   trim removed real work. Config published:
   `bench/linux/linux-trimmed.fragment`. A Linux boot-time
   specialist could likely do better; we claim *a* minimal Linux,
   not *the* minimal Linux. On the T4.8 Image, `FTRACE` is a
   recorded miss: it defaults y from `DEBUG_KERNEL`, the fragment
   never unsets it, and `trace_eval_sync` is 68% of the 327 ms
   hole. D-0073 acts on that miss (new Image, T4.8b campaign);
   the T4.8 numbers still include it. The miss is not left as
   "we ran out of patience."

   **Non-reversing select.** Kconfig `select` does not unset the
   target when the selector goes. A helper or transport that
   `select` pulled in stays `=y` whenever it has its own prompt,
   a default, or another selector under a menu the trim did not
   touch. That is a property of trimming a kernel by subsystem,
   not a quirk of this fragment; it cost three `linux-build`
   iterations to see. Seven symbols on this Image were that
   shape: `NET_9P`, `DNS_RESOLVER`, `NLS`, `MTD`, `DAX`,
   `IP_PNP`, `EXPORTFS`. The fourth pass walked remaining `=y`
   against live `select` edges in one go (`FHANDLE` and overlay
   were still selecting `EXPORTFS`; unsetting the helper alone
   would have left it `=m`) rather than catching the next
   orphan after another rebuild.

   Walked and kept, with the reason, so the bound is analysis
   rather than patience: `CRC32` (`MACB` still selects it);
   `NVMEM` (`NVMEM_SUNXI_SID` still y); `SHMEM` (anonymous
   memory, not a DRM leftover); `EVENTFD` (`default y` syscall;
   `MEMCG`'s select was extra); `FW_LOADER` (`default y`
   firmware facility); `FILE_LOCKING` (`default y`); `MACB` /
   `PHYLIB` / `MICREL_PHY` (sibling NIC to virtio-net, still a
   live ethernet driver); `NETFILTER` (defconfig y, own prompt);
   `INET_DIAG` (`default y` for `ss`; not a dropped consumer);
   `AUTOFS_FS`, `POSIX_MQUEUE`, `SYSVIPC` (defconfig y, own
   prompts); `VIRTIO_CONSOLE` / `BALLOON` / `INPUT` (defconfig y
   extra virtio); `XFRM` (`XFRM_USER=m` still a consumer);
   `FAILOVER` / `NET_FAILOVER` (`VIRTIO_NET` selects them);
   `DEBUG_FS`, `FB`, `VT`, `PINCTRL`, `I2C`, `SPI`, `THERMAL`,
   `CPU_IDLE` (named deferred: no-boot risk or idle path).
9. **Unikraft pin** (D-0063) — stated when that row exists.
10. **Instrumentation observer effect.** Stamp overhead is a generated
    row in [exhibits/edges.md](exhibits/edges.md) (5.5 µs on
    fast-boot). `print_after_response` is a second observer. D-0068
    moved it after a yield so DBCN is not on publish→E4. Two N-trials
    produced no E0→E4 improvement
    ([dump-placement.md](exhibits/dump-placement.md)). The yield
    stays on principle. The null was later explained: there was no
    post-publish host work for it to move (D-0070).
11. **Host variance.** Dedicated native host, performance governor,
    SMT off, boost off, steal 0 on all recorded trials of the freeze,
    T4.4, T4.6, both D-0068 invocations, and T4.8 (300 recorded), two
    interleaved batches
    that met max(2%, 200 µs). D-0068 additionally reproduced across
    two independent campaigns. The KVM pod failed the criterion and
    is not cited.
12. **E3w fidelity.** filter-dump timestamps are a QEMU realtime clock
    that does not match Python `time.time_ns()` — measured as a
    per-boot offset of −30 to −846 ms on the pod, so no absolute
    `pcap_epoch − e0_wall` quantity is usable (D-0071).
    `e0_to_e3w_ns` was first-connect plus the pcap-relative
    SYN/ACK→HTTP interval; its anchor ("first-connect ≈ SYN/ACK")
    was tested and is false under hostfwd — connect-success is the
    host kernel accepting into QEMU's listen backlog during startup,
    not the guest handshake (D-0070, confirmed). E3w-derived metrics
    are retired in favor of pcap-internal intervals on one clock
    (`W`, `D_ack`, `D_fin`). No E3w-derived column may appear in a
    cross-system table.
13. **Reservation vs working set** (D-0030).
14. **Estimate bias (D-0069).** Stated as methodology prose, not only
    here. Three-for-three, all optimistic (predicted too fast):
    finding 10, T4.4 leftover bounds (~40%), T4.6 both paging phases
    over range. We scale as if cost were linear in operation count;
    a fixed per-call cost does not scale down with N (~75 ns/leaf
    over ~32k becoming ~1.3 µs/leaf over ~580). Any rung that
    reduces an operation count will disappoint relative to linear
    projection, because the fixed component becomes the dominant
    term. Headline E2→E3g ranges that pad for this have held;
    unpadded phase ranges have not.
15. **Matched TCG secondaries.** T4.4 made later phases faster (warm
    data cache after not touching ~125 MiB). T4.6 made `freeze`
    slower (cold instruction translation after the hot loop's
    removal). Same cause, opposite signs, both
    sub-instrumentation-noise. Presented together under item 16;
    named in the ladder rows; not rungs.
16. **The measurement apparatus and the measured system share
    state.** QEMU's TCG (data cache, instruction translation) and
    the main loop that pumps slirp are host state that guest work
    writes as a side effect of existing. Two illustrations. The
    matched pair in item 15 is the cache/translation surface. The
    occupancy surface is guest work after a guest-side stamp moving
    a host-observed edge. D-0068 tested the PHASE dump as that
    occupant: two N-trials, no E4 movement. The dump stays off the
    interval on principle. Linux and Unikraft share slirp/hostfwd;
    they will not share a PHASE dump. If measured runs stopped
    printing PHASE, the decomposition and E0→E4 would come from
    different boots — that would be its own line.
17. **A derived metric double-counted guest boot under a
    host-sounding name (D-0070/D-0071).** "E3w→E4" folded QEMU
    startup (~6.8 ms) and boot-to-net-init wait (~24/~85 ms) into a
    term labeled host-side delivery. It survived a pre-registered
    audit and four measurement campaigns, and was caught only
    because it moved with kernel rungs a host-side term should not
    respond to. The honest headline (E0→E4, two direct client-clock
    stamps) was never wrong — each piece was counted once there.
    The lesson sits beside D-0069's: an unexplained constant must
    not keep a plausible-sounding name.
18. **A kernel trimmed this hard cannot be fully instrumented by
    its own debug facility** (D-0072). `initcall_debug` is on the
    T4.8 cmdline and produced zero entries: `loglevel=7` filters
    `KERN_DEBUG` (necessary and sufficient); kallsyms off affects
    names only. Stated. The decomposition is printk gaps plus
    `/init` stamps plus UART-inflated labels from one
    `ignore_loglevel` boot of the same Image
    ([linux-decomposition.md](exhibits/linux-decomposition.md)).
    Gap 1 is `trace_eval_sync` (222.6 ms UART-inflated, 68% of the
    327.24 ms cell, which those microseconds label and do not
    replace). Not a sixth comparison arm. On this Image `FTRACE`
    is a missed trim (D-0062: *a* minimal Linux, not *the*
    minimal Linux). D-0073 acts on it; T4.8b is the after.
19. **`W` fuses guest boot with egress delay; only a guest-internal
    stamp separates them** (T4.8b diagnosis, 2026-08-18). `w_ns`
    spans the guest's entire boot-to-first-TX, so an inflated `W` is
    not attributable to boot or delivery without an instrument that
    sees the boundary. The largest T4.8 excursions (+39 to +125 ms in
    E0→E4 and `W` together) were slow guest boots — `/init` announce
    stamp late by the same amount, egress lag normal — not delivery
    faults; the egress-attributed tail is ≤ 7.7 ms. Medians and the
    trimmed-vs-stock delta are unmoved (≤ 0.037 ms). The one ~1 s
    anomaly is a single T4.8b warmup trial; the SYN-grid gate fired
    and nothing published. It is not an egress fault but a guest-side
    lost ARP solicit (D-0074), measured at 25 in 550 boots on the
    bench host: the first solicit never reached the TX ring and the
    guest's own `neigh` retransmit re-sent it 1.03 s later, past
    slirp's ARP-pending drop. An earlier revision of this item offered the
    absence of comparable stalls across the recorded campaigns as
    evidence of robustness; that claim is withdrawn, not weakened —
    it was an absence of observation under a detector blind below the
    cliff, and the one quantity that separated event boots from clean
    ones (the margin between the announce and the last virtio ctrl-vq
    completion, ~12.4 ms typical against ~0.2 ms on every event boot;
    a marker, not a cause — D-0076) was never measured on the older
    images. During this diagnosis the fused metric
    briefly produced the same misattribution item 17 records — one
    step after that lesson was written down — reversed by the
    guest-stamp split; the recurrence is why the corollary is an
    instrument rule, not vigilance: a metric spanning two subsystems
    must not be attributed to either without an instrument that sees
    the boundary. Two harness facts are recorded, not fixed: the
    SYN-grid gate tests a relative interval (blind to absolute
    first-TX time, so it catches only stalls that cross slirp's ~1 s
    ARP-pending drop) and runs only on Linux trials — coverage
    inversely matched to exposure (Whimbrel fast-boot: ~980 ms silent
    window, ungated; stock: ~103 ms, gated). And Whimbrel's
    guest-side detector (`first_rx − DRIVER_OK`, flat to 0.56 ms over
    ~400 boots) is a side effect of D-0056.3's correctness `wfi`
    removal — luck, not design; a rung re-introducing that `wfi`
    degrades it to ≥ 10 ms tick grain. That interval spans request to
    reply through Whimbrel's own stack, so it bounds guest-side loss
    as well as egress hold — where Linux's fire-and-forget `/init`
    announce observes neither (D-0074).

20. **T4.8b's Linux `/init` is not T4.8's** (D-0075/D-0076). The
    lost-solicit event is campaign-fatal, so `/init` now shortens the
    `neigh` retransmit from 1 s to 50 ms via one `RTM_SETNEIGHTBL`
    before the announce. That call is on the measured path and
    inflates the Linux baseline by a measured **2.87 ms** (`T_NEIGH`
    stamp; ~1.5% of the 188 ms cross-system delta) — a bias toward
    Whimbrel, applied identically to stock and trimmed so the trim
    comparison is unaffected. An earlier "sub-millisecond" estimate
    was wrong by 3x, which is why the cost is stamped rather than
    argued. The heal it installs is **unexercised**: the event has
    not recurred in any fix-bearing configuration, so the timer-wheel
    arithmetic behind the 50 ms constant is read out of the 6.18.7
    source and has never been observed. Why the events stopped is
    **not** established, and four diagnostic arms overturned two
    successive readings of it (D-0076). What per-boot analysis does
    show: the events are a distinct early announce mode at ~156.4 ms,
    which occurred on 4.73% of boots one morning and 1.67% of boots
    that afternoon on the same source — so the published 4.55% rate
    measures a host state, not the image. Two consequences: the 2.87
    ms may also be the protection, so anything making the
    pre-announce path cheaper may re-arm the race; and "no event in
    6200 boots" is really **no event in 11 informative boots**, since
    only early-mode boots can collide. Per-trial `guest_ftx_ns` /
    `guest_arp_req_n` make any recurrence countable rather than
    fatal.

---

## Future work

Unikraft spike (D-0063). `virtq_init` remains eligible at 13% of
6.43 ms and is not the next action. D-0060 is
declined-by-subsumption. `-bios none` (D-0061). T4.3b audit
cleanup. T4.8b (D-0073) is the FTRACE act-on, spec'd, not yet
run on the bench host; the T4.8 exhibit stays the before.

---

## Appendices

- [Numbers that must be regenerated](appendix-regenerate.md) (audit
  findings 16–23).
- [Phase decomposition exhibit](exhibits/phase-decomposition.md)
- [Edges exhibit](exhibits/edges.md)
- [Dump placement exhibit](exhibits/dump-placement.md)
- [Cross-system exhibit](exhibits/cross-system.md)
- [Linux boot decomposition](exhibits/linux-decomposition.md)
- [D-0070 pcap pass exhibit](exhibits/d0070-pcap.md) (generated on
  the bench host; committed from there)
- [Machine-spec block](exhibits/machine-spec.md)
