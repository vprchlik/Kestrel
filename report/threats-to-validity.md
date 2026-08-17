# Threats to validity — seed (T4.0 list, maintained from T4.3)

Opened at T4.0 per D-0064. The draft section in `report/draft.md` is
normative; this file is the seed that section was written from. Each
item is mitigated-and-measured or stated.

1. **TCG ≠ hardware.** Compute-dense phases and MMIO-dense phases are
   taxed differently than on silicon. Every claim carries "under QEMU TCG".
2. **slirp is a peer**, not a wire. E3w−E3g prices virtio+slirp, not a NIC.
3. **Client granularity.** Persistent process. Measured median on the
   T4.3 freeze is **1.000 ms** (see `report/exhibits/machine-spec.md`
   `client_granularity_ns`). Fork-per-attempt curl was 5–15 ms (finding 32).
4. **Single hart and fixed RAM.** The floor is for this machine shape.
5. **Debug-era history is not evidence.** Regeneration from CSV kills
   chat-only numbers (audit findings 16–23).
6. **Linux-tuning fairness** (D-0062) — stated when that row exists.
7. **Unikraft pin** (D-0063) — stated when that row exists.
8. **Instrumentation observer effect.** Stamp overhead is a generated
   edges-exhibit row (~5.5 µs fast-boot on the freeze).
9. **Host variance.** Report numbers are the T4.3 freeze on a dedicated
   Ubuntu 26.04 host (7800X3D, 8 cores SMT off, boost off, performance
   governor, QEMU 10.2.1, steal 0 on 120 recorded trials). Two interleaved
   batches met max(2%, 200 µs) on both configs. The KVM pod failed this
   criterion and is not cited. Steal=0 is necessary, not sufficient
   (USER_HZ=100) — recorded as a surviving T4.1 finding.
10. **E3w fidelity.** filter-dump pcap timestamps are a QEMU realtime
    clock that does not match Python `time.time_ns()`. `e0_to_e3w_ns` is
    first-connect (monotonic from E0) plus the pcap-relative SYN/ACK→HTTP
    interval, not `pcap_epoch - e0_wall`.
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
