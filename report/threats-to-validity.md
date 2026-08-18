# Threats to validity — seed (T4.0 list, maintained from T4.3)

Opened at T4.0 per D-0064. The draft section in `report/draft.md` is
normative; this file is the seed that section was written from. Each
item is mitigated-and-measured or stated.

1. **TCG ≠ hardware.** Compute-dense phases and MMIO-dense phases are
   taxed differently than on silicon. Every claim carries "under QEMU TCG".
2. **slirp is a peer**, not a wire. E3w−E3g prices virtio+slirp, not a NIC.
   E3w→E4 prices hostfwd delivery plus client recv (D-0066). After
   D-0068 it is still ~31 ms of ~52 ms and unexplained.
3. **Client granularity.** Persistent process. Measured median on the
   T4.3 freeze is **1.000 ms** (see `report/exhibits/machine-spec.md`
   `client_granularity_ns`). That cadence is connect-retry only; it
   does not apply after `connect()`. Fork-per-attempt curl was 5–15 ms
   (finding 32).
4. **Single hart and fixed RAM.** The floor is for this machine shape.
5. **Debug-era history is not evidence.** Regeneration from CSV kills
   chat-only numbers (audit findings 16–23).
6. **Linux-tuning fairness** (D-0062) — stated when that row exists.
7. **Unikraft pin** (D-0063) — stated when that row exists.
8. **Instrumentation observer effect.** Stamp overhead is a generated
   edges-exhibit row (~5.5 µs fast-boot). D-0068 moved
   `print_after_response` after a yield. Two N-trials produced no
   E0→E4 improvement (`report/exhibits/dump-placement.md`). The yield
   stays on principle. It did not solve E3w→E4.
9. **Host variance.** Report numbers are the dedicated Ubuntu 26.04
   host (7800X3D, 8 cores SMT off, boost off, performance governor,
   QEMU 10.2.1, steal 0). Freeze, T4.4, T4.6, and both D-0068
   invocations each ran two interleaved batches that met max(2%, 200 µs)
   on both configs. D-0068 additionally reproduced across two
   independent campaigns. The KVM pod failed this criterion and is
   not cited. Steal=0 is necessary, not sufficient (USER_HZ=100) —
   recorded as a surviving T4.1 finding.
10. **E3w fidelity.** filter-dump pcap timestamps are a QEMU realtime
    clock that does not match Python `time.time_ns()`. `e0_to_e3w_ns` is
    first-connect (monotonic from E0) plus the pcap-relative SYN/ACK→HTTP
    interval, not `pcap_epoch - e0_wall`. E3w→E4 inherits that
    construction (D-0066).
11. **Reservation vs working set** (D-0030).
12. **Pre-M4 harness fail-open (finding 31, T4.0b receipt).**
    `scripts/boot-test.sh` ran under `set -u` only; a failed `cargo build`
    left the previous ELF in place and printed `TEST PASS`. **No report
    number derives from that harness.** This baseline is `scripts/bench.py`
    on the fail-closed tree.
13. **Boost-off (~17% peak clock, 4.2 vs 5.05 GHz).** Dedicated-host
    override of D-0055's original runs-anywhere alternative. Absolute
    numbers are larger; boost-state and thermal variance are removed.
    All systems measured identically; comparisons unaffected; only the
    absolute floor moves.
14. **Estimate bias (D-0069).** Methodology prose, not only this bullet.
    Three-for-three, all optimistic: finding 10, T4.4 leftovers (~40%),
    T4.6 paging phases over range. Cost is not linear in operation
    count; a fixed per-call cost does not scale down with N
    (~75 ns/leaf over ~32k becoming ~1.3 µs/leaf over ~580). Any rung
    that reduces an operation count will disappoint relative to linear
    projection, because the fixed component becomes the dominant term.
15. **Matched TCG secondaries.** T4.4 made later phases faster (warm
    data cache after not touching ~125 MiB). T4.6 made `freeze` slower
    (cold instruction translation after the hot loop's removal). Same
    cause, opposite signs, both sub-instrumentation-noise. Presented
    together under item 16.
16. **The measurement apparatus and the measured system share state.**
    QEMU's TCG (data cache, instruction translation) and the main loop
    that pumps slirp are host state that guest work writes as a side
    effect of existing. Two illustrations. The matched pair in item 15
    is the cache/translation surface. The occupancy surface is guest
    work after a guest-side stamp moving a host-observed edge. D-0068
    tested the PHASE dump as that occupant: two N-trials, no E4
    movement. The dump stays off the interval on principle. E3w→E4
    is open (~31 ms of ~52 ms). Linux and Unikraft share slirp/hostfwd
    and will not share a PHASE dump. If measured runs ever stopped
    printing PHASE, the decomposition and E0→E4 would come from
    different boots — that would be its own line.
