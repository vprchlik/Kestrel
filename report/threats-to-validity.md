# Threats to validity — seed (T4.0 list, maintained from T4.1)

Opened at T4.0 per D-0064; the report draft inherits this list. Each item
is mitigated-and-measured or stated. This is a seed, not the section.

1. **TCG ≠ hardware.** Compute-dense phases and MMIO-dense phases are
   taxed differently than on silicon. Every claim carries "under QEMU TCG".
2. **slirp is a peer**, not a wire. E3w−E3g prices virtio+slirp, not a NIC.
3. **Client granularity.** The measurement client is a persistent process
   at ~1 ms cadence. Measured median `client_granularity_ns` = **1 000 232 ns**
   (≈ 1.000 ms; min 1.000 ms, max 1.10 ms on the T4.1 batch). Fork-per-attempt
   curl was 5–15 ms (finding 32).
4. **Single hart and fixed RAM.** The floor is for this machine shape.
5. **Debug-era history is not evidence.** Regeneration from CSV kills
   chat-only numbers (audit findings 16–23).
6. **Linux-tuning fairness** (D-0062) — stated when that row exists.
7. **Unikraft pin** (D-0063) — stated when that row exists.
8. **Instrumentation observer effect.** Stamp overhead is measured in T4.2.
9. **Host variance.** `taskset`, recorded load average, N=30, median/IQR.
    This harness host is a shared KVM guest (`hypervisor` in `/proc/cpuinfo`,
    `systemd-detect-virt=kvm`, cgroup `pod-…`) with **no cpufreq**,
    **nonzero `/proc/stat` steal**, and USER_HZ=100 (10 ms steal ticks).
    D-0055's performance governor is `unavailable`. Fresh interleaved
    pair (git `9678270`): steal was 0 on 119/120 recorded trials,
    Spearman(steal, E0→E4) ≈ 0, and the one stolen trial was not in the
    slow quartile. Sub-tick host jitter remains (fast-boot E0→E4 still
    misses the two-batch bar; Spearman(run_order, E0→E4) = 0.55).
    Report-grade numbers need a dedicated machine; this host is for
    harness bring-up.
10. **E3w fidelity.** filter-dump pcap timestamps are a QEMU realtime
    clock that does not match Python `time.time_ns()` (offsets of tens to
    hundreds of ms observed). `e0_to_e3w_ns` is therefore first-connect
    (monotonic from E0) plus the pcap-relative SYN/ACK→HTTP interval, not
    `pcap_epoch - e0_wall`.
11. **Reservation vs working set** (D-0030).
12. **Pre-M4 harness fail-open (finding 31, T4.0b receipt).**
    `scripts/boot-test.sh` ran under `set -u` only; a failed `cargo build`
    left the previous ELF in place, `check-utext` accepted it, QEMU booted
    it, and the harness printed `TEST PASS`. Demonstrated 2026-08-16 on
    `m4-evaluation` before the T4.0b fix: `compile_error!` on screen,
    kernel sha256 unchanged, exit 0. **No report number derives from that
    harness.** T4.1 numbers come from `scripts/bench.sh` on a fail-closed
    `boot-test.sh` tree.
