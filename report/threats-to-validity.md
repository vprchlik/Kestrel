# Threats to validity — seed (T4.0 list, maintained from T4.3)

Opened at T4.0 per D-0064. The draft section in `report/draft.md` is
normative; this file is the seed that section was written from. Each
item is mitigated-and-measured or stated.

1. **TCG ≠ hardware.** Compute-dense phases and MMIO-dense phases are
   taxed differently than on silicon. Every claim carries "under QEMU TCG".
2. **slirp is a peer**, not a wire. E3w−E3g prices virtio+slirp, not a NIC.
   True hostfwd delivery plus client recv is bounded by `D_fin` at
   63–155 µs (D-0070 pcap pass, `report/exhibits/d0070-pcap.md`).
   The former "E3w→E4" term is retired: it was QEMU startup (D-0071)
   plus the accepted connection waiting for the guest to boot
   (D-0070), mislabeled by E3w's anchoring construction.
3. **Client granularity.** Persistent process. Measured median on the
   T4.3 freeze is **1.000 ms** (see `report/exhibits/machine-spec.md`
   `client_granularity_ns`). That cadence is connect-retry only; it
   does not apply after `connect()`. Fork-per-attempt curl was 5–15 ms
   (finding 32).
4. **Single hart and fixed RAM.** The floor is for this machine shape.
5. **Debug-era history is not evidence.** Regeneration from CSV kills
   chat-only numbers (audit findings 16–23).
6. **Linux-tuning fairness** (D-0062) — measured: trimmed beats
   stock by 188.32 ms (`report/exhibits/cross-system.md`). Config
   published: `bench/linux/linux-trimmed.fragment`. A Linux
   boot-time specialist could likely do better; we claim *a*
   minimal Linux, not *the* minimal Linux. On the T4.8 Image,
   `FTRACE` is a recorded miss (`trace_eval_sync` / D-0072).
   D-0073 acts on it; T4.8b is the after. The T4.8 numbers still
   include the miss.

   **Non-reversing select.** Kconfig `select` does not unset the
   target when the selector goes. A helper or transport that
   `select` pulled in stays `=y` whenever it has its own prompt,
   a default, or another selector under a menu the trim did not
   touch. That is a property of trimming a kernel by subsystem;
   it cost three `linux-build` iterations to see. Seven symbols
   on this Image were that shape: `NET_9P`, `DNS_RESOLVER`,
   `NLS`, `MTD`, `DAX`, `IP_PNP`, `EXPORTFS`. The fourth pass
   walked remaining `=y` against live `select` edges in one go
   (`FHANDLE` and overlay still selected `EXPORTFS`; unsetting
   the helper alone would have left it `=m`).

   Walked and kept, with the reason: `CRC32` (`MACB` still
   selects it); `NVMEM` (`NVMEM_SUNXI_SID` still y); `SHMEM`
   (anonymous memory); `EVENTFD` (`default y` syscall);
   `FW_LOADER` (`default y`); `FILE_LOCKING` (`default y`);
   `MACB` / `PHYLIB` / `MICREL_PHY` (sibling NIC, still live);
   `NETFILTER` (defconfig y); `INET_DIAG` (`default y` for
   `ss`); `AUTOFS_FS`, `POSIX_MQUEUE`, `SYSVIPC` (defconfig y);
   `VIRTIO_CONSOLE` / `BALLOON` / `INPUT` (defconfig y extra
   virtio); `XFRM` (`XFRM_USER=m` still a consumer);
   `FAILOVER` / `NET_FAILOVER` (`VIRTIO_NET` selects them);
   `DEBUG_FS`, `FB`, `VT`, `PINCTRL`, `I2C`, `SPI`, `THERMAL`,
   `CPU_IDLE` (named deferred).
7. **Unikraft pin** (D-0063) — stated when that row exists.
8. **Instrumentation observer effect.** Stamp overhead is a generated
   edges-exhibit row (~5.5 µs fast-boot). D-0068 moved
   `print_after_response` after a yield. Two N-trials produced no
   E0→E4 improvement (`report/exhibits/dump-placement.md`). The yield
   stays on principle. The null was later explained: there was no
   post-publish host work for it to move (D-0070).
