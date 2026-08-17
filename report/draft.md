# Floor-finding: boot to first HTTP byte on a RISC-V unikernel

Draft-early skeleton (T4.3 / D-0064). Every quantitative claim in
Results is generated from `results/runs.csv` and `results/phases.csv`
by `scripts/report-exhibits.py`. Regeneration: `just report-exhibits`.
Do not type table cells. The baseline freeze is D-0055 at tag
`baseline-t4.3` (measured kernel `35861f3`). Every subsequent
before/after cites this baseline.

Conditions, stated once: QEMU TCG, `virt` machine, `-bios default`,
slirp as the TCP peer, dedicated Ubuntu 26.04 host, boost off. Not
hardware time.

---

## Abstract

*(stub — fill at T4.11)* Under QEMU TCG on a dedicated host, a
minimal rv64gc unikernel reaches first HTTP byte in a measured
E2→E3g median of the fast-boot configuration reported in
[exhibits/edges.md](exhibits/edges.md). The dominant kernel terms
are two walks of the frame free list. Verification of the page map
costs more than building it. Safe vs fast is a factor of four,
concentrated in those same terms. Cross-system rows (Linux,
Unikraft) are empty until T4.8/T4.9.

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

Protocol: D-0055. Edges: D-0043. Client: persistent process, retry
started before E0; measured granularity in the machine-spec block.
Pinning: `taskset`, QEMU and client on separate cores. Stamp
overhead: two adjacent stamps at boot (`stamp_a`, `stamp_b`), quoted
against every attributed delta. Statistics: median and IQR; min
shown as the observed floor bound; means never. Stability: two
interleaved 30-trial batches, per-metric medians within max(2%,
200 µs) for every metric ≥ 1 ms. The criterion passed on this
host for both configs; it failed on the KVM pod, so that pod's
numbers are not cited here.

**Baseline freeze.** Tag `baseline-t4.3` (CSV freeze commit). Measured
kernel git SHA `35861f3`. Batches `20260817T041311Z-1` and
`20260817T041311Z-2`. The machine-spec block is copied verbatim from
`results/baseline-summary.txt` into
[exhibits/machine-spec.md](exhibits/machine-spec.md) by
`just report-exhibits`.

Host-control asserts (virt / governor / SMT / boost / steal) fail
closed at batch start. Boost-off is a dedicated-host override of
D-0055's original runs-anywhere alternative: peak clock 4.2 GHz vs
5.05 GHz (~17%), so absolute numbers are larger and boost-state /
thermal variance are removed. All compared systems run on this host
under the same policy; comparisons are unaffected; only the
absolute floor moves.

Exhibit tables: [phase decomposition](exhibits/phase-decomposition.md)
(D-0064 centerpiece columns) and [edges](exhibits/edges.md).

---

## Results

The centerpiece is [exhibits/phase-decomposition.md](exhibits/phase-decomposition.md).
Host-observed edges: [exhibits/edges.md](exhibits/edges.md). Figures
in the three findings below are those generated tables, in prose.

### Two walks, one root cause

On release+fast-boot, `frame_init` (7.20 ms, 34% of E2→E3g) and
`accounting` (4.79 ms, 22%) are 56% of boot-to-publish (11.99 ms of
21.42 ms). They are the same underlying cost: two separate walks of
~31k frames. The first links remaining RAM into the free list at
`frame::init`; the second is `free_count()` before freeze. Bump /
lazy free-list (next) subsumes O(1) accounting: the walk is O(n)
because the list *is* those ~31k virgin frames. D-0060 is
declined-by-subsumption. Paging is not the dominant kernel term.

### Verification costs more than the thing verified

`page_verify` is 2.57 ms; `page_build` is 1.45 ms — 1.8×. The
second walk of the identity map, kept deliberately (D-0043), is
more expensive than constructing the map. That is the
price-of-paranoia line as a specific, defensible number, not a
slogan. Superpages (D-0059) wait until this pair is a larger share
of what remains.

### Safe vs fast is 4.4×, concentrated

Release-default E2→E3g is 94.88 ms against 21.42 ms fast — 4.4×.
The delta is concentrated in `frame_init` (36.78 → 7.20 ms) and
`page_verify` (13.22 → 2.57 ms): opt-level=0 vs release on the same
walks, plus the safe profile's extra `free_count()` in `freeze()`'s
println (safe `freeze` 4.88 ms vs 7.5 µs fast). The safe build is
the control; it is not the flagship number.

### Ladder

*(no rung has landed. Dispositions below are plan, not measurements.)*

| rung | hypothesis | E2→E3g after | Δ vs `baseline-t4.3` | disposition |
|---|---|---|---|---|
| bump / lazy free-list | stop linking ~31k virgin frames; `free_count()` becomes bump arithmetic | — | — | planned next; subsumes D-0060 |
| D-0060 allocated counter | `free_count = TOTAL − allocated` on the current list | — | — | declined-by-subsumption |
| `page_verify` | keep the pass; shrink or speed the walk | — | — | planned after bump |
| `virtq_init` skip discarded program+verify | first pass wiped by `net::init` reset; `fill_descriptors` stays | — | — | candidate; 850.5 µs = 4.0% of 21.42 ms, does not clear 5% on its own; not bundled with `DRIVER_OK`; re-evaluate after bump |

### Cross-system

*(stub — T4.8 / T4.9)* Comparisons ride only on E0→first-connect and
E0→E4. Whimbrel's row is [exhibits/edges.md](exhibits/edges.md).
Linux and Unikraft cells stay empty until those tasks land.

---

## Threats to validity

Each item is mitigated-and-measured or stated. Seed: T4.0 list as
maintained in [threats-to-validity.md](threats-to-validity.md).

1. **TCG is not hardware.** Compute-dense phases (free-list walk,
   page-table build) and MMIO-dense phases (virtio) are taxed
   differently than on silicon. Every claim carries "under QEMU TCG".
2. **slirp is the TCP peer**, not a wire. E3w−E3g prices virtio+slirp.
3. **Client retry granularity is 1.000 ms measured** (persistent
   process; see machine-spec `client_granularity_ns`). Fork-per-attempt
   curl was 5–15 ms (finding 32) and is not in this dataset.
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
    fast-boot).
11. **Host variance.** Dedicated native host, performance governor,
    SMT off, boost off, steal 0 on all 120 recorded trials, two
    interleaved batches that met max(2%, 200 µs). The KVM pod failed
    this criterion and is not cited.
12. **E3w fidelity.** filter-dump timestamps are a QEMU realtime clock
    that does not match Python `time.time_ns()`. `e0_to_e3w_ns` is
    first-connect plus the pcap-relative SYN/ACK→HTTP interval.
13. **Reservation vs working set** (D-0030).

---

## Future work

*(stub)* Next planned: bump/lazy free-list (D-0058). D-0060 is
declined-by-subsumption. No rung until the bump design entry exists.
`-bios none` (D-0061). Linux row (D-0062). Unikraft spike (D-0063).
T4.3b audit cleanup after the freeze, not before.

---

## Appendices

- [Numbers that must be regenerated](appendix-regenerate.md) (audit
  findings 16–23).
- [Phase decomposition exhibit](exhibits/phase-decomposition.md)
- [Edges exhibit](exhibits/edges.md)
- [Machine-spec block](exhibits/machine-spec.md)