9. **Host variance.** Report numbers are the dedicated Ubuntu 26.04
   host (7800X3D, 8 cores SMT off, boost off, performance governor,
   QEMU 10.2.1, steal 0). Freeze, T4.4, T4.6, both D-0068
   invocations, and T4.8 each ran two interleaved batches that met
   max(2%, 200 µs). D-0068 additionally reproduced across two
   independent campaigns. The KVM pod failed this criterion and is
   not cited. Steal=0 is necessary, not sufficient (USER_HZ=100) —
   recorded as a surviving T4.1 finding.
10. **E3w fidelity.** filter-dump pcap timestamps are a QEMU realtime
    clock that does not match Python `time.time_ns()` — measured on
    the pod as a per-boot offset of −30 to −846 ms, so no absolute
    `pcap_epoch − e0_wall` quantity is ever usable (D-0071 evidence).
    `e0_to_e3w_ns` was first-connect plus the pcap-relative
    SYN/ACK→HTTP interval; the anchor ("first-connect ≈ SYN/ACK")
    was tested and is false under hostfwd: connect-success is the
    host kernel accepting into QEMU's listen backlog during startup,
    not the guest handshake (D-0070, confirmed). E3w-derived metrics
    are retired; pcap-internal intervals on one clock (`W`, `D_ack`,
    `D_fin`) replace them. No E3w-derived column may appear in a
    cross-system table; E0→first-connect is a same-QEMU control,
    not a comparison.
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
    movement. The dump stays off the interval on principle. Linux and
    Unikraft share slirp/hostfwd and will not share a PHASE dump. If
    measured runs ever stopped printing PHASE, the decomposition and
    E0→E4 would come from different boots — that would be its own
    line.
17. **A derived metric double-counted guest boot under a
    host-sounding name (D-0070/D-0071).** "E3w→E4" folded QEMU
    startup (~6.8 ms) and boot-to-net-init wait (~24/~85 ms) into a
    term labeled host-side delivery. It survived a pre-registered
    audit and four measurement campaigns, and was caught only
    because it moved with kernel rungs a host-side term should not
    respond to. The honest headline (E0→E4, two direct client-clock
    stamps) was never wrong — each piece was counted once there.
    Lesson recorded next to D-0069's: an unexplained constant must
    not keep a plausible-sounding name.
18. **A kernel trimmed this hard cannot be fully instrumented by
    its own debug facility (D-0072).** `initcall_debug` on the T4.8
    cmdline produced zero entries: `loglevel=7` filters
    `KERN_DEBUG` (necessary and sufficient); kallsyms off affects
    names only (`PM: Calling 0xffffffff800614ec`). Stated. The
    decomposition is printk gaps plus `/init` stamps plus
    UART-inflated labels from one `ignore_loglevel` boot of the
    same Image (`report/exhibits/linux-decomposition.md`). Gap 1
    is `trace_eval_sync` (222.6 ms UART-inflated, labeling 68% of
    the 327.24 ms cell, not replacing it). Not a sixth comparison
    arm. On this Image `FTRACE` is a missed trim. D-0073 acts on
    it; T4.8b is the after.
19. **`W` fuses guest boot with egress delay; only a guest-internal
    stamp separates them (T4.8b diagnosis, 2026-08-18).** `w_ns` =
    t(guest SYN/ACK) − t(slirp ARP) spans the guest's entire
    boot-to-first-TX, so an inflated `W` is not attributable to
    boot or to delivery without an instrument that sees the
    boundary. On the T4.8 pin (`ffb7ac7`), the largest E0→E4 / `W`
    excursions (+39, +52, +115, +125 ms) were **slow guest boots**
    — `/init`'s announce stamp late by the same amount, pcap
    first-TX minus announce normal — not delivery faults. T4.8's
    egress-attributed tail is ≤ 7.7 ms (stock) and ≤ 4.3 ms
    (trimmed arms), established read-only by splitting `W` at the
    `/init` CLOCK_MONOTONIC stamps against pcap first-TX over the
    recorded per-trial artifacts. Published medians and the
    188.32 ms trimmed-vs-stock delta are unmoved (≤ 0.037 ms); the
    stability criterion passes on all five arms. The one observed
    ~1 s anomaly is a single T4.8b **warmup** trial
    (`20260818T143032Z-1` trimmed/02: announce at guest-mono
    156.7 ms, first wire TX at pcap 1.263 s, the queued hostfwd SYN
    snapped to slirp's ~6 s RTO); the SYN-grid gate fired, the
    batch aborted, no row published. It is **not** an egress fault.
    It is a **guest-side lost ARP solicit** (D-0074): the guest's
    first solicit never reached the TX ring, and the guest's own
    `neigh` retransmit re-sent it 1.03 s later — past slirp's ~1 s
    ARP-pending drop, which snapped the queued SYN to the ~6 s RTO.
    Reproduced 1 in 50 on the bench host under D-0055 controls,
    matching this trial's `/init` stamps to 0.22 ms.

    **Correction to this item's earlier supporting evidence.** An
    earlier revision of this item offered the absence of any
    comparable stall across the recorded campaigns as evidence of
    robustness. That claim is **withdrawn**, not weakened. It was
    an absence of *observation* under a detector that fires only
    when the delay crosses slirp's ~1 s drop and that runs only on
    Linux trials (a), and it recorded nothing about the quantity
    that governs the race: the margin between the guest's announce
    and the last virtio ctrl-vq completion — ~12.4 ms on the
    current `Image-trimmed`, 0.199 ms on the failing boot. That
    margin was never measured on any earlier image and cannot be
    recovered from the recorded artifacts. Those clean boots are
    equally consistent with the older images having had a larger
    margin, with sub-cliff instances passing unremarked, and with
    luck; the evidence does not distinguish them. What is measured
    is stated in D-0074, whose pre-registered 5000-boot experiment
    exists to replace the withdrawn inference with a rate.

    **Documented instance of the failure mode (the corollary below
    is not hypothetical):** during this diagnosis, an analysis pass
    first attributed the T4.8 excursions above to silent egress
    stalls by reading Δ(E0→E4) ≈ Δ(`W`) with first-connect flat as
    a delivery signature — one step after item 17's lesson was
    recorded in this file. Because `W` fuses boot and delivery,
    ordinary boot jitter produces that signature exactly as a held
    frame does; the guest-stamp split reversed the attribution.
    D-0071's error recurred immediately after being written down,
    which is evidence the pattern is easy to fall into; the
    mitigation is the instrument rule, not vigilance. Corollary,
    beside D-0069's and item 17's: **a metric spanning two
    subsystems must not be attributed to either without an
    instrument that sees the boundary.**

    Two harness facts are recorded rather than fixed:
    (a) **Gate asymmetry — a gap, not just a fact.** The SYN-grid
    gate tests the relative interval t(SYN) − t(guest first TX)
    and is blind to the absolute time of first TX, so it detects
    an egress stall only when the stall pushes first TX past
    slirp's ~1 s ARP-pending drop — and `bench.py` calls it only
    for `system=linux` (`run_trial`). Coverage is therefore
    inversely matched to exposure: Whimbrel fast-boot's silent
    window below the cliff is ~980 ms (first TX ~24 ms) and
    ungated; Linux stock's is ~103 ms (first TX ~897 ms) and
    gated.
    (b) **Whimbrel's guest-side detector is luck, not design.**
    D-0056.3 removed the `wfi` from the boot RX waits to
    un-quantize ARP/ping latency from the 10 ms tick — a
    correctness decision, not instrumentation. Its side effect is
    that a held first-TX frame becomes visible in guest time at
    100 ns grain: `first_rx − DRIVER_OK` stayed flat to 0.56 ms
    worst-case across ~400 boots, the independent bound on
    Whimbrel's egress tail. After D-0074 that interval bounds more
    than egress: it spans request to reply through Whimbrel's own
    stack, so a solicit lost inside the guest would delay
    `first_rx` exactly as a held frame would. Linux's `/init`
    announce is a fire-and-forget `sendto` that observes neither —
    which is why naming that failure needed the pcap and the QEMU
    trace. A later rung re-introducing that `wfi` would degrade
    this visibility to ≥ 10 ms tick grain (D-0056.3's finding-13
    corollary already constrains such a rung); the protection is
    contingent, not guaranteed.
