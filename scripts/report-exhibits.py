#!/usr/bin/env python3
"""Generate the M4 report exhibits from named git objects (D-0064 / D-0067 / D-0071 / D-0072 / D-0079).

The harness overwrites `results/runs.csv` and `results/phases.csv` per
run; they are not an append-only history. Baseline columns therefore
come from tag `baseline-t4.3` via `git show`, after-ladder / Δ
columns from the T4.6 superpage CSV commit, D-0068 dump-placement
from its two CSV commits, the T4.8 cross-system table from that
campaign's CSV commit (frozen as the pre-FTRACE before; D-0073
does not retarget it), the Linux decomposition from the T4.8
serial pin (`d705ecb`), and D-0072 hole labels from the
`ignore_loglevel` pin (`93ab617`). HEAD may hold a later batch;
pins do not follow it. The T4.8b table comes from its own CSV pin
(`a0c53e2`, D-0073 after) with T4.8 frozen as the before. The T4.7
firmware exhibit comes from the t47c CSV pin (`c2759e2`). The
working-tree files are not read — a local `just bench` leftover
cannot become an exhibit.

Never type the numbers this script prints.
`just report-exhibits` regenerates report/exhibits/.
Failing-input selftest: `just report-exhibits-selftest` (does not write exhibits).
"""

from __future__ import annotations

import csv
import io
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "report" / "exhibits"

BASELINE_TAG = "baseline-t4.3"
# T4.6 superpage CSV commit. HEAD may hold a later non-rung batch
# (D-0068 confirmation); after-ladder columns stay this object.
AFTER_REV = "c40945cdb71b5aef68c5e72e292a718b66ec651e"

BASELINE_BATCHES = frozenset({"20260817T041311Z-1", "20260817T041311Z-2"})
BASELINE_SHA_PREFIX = "35861f3"
AFTER_BATCHES = frozenset({"20260817T061753Z-1", "20260817T061753Z-2"})
AFTER_SHA_PREFIX = "76830e13"
# Caption label for the after-ladder CSV pin (not the baseline freeze).
LADDER_LABEL = "superpages"

# D-0068 dump-placement pins. Not ladder rungs; a separate exhibit.
D68_RUN1_REV = "59e070321ab5ec30ff97830ac3f9f78577511db4"
D68_RUN1_SHA_PREFIX = "c40945cd"
D68_RUN1_BATCHES = frozenset({"20260818T013740Z-1", "20260818T013740Z-2"})
D68_RUN2_REV = "4755fa3fe2cf98ded4dd333fa81ca66a2b811cfe"
D68_RUN2_SHA_PREFIX = "59e07032"
D68_RUN2_BATCHES = frozenset({"20260818T014549Z-1", "20260818T014549Z-2"})

# T4.8 five-arm CSV commit. Measured kernel is git_sha 1005399 (not
# this object). New schema: w_ns / d_ack_ns / d_fin_ns, no e0_to_e3w_ns.
# Frozen as the pre-FTRACE (D-0073) before. Do not retarget to T4.8b.
T48_REV = "ffb7ac71234e953ae51339a3e1f5e17ba8c3f1b3"
T48_SHA_PREFIX = "1005399"
T48_BATCHES = frozenset({"20260818T073023Z-1", "20260818T073023Z-2"})
T48_N_PER_ARM = 60
# T4.8b five-arm CSV commit (D-0073 after: FTRACE-swept Image-trimmed +
# D-0075 /init). Measured kernel is git_sha 06687e2 (not this object).
# The T4.8 pin above stays the before; cross-system-t48b.md carries the
# before/after cells. The rev is the annotated tag (object a0c53e2) so
# the exhibit cites a name a reader can resolve, like baseline-t4.3.
T48B_REV = "t48b"
T48B_SHA_PREFIX = "06687e2"
T48B_BATCHES = frozenset({"20260819T142033Z-1", "20260819T142033Z-2"})
T48B_N_PER_ARM = 60
# T4.7 confirmation CSV commit (D-0079). Measured kernel is git_sha
# 346f4c1 (not this object). Four Whimbrel arms, two firmware lanes,
# one batch set. Working-tree CSVs are not read.
T47_REV = "c2759e245bf7cbcf23dcf43ac228b73f06bb0960"
T47_SHA_PREFIX = "346f4c1"
T47_BATCHES = frozenset({"20260820T130700Z-1", "20260820T130700Z-2"})
T47_N_PER_ARM = 60
# t47b: aborted confirmation, recorded-not-published. Not an exhibit
# pin. The selftest plants a cross-campaign pair against T47_REV.
T47B_REV = "793680bcd4fe4174ede1ddd3ec80d9e1135b4b2b"
# T4.8 instrumented + Whimbrel serial pin (decomposition, not CSVs).
SERIAL_REV = "d705ecb8c67350519f9ce4653a4685a89e20e1d4"
LINUX_SERIAL_PATH = (
    "results/serial/linux-trimmed-instrumented-20260818T073023Z-1-t04.log"
)
WHIMBREL_SERIAL_PATH = (
    "results/serial/whimbrel-fast-20260818T073023Z-1-t04.log"
)
# D-0072 diagnostic labels. UART-inflated; they annotate the T4.8
# 327 ms cell, they do not replace it. Not a sixth campaign arm.
# Frozen with T48_REV / SERIAL_REV as the pre-FTRACE exhibit.
LABEL_REV = "93ab617676672f6db7a1d076389f9a049678192a"
LABEL_PATH = (
    "results/serial/"
    "linux-trimmed-ignore-loglevel-20260818T084831Z-initcalls.txt"
)
FRAGMENT_PATH = "bench/linux/linux-trimmed.fragment"
MANIFEST_PATH = "bench/linux/MANIFEST"
T48_ARM_ORDER = (
    ("whimbrel", "release-fast-boot"),
    ("whimbrel", "release-default"),
    ("linux", "trimmed"),
    ("linux", "trimmed-instrumented"),
    ("linux", "stock"),
)
CONTROL_TOL_NS = 1_000_000

SAFE = "release-default"
FAST = "release-fast-boot"
M_FAST = "m-release-fast-boot"
M_SAFE = "m-release-default"
T47_ARM_ORDER = (FAST, SAFE, M_FAST, M_SAFE)
T47_OPENSBI = frozenset({FAST, SAFE})
T47_SHIM = frozenset({M_FAST, M_SAFE})
# D-0079 registered seam set (source inspection, not a measured delta).
T47_SEAM_PHASES = ("stvec", "frame_init", "E3g")
CANARY_FIELDS = ("canary_stvec_ns", "canary_page_verify_ns")
# ΔS bounds (D-0079). Expectation was retired from the fw_dynamic-load
# window (+0.1, +1.5) ms; the corrected window is ΔS ≈ 0, |ΔS| ≤ 0.2 ms.
# Falsifiers are unchanged coarse sanity — they refuse the exhibit.
DS_EXPECT_ABS_NS = 200_000
DS_FALSIFY_SLOWER_NS = -300_000
DS_FALSIFY_ABS_NS = 3_000_000

# Serial order from src/phase.rs NAMES (not a fourth copy of the justfile
# list — this is the exhibit's row order, parsed values still come from CSV).
PHASE_ORDER = [
    "_start",
    "stamp_a",
    "stamp_b",
    "stvec",
    "frame_init",
    "task_init",
    "page_build",
    "page_verify",
    "activate",
    "virtq_init",
    "DRIVER_OK",
    "first_rx",
    "serving_ready",
    "net_init_done",
    "heap_init",
    "accounting",
    "freeze",
    "sret",
    "syn_rx",
    "established",
    "E3g",
    "E3g_doorbell",
]

PHASE_WHAT = {
    "_start": "first kernel instruction (E2); zero-width by construction",
    "stamp_a": "overhead pair, first stamp",
    "stamp_b": "overhead pair, second stamp (the floor)",
    "stvec": "DBCN probe + CSR snapshot + trap install",
    "frame_init": "allocator init: eager free-list link at baseline; bump pointer after T4.4",
    "task_init": "fabricate four task frames (three Exited)",
    "page_build": "Sv39 identity map, mixed 4 KiB / 2 MiB leaves (D-0059)",
    "page_verify": "full second walk of the map (D-0043 paranoia); grain-aware after D-0059",
    "activate": "`satp` write + `sfence.vma`",
    "virtq_init": "first virtqueue program+verify (wiped by later reset)",
    "DRIVER_OK": "virtio-net reset, second program+verify, DRIVER_OK",
    "first_rx": "gateway ARP reply arrived (slirp RTT, not kernel)",
    "serving_ready": "gateway MAC learned; earliest serve point",
    "net_init_done": "GARP + diagnostic `ping_gateway` done",
    "heap_init": "kernel heap init (idle in production images)",
    "accounting": "frames-consumed check: free-list walk at baseline; bump arithmetic after T4.4",
    "freeze": "`FROZEN` store; safe profile also prints `free_count()`",
    "sret": "first `sret` to U-mode",
    "syn_rx": "client SYN arrived (external)",
    "established": "TCP handshake complete",
    "E3g": "HTTP response published to the used ring (D-0043)",
    "E3g_doorbell": "`QueueNotify` store returned (device-model handoff)",
}

PHASE_NECESSARY = {
    "_start": "yes — the origin",
    "stamp_a": "no — instrumentation",
    "stamp_b": "no — instrumentation",
    "stvec": "yes — a trap handler",
    "frame_init": "an allocator, not the O(n) link; T4.4 collapsed it to a bump (D-0065)",
    "task_init": "yes — U-mode task slots",
    "page_build": "yes — Sv39",
    "page_verify": "no — paranoia; kept as its own line (D-0043)",
    "activate": "yes — paging on",
    "virtq_init": "no — discarded first pass (finding 4); still above the 5% bar after superpages",
    "DRIVER_OK": "yes — the NIC; not bundled with virtq_init",
    "first_rx": "no — slirp RTT",
    "serving_ready": "ARP wait; not kernel compute",
    "net_init_done": "no — ping is diagnostic",
    "heap_init": "no — heap is idle (finding 11)",
    "accounting": "no — paranoia; T4.4 subsumed the walk (D-0065)",
    "freeze": "the bool, not a second walk",
    "sret": "yes — U-mode",
    "syn_rx": "external arrival",
    "established": "protocol",
    "E3g": "yes — the byte",
    "E3g_doorbell": "the notify; priced separately (D-0056.2)",
}


class ExhibitFail(Exception):
    pass


def git_show(rev: str, path: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "git show failed"
        raise ExhibitFail(f"TEST FAIL: git show {rev}:{path}: {err}")
    if not proc.stdout:
        raise ExhibitFail(f"TEST FAIL: git show {rev}:{path} was empty")
    return proc.stdout


def read_csv_text(text: str, label: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ExhibitFail(f"TEST FAIL: empty CSV ({label})")
    return rows


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        raise ExhibitFail("TEST FAIL: percentile of empty list")
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def iqr(vals: list[float]) -> float:
    s = sorted(vals)
    return percentile(s, 0.75) - percentile(s, 0.25)


def fmt_ns(ns: float) -> str:
    mag = abs(ns)
    if mag >= 1_000_000:
        return f"{ns / 1e6:.2f} ms"
    if mag >= 1_000:
        return f"{ns / 1e3:.1f} µs"
    return f"{ns:.0f} ns"


def fmt_delta(ns: float) -> str:
    if ns == 0:
        return "0"
    sign = "−" if ns < 0 else "+"
    return sign + fmt_ns(abs(ns))


def fmt_ratio(num: float, den: float) -> str:
    if den == 0:
        raise ExhibitFail("TEST FAIL: ratio denominator is 0")
    return f"{num / den:.1f}×"


def fmt_ms3(ns: float) -> str:
    return f"{ns / 1e6:.3f} ms"


def fmt_signed_ms3(ns: float) -> str:
    if ns == 0:
        return "0.000 ms"
    sign = "−" if ns < 0 else "+"
    return f"{sign}{abs(ns) / 1e6:.3f} ms"


def md_cell(s: str) -> str:
    return s.replace("|", "\\|")


def recorded(rows: list[dict]) -> list[dict]:
    return [r for r in rows if int(r["warmup"]) == 0]


def runs_schema(fieldnames) -> str:
    fields = set(fieldnames)
    has_e3w = "e0_to_e3w_ns" in fields
    new_cols = {"w_ns", "d_ack_ns", "d_fin_ns"}
    has_new = new_cols <= fields
    if has_e3w and has_new:
        raise ExhibitFail(
            "TEST FAIL: mixed runs.csv schema "
            "(e0_to_e3w_ns and w_ns/d_ack_ns/d_fin_ns both present)"
        )
    if has_e3w:
        extra = new_cols & fields
        if extra:
            raise ExhibitFail(
                "TEST FAIL: mixed runs.csv schema "
                f"(e0_to_e3w_ns with partial new columns {sorted(extra)})"
            )
        return "old"
    if has_new:
        return "new"
    raise ExhibitFail(
        "TEST FAIL: incomplete runs.csv schema "
        "(need e0_to_e3w_ns without w_ns/d_ack_ns/d_fin_ns, "
        "or w_ns/d_ack_ns/d_fin_ns without e0_to_e3w_ns)"
    )


def validate(
    runs: list[dict],
    phases: list[dict],
    want_batches: frozenset[str],
    want_sha_prefix: str,
    label: str,
    *,
    n_per_cfg: int = 60,
) -> None:
    rec = recorded(runs)
    if runs_schema(runs[0].keys()) != "old":
        raise ExhibitFail(
            f"TEST FAIL: {label} is not old-schema "
            "(historical pins keep e0_to_e3w_ns; T4.8 is a different pin)"
        )
    batches = {r["batch_id"] for r in runs}
    if batches != want_batches:
        raise ExhibitFail(
            f"TEST FAIL: {label} batch_id set {sorted(batches)} "
            f"want {sorted(want_batches)}"
        )
    shas = {r["git_sha"] for r in rec}
    if len(shas) != 1:
        raise ExhibitFail(f"TEST FAIL: {label} mixed git_sha {sorted(shas)}")
    sha = next(iter(shas))
    if not sha.startswith(want_sha_prefix):
        raise ExhibitFail(
            f"TEST FAIL: {label} git_sha {sha} does not start with "
            f"{want_sha_prefix}"
        )
    if any(int(r["dirty"]) != 0 for r in rec):
        raise ExhibitFail(f"TEST FAIL: dirty-tree row in {label}")
    cfgs = {r["config"] for r in rec}
    if cfgs != {SAFE, FAST}:
        raise ExhibitFail(
            f"TEST FAIL: {label} configs {sorted(cfgs)} want {SAFE}, {FAST}"
        )
    for cfg in (SAFE, FAST):
        n = sum(1 for r in rec if r["config"] == cfg)
        if n != n_per_cfg:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} has {n} recorded trials, "
                f"want {n_per_cfg}"
            )
    steal = [int(r["steal_ticks"]) for r in rec]
    if any(s != 0 for s in steal):
        raise ExhibitFail(
            f"TEST FAIL: nonzero steal_ticks in recorded {label} "
            f"(nonzero={sum(1 for s in steal if s != 0)}/{len(steal)})"
        )
    # Unreachable if the per-config counts and config-set checks hold.
    # Kept as a belt; the selftest cannot plant it independently.
    if len(rec) != n_per_cfg * 2:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(rec)} recorded trials, "
            f"want {n_per_cfg * 2}"
        )
    for field, want in (
        ("virt", "none"),
        ("governor", "performance"),
        ("smt_control", "off"),
        ("cpufreq_boost", "0"),
    ):
        if field not in rec[0]:
            raise ExhibitFail(f"TEST FAIL: {label} runs.csv missing {field}")
        vals = {r[field] for r in rec}
        if vals != {want}:
            raise ExhibitFail(
                f"TEST FAIL: {label} {field} values {sorted(vals)} "
                f"want {{{want!r}}}"
            )
    rec_keys = {(r["batch_id"], r["trial"], r["config"]) for r in rec}
    e3g = [
        p
        for p in phases
        if int(p["warmup"]) == 0
        and p["phase"] == "E3g"
        and (p["batch_id"], p["trial"], p["config"]) in rec_keys
    ]
    want_e3g = n_per_cfg * 2
    if len(e3g) != want_e3g:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(e3g)} recorded E3g rows, "
            f"want {want_e3g}"
        )


def parse_linux_manifest(text: str) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 3 and parts[0] == "artifact":
            artifacts[parts[1]] = parts[2]
    want = ("Image-stock", "Image-trimmed", "rootfs.cpio", "init")
    missing = [n for n in want if n not in artifacts]
    if missing:
        raise ExhibitFail(f"TEST FAIL: MANIFEST missing {missing}")
    return artifacts


def validate_t48(
    runs: list[dict],
    phases: list[dict],
    *,
    rev: str = T48_REV,
    batches: frozenset[str] = T48_BATCHES,
    sha_prefix: str = T48_SHA_PREFIX,
    n_per_arm: int = T48_N_PER_ARM,
    label: str = "T4.8",
    manifest_text: str | None = None,
) -> None:
    if runs_schema(runs[0].keys()) != "new":
        raise ExhibitFail(f"TEST FAIL: {label} is not new-schema")
    rec = recorded(runs)
    batches_got = {r["batch_id"] for r in runs}
    if batches_got != batches:
        raise ExhibitFail(
            f"TEST FAIL: {label} batch_id set {sorted(batches_got)} "
            f"want {sorted(batches)}"
        )
    shas = {r["git_sha"] for r in rec}
    if len(shas) != 1:
        raise ExhibitFail(f"TEST FAIL: {label} mixed git_sha {sorted(shas)}")
    sha = next(iter(shas))
    if not sha.startswith(sha_prefix):
        raise ExhibitFail(
            f"TEST FAIL: {label} git_sha {sha} does not start with "
            f"{sha_prefix}"
        )
    if any(int(r["dirty"]) != 0 for r in rec):
        raise ExhibitFail(f"TEST FAIL: dirty-tree row in {label}")
    want_cfgs = {cfg for _sys, cfg in T48_ARM_ORDER}
    cfgs = {r["config"] for r in rec}
    if cfgs != want_cfgs:
        raise ExhibitFail(
            f"TEST FAIL: {label} configs {sorted(cfgs)} want {sorted(want_cfgs)}"
        )
    for sys, cfg in T48_ARM_ORDER:
        n = sum(1 for r in rec if r["config"] == cfg)
        if n != n_per_arm:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} has {n} recorded trials, "
                f"want {n_per_arm}"
            )
        systems = {r["system"] for r in rec if r["config"] == cfg}
        if systems != {sys}:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} system {sorted(systems)} want {sys}"
            )
    steal = [int(r["steal_ticks"]) for r in rec]
    if any(s != 0 for s in steal):
        raise ExhibitFail(
            f"TEST FAIL: nonzero steal_ticks in recorded {label} "
            f"(nonzero={sum(1 for s in steal if s != 0)}/{len(steal)})"
        )
    # Unreachable if the per-arm counts and config-set checks hold.
    if len(rec) != n_per_arm * len(T48_ARM_ORDER):
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(rec)} recorded trials, "
            f"want {n_per_arm * len(T48_ARM_ORDER)}"
        )
    for field, want in (
        ("virt", "none"),
        ("governor", "performance"),
        ("smt_control", "off"),
        ("cpufreq_boost", "0"),
    ):
        vals = {r[field] for r in rec}
        if vals != {want}:
            raise ExhibitFail(
                f"TEST FAIL: {label} {field} values {sorted(vals)} "
                f"want {{{want!r}}}"
            )
    man = parse_linux_manifest(
        manifest_text
        if manifest_text is not None
        else git_show(rev, "bench/linux/MANIFEST")
    )
    for cfg, image in (
        ("stock", "Image-stock"),
        ("trimmed", "Image-trimmed"),
        ("trimmed-instrumented", "Image-trimmed"),
    ):
        got = {r["kernel_sha256"] for r in rec if r["config"] == cfg}
        if got != {man[image]}:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} kernel_sha256 {sorted(got)} "
                f"want MANIFEST {image}={man[image]}"
            )
    conn_meds = []
    for _sys, cfg in T48_ARM_ORDER:
        vals = [
            float(r["e0_to_first_connect_ns"])
            for r in rec
            if r["config"] == cfg
        ]
        conn_meds.append(statistics.median(vals))
    span = max(conn_meds) - min(conn_meds)
    if span > CONTROL_TOL_NS:
        raise ExhibitFail(
            f"TEST FAIL: {label} first-connect medians span {span:.0f} ns "
            f"(> 1 ms): {conn_meds}"
        )
    e4 = {
        cfg: statistics.median(
            [float(r["e0_to_e4_ns"]) for r in rec if r["config"] == cfg]
        )
        for _sys, cfg in T48_ARM_ORDER
    }
    if e4["trimmed"] >= e4["stock"]:
        raise ExhibitFail(
            f"TEST FAIL: {label} trimmed E0→E4 {e4['trimmed']:.0f} ns ≥ "
            f"stock {e4['stock']:.0f} ns (tripwire; trimmed is not published)"
        )
    rec_keys = {(r["batch_id"], r["trial"], r["config"]) for r in rec}
    linux_ph = [
        p
        for p in phases
        if int(p["warmup"]) == 0 and p.get("system") == "linux"
    ]
    if linux_ph:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(linux_ph)} Linux PHASE rows "
            "(Linux writes none)"
        )
    e3g = [
        p
        for p in phases
        if int(p["warmup"]) == 0
        and p["phase"] == "E3g"
        and p["config"] in {FAST, SAFE}
        and (p["batch_id"], p["trial"], p["config"]) in rec_keys
    ]
    want_e3g = n_per_arm * 2
    if len(e3g) != want_e3g:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(e3g)} recorded Whimbrel E3g rows, "
            f"want {want_e3g}"
        )


def s_trial_ns(row: dict) -> float:
    """S = (E4 − first_connect) − pcap(ARP → FIN). Same formula as
    `scripts/bench.py::s_trial_ns`. S is not a CSV column."""
    for key in (
        "e0_to_e4_ns",
        "e0_to_first_connect_ns",
        "w_ns",
        "synack_to_http_ns",
        "d_fin_ns",
    ):
        if row.get(key) in (None, ""):
            raise ExhibitFail(f"TEST FAIL: missing {key} for S")
    return (
        float(row["e0_to_e4_ns"]) - float(row["e0_to_first_connect_ns"])
    ) - (
        float(row["w_ns"])
        + float(row["synack_to_http_ns"])
        + float(row["d_fin_ns"])
    )


def s_vals(rec: list[dict], config: str) -> list[float]:
    """One config, never concatenated across lanes (D-0062 / D-0071 / D-0079)."""
    vals = [s_trial_ns(r) for r in rec if r["config"] == config]
    if not vals:
        raise ExhibitFail(f"TEST FAIL: no S values for {config}")
    return vals


def cfg_median_batch(
    rec: list[dict], config: str, field: str, batch_id: str
) -> float:
    vals = [
        float(r[field])
        for r in rec
        if r["config"] == config and r["batch_id"] == batch_id
    ]
    if not vals:
        raise ExhibitFail(
            f"TEST FAIL: no {field} rows for {config} batch {batch_id}"
        )
    return statistics.median(vals)


def stability_tol_ns(median_ns: float) -> float:
    return max(0.02 * abs(median_ns), 200_000.0)


def require_e2e3g_stable(
    rec: list[dict],
    phases: list[dict],
    config: str,
    label: str,
) -> None:
    batches = sorted({r["batch_id"] for r in rec})
    if len(batches) != 2:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(batches)} batch_id values, "
            "want 2 (Claim A is stability-gated)"
        )
    meds = [
        statistics.median(e2e3g_vals(rec, phases, config, batch_id=b))
        for b in batches
    ]
    if max(meds) < 1_000_000:
        return
    tol = stability_tol_ns(max(meds))
    if abs(meds[0] - meds[1]) > tol:
        raise ExhibitFail(
            f"TEST FAIL: {label} {config} E2→E3g not stable across batches "
            f"(medians {meds[0]:.0f} vs {meds[1]:.0f} ns, "
            f"Δ={abs(meds[0] - meds[1]):.0f}, tol={tol:.0f}; "
            "Claim A is stability-gated)"
        )


def require_delta_s_sane(rec: list[dict], label: str) -> float:
    """ΔS = S(OpenSBI fast) − S(shim fast). Never pooled across lanes."""
    delta = statistics.median(s_vals(rec, FAST)) - statistics.median(
        s_vals(rec, M_FAST)
    )
    if delta < DS_FALSIFY_SLOWER_NS:
        raise ExhibitFail(
            f"TEST FAIL: {label} ΔS = {delta:.0f} ns < −0.3 ms "
            "(variant made startup slower; unmodelled cost)"
        )
    if abs(delta) > DS_FALSIFY_ABS_NS:
        raise ExhibitFail(
            f"TEST FAIL: {label} |ΔS| = {abs(delta):.0f} ns > 3 ms "
            "(E0-side contaminated; no firmware saving is published)"
        )
    return delta


def validate_t47(
    runs: list[dict],
    phases: list[dict],
    *,
    batches: frozenset[str] = T47_BATCHES,
    sha_prefix: str = T47_SHA_PREFIX,
    n_per_arm: int = T47_N_PER_ARM,
    label: str = "T4.7",
) -> None:
    """One campaign, two firmware lanes, or the exhibit does not generate.

    Binding gate (D-0079): the OpenSBI lane and the shim lane share one
    batch set and one canary in the shared header. Mixing lanes from
    different campaigns is a validator failure, not a caption.
    """
    if runs_schema(runs[0].keys()) != "new":
        raise ExhibitFail(f"TEST FAIL: {label} is not new-schema")
    rec = recorded(runs)
    for field in CANARY_FIELDS:
        if field not in runs[0]:
            raise ExhibitFail(
                f"TEST FAIL: {label} missing canary column {field} "
                "(D-0079: one canary in the shared header)"
            )
    cfgs = {r["config"] for r in rec}
    want_cfgs = set(T47_ARM_ORDER)
    if cfgs != want_cfgs:
        raise ExhibitFail(
            f"TEST FAIL: {label} configs {sorted(cfgs)} "
            f"want {sorted(want_cfgs)}"
        )
    opensbi_batches = {
        r["batch_id"] for r in rec if r["config"] in T47_OPENSBI
    }
    shim_batches = {r["batch_id"] for r in rec if r["config"] in T47_SHIM}
    if opensbi_batches != shim_batches:
        raise ExhibitFail(
            f"TEST FAIL: {label} lanes from different batch sets "
            f"(OpenSBI {sorted(opensbi_batches)} vs shim "
            f"{sorted(shim_batches)}; D-0079: one campaign or the "
            "exhibit does not generate)"
        )
    batches_got = {r["batch_id"] for r in runs}
    if batches_got != batches:
        raise ExhibitFail(
            f"TEST FAIL: {label} batch_id set {sorted(batches_got)} "
            f"want {sorted(batches)}"
        )
    canaries = set()
    for r in runs:
        pair = tuple(r.get(f, "") for f in CANARY_FIELDS)
        if any(v in (None, "") for v in pair):
            raise ExhibitFail(
                f"TEST FAIL: {label} empty canary columns "
                "(D-0079: one canary in the shared header)"
            )
        canaries.add(pair)
    if len(canaries) != 1:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(canaries)} canary values "
            "in the shared header, want 1 (D-0079: lanes from "
            "different campaigns do not generate)"
        )
    shas = {r["git_sha"] for r in rec}
    if len(shas) != 1:
        raise ExhibitFail(f"TEST FAIL: {label} mixed git_sha {sorted(shas)}")
    sha = next(iter(shas))
    if not sha.startswith(sha_prefix):
        raise ExhibitFail(
            f"TEST FAIL: {label} git_sha {sha} does not start with "
            f"{sha_prefix}"
        )
    if any(int(r["dirty"]) != 0 for r in rec):
        raise ExhibitFail(f"TEST FAIL: dirty-tree row in {label}")
    for cfg in T47_ARM_ORDER:
        n = sum(1 for r in rec if r["config"] == cfg)
        if n != n_per_arm:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} has {n} recorded trials, "
                f"want {n_per_arm}"
            )
        systems = {r["system"] for r in rec if r["config"] == cfg}
        if systems != {"whimbrel"}:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} system {sorted(systems)} "
                "want whimbrel"
            )
    steal = [int(r["steal_ticks"]) for r in rec]
    if any(s != 0 for s in steal):
        raise ExhibitFail(
            f"TEST FAIL: nonzero steal_ticks in recorded {label} "
            f"(nonzero={sum(1 for s in steal if s != 0)}/{len(steal)})"
        )
    if len(rec) != n_per_arm * len(T47_ARM_ORDER):
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(rec)} recorded trials, "
            f"want {n_per_arm * len(T47_ARM_ORDER)}"
        )
    for field, want in (
        ("virt", "none"),
        ("governor", "performance"),
        ("smt_control", "off"),
        ("cpufreq_boost", "0"),
    ):
        if field not in rec[0]:
            raise ExhibitFail(f"TEST FAIL: {label} runs.csv missing {field}")
        vals = {r[field] for r in rec}
        if vals != {want}:
            raise ExhibitFail(
                f"TEST FAIL: {label} {field} values {sorted(vals)} "
                f"want {{{want!r}}}"
            )
    bios = {r.get("bios_sha256", "") for r in rec if r["config"] in T47_SHIM}
    if len(bios) != 1 or "" in bios:
        raise ExhibitFail(
            f"TEST FAIL: {label} shim bios_sha256 {sorted(bios)} "
            "(want one non-empty blob hash on every shim row)"
        )
    conn_meds = []
    for cfg in T47_ARM_ORDER:
        vals = [
            float(r["e0_to_first_connect_ns"])
            for r in rec
            if r["config"] == cfg
        ]
        conn_meds.append(statistics.median(vals))
    span = max(conn_meds) - min(conn_meds)
    if span > CONTROL_TOL_NS:
        raise ExhibitFail(
            f"TEST FAIL: {label} first-connect medians span {span:.0f} ns "
            f"(> 1 ms): {conn_meds}"
        )
    rec_keys = {(r["batch_id"], r["trial"], r["config"]) for r in rec}
    e3g = [
        p
        for p in phases
        if int(p["warmup"]) == 0
        and p["phase"] == "E3g"
        and (p["batch_id"], p["trial"], p["config"]) in rec_keys
    ]
    want_e3g = n_per_arm * len(T47_ARM_ORDER)
    if len(e3g) != want_e3g:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(e3g)} recorded E3g rows, "
            f"want {want_e3g}"
        )
    for cfg in (FAST, M_FAST):
        for phase in T47_SEAM_PHASES:
            n_ph = sum(
                1
                for p in phases
                if int(p["warmup"]) == 0
                and p["phase"] == phase
                and p["config"] == cfg
                and (p["batch_id"], p["trial"], p["config"]) in rec_keys
            )
            if n_ph != n_per_arm:
                raise ExhibitFail(
                    f"TEST FAIL: {label} {cfg} has {n_ph} recorded "
                    f"{phase} rows, want {n_per_arm}"
                )
        require_e2e3g_stable(rec, phases, cfg, label)
    require_delta_s_sane(rec, label)


def phase_deltas(
    rec_runs: list[dict], phases: list[dict], config: str
) -> dict[str, list[float]]:
    keys = {
        (r["batch_id"], r["trial"], r["config"])
        for r in rec_runs
        if r["config"] == config
    }
    out: dict[str, list[float]] = defaultdict(list)
    for p in phases:
        if int(p["warmup"]) != 0:
            continue
        if (p["batch_id"], p["trial"], p["config"]) not in keys:
            continue
        out[p["phase"]].append(float(p["delta_ns"]))
        out[f"{p['phase']}_since"].append(float(p["ns_since_e2"]))
    return out


def stat(vals: list[float]) -> tuple[float, float, float]:
    if not vals:
        raise ExhibitFail("TEST FAIL: empty metric")
    return statistics.median(vals), iqr(vals), min(vals)


def e2e3g_vals(
    rec: list[dict],
    phases: list[dict],
    config: str,
    batch_id: str | None = None,
) -> list[float]:
    keys = {
        (r["batch_id"], r["trial"])
        for r in rec
        if r["config"] == config
        and (batch_id is None or r["batch_id"] == batch_id)
    }
    vals = [
        float(p["ns_since_e2"])
        for p in phases
        if int(p["warmup"]) == 0
        and p["phase"] == "E3g"
        and p["config"] == config
        and (p["batch_id"], p["trial"]) in keys
        and p.get("ns_since_e2") not in (None, "")
    ]
    if not vals:
        where = config if batch_id is None else f"{config} batch {batch_id}"
        raise ExhibitFail(f"TEST FAIL: no E2→E3g values for {where}")
    return vals


def e2e3g_median(
    rec: list[dict],
    phases: list[dict],
    config: str,
    batch_id: str | None = None,
) -> float:
    return statistics.median(e2e3g_vals(rec, phases, config, batch_id))


def csv_field_block(rec: list[dict], label: str) -> list[str]:
    row = rec[0]
    return [
        f"# {label} (CSV fields; not results/summary.txt)",
        f"qemu_version={row['qemu_version']}",
        f"qemu_hash={row['qemu_hash']}",
        f"git_sha={row['git_sha']} dirty={row['dirty']}",
        f"host_kernel={row['host_kernel']}",
        f"cpu_model={row['cpu_model']}",
        f"governor={row['governor']} smt_control={row['smt_control']} "
        f"cpufreq_boost={row['cpufreq_boost']} virt={row['virt']} "
        f"steal_start_ticks={row['steal_start_ticks']} "
        f"loadavg_1m={row['loadavg_1m']}",
        f"client_granularity_ns={row['client_granularity_ns']}",
        f"shuffle_seed={row['shuffle_seed']}",
    ]


def write_machine_spec(
    base_rec: list[dict],
    after_rec: list[dict],
    baseline_summary: str,
    t48_rec: list[dict] | None = None,
) -> str:
    header: list[str] = []
    for line in baseline_summary.splitlines():
        if line.startswith("## "):
            break
        header.append(line.rstrip())
    while header and header[-1] == "":
        header.pop()
    extra = [
        "",
        f"tag:                   {BASELINE_TAG}",
        f"n_recorded:            {len(base_rec)}",
        f"batches:               {', '.join(sorted(BASELINE_BATCHES))}",
        f"source:                git show {BASELINE_TAG}:results/baseline-summary.txt",
    ]
    after_block = [
        "",
        f"## after-ladder ({LADDER_LABEL})",
        "",
        *csv_field_block(after_rec, f"{LADDER_LABEL} {AFTER_REV[:12]}"),
        f"rev:                   {AFTER_REV}",
        f"n_recorded:            {len(after_rec)}",
        f"batches:               {', '.join(sorted(AFTER_BATCHES))}",
        f"source:                git show {AFTER_REV}:results/runs.csv",
    ]
    t48_block: list[str] = []
    if t48_rec:
        t48_block = [
            "",
            "## T4.8 five-arm campaign",
            "",
            *csv_field_block(t48_rec, f"T4.8 {T48_REV[:12]}"),
            f"rev:                   {T48_REV}",
            f"n_recorded:            {len(t48_rec)}",
            f"batches:               {', '.join(sorted(T48_BATCHES))}",
            f"source:                git show {T48_REV}:results/runs.csv",
        ]
    return (
        "<!-- generated by scripts/report-exhibits.py — do not edit -->\n\n"
        + "\n".join(header)
        + "\n"
        + "\n".join(extra)
        + "\n"
        + "\n".join(after_block)
        + "\n"
        + "\n".join(t48_block)
        + "\n"
    )


def write_phase_table(
    base_rec: list[dict],
    base_phases: list[dict],
    after_rec: list[dict],
    after_phases: list[dict],
    e2e3g_after_fast: float,
) -> str:
    safe = phase_deltas(base_rec, base_phases, SAFE)
    fast = phase_deltas(base_rec, base_phases, FAST)
    after_fast = phase_deltas(after_rec, after_phases, FAST)
    header = (
        "| phase | what the work is | safe median | fast median | "
        "fast IQR | fast min | after-ladder median | Δ vs baseline | "
        "structurally necessary? |"
    )
    sep = "|---|---|---:|---:|---:|---:|---:|---:|---|"
    rows = [header, sep]
    for name in PHASE_ORDER:
        if name not in fast and name not in safe:
            continue
        s_med = stat(safe[name])[0] if name in safe else None
        f_med, f_iqr, f_min = stat(fast[name]) if name in fast else (None, None, None)
        if name in after_fast:
            a_med = stat(after_fast[name])[0]
            after_cell = fmt_ns(a_med)
            delta_cell = fmt_delta(a_med - f_med) if f_med is not None else "—"
        else:
            after_cell = "—"
            delta_cell = "—"
        rows.append(
            "| "
            + " | ".join(
                [
                    md_cell(name),
                    md_cell(PHASE_WHAT.get(name, "")),
                    fmt_ns(s_med) if s_med is not None else "—",
                    fmt_ns(f_med) if f_med is not None else "—",
                    fmt_ns(f_iqr) if f_iqr is not None else "—",
                    fmt_ns(f_min) if f_min is not None else "—",
                    after_cell,
                    delta_cell,
                    md_cell(PHASE_NECESSARY.get(name, "")),
                ]
            )
            + " |"
        )
    share_lines = [
        "",
        f"After-ladder ({LADDER_LABEL}) fast-boot E2→E3g median is "
        f"**{fmt_ns(e2e3g_after_fast)}** (share denominator; baseline "
        f"fast E2→E3g stays in the columns to the left). Share is "
        f"(after-ladder phase median) / (after-ladder E2→E3g median), "
        "not a median of ratios.",
        "",
        f"| phase | {LADDER_LABEL} fast median | share of {LADDER_LABEL} E2→E3g |",
        "|---|---:|---:|",
    ]
    ranked = []
    for name in PHASE_ORDER:
        if name not in after_fast:
            continue
        med = stat(after_fast[name])[0]
        ranked.append((med, name))
    ranked.sort(reverse=True)
    for med, name in ranked:
        share = 100.0 * med / e2e3g_after_fast if e2e3g_after_fast else 0.0
        share_lines.append(
            f"| {md_cell(name)} | {fmt_ns(med)} | {share:.0f}% |"
        )
    caption = (
        f"<!-- generated by scripts/report-exhibits.py — do not edit -->\n\n"
        f"Safe / fast / IQR / min columns: tag `{BASELINE_TAG}` via "
        f"`git show {BASELINE_TAG}:results/{{runs,phases}}.csv` "
        f"(batches `{sorted(BASELINE_BATCHES)[0]}` / "
        f"`{sorted(BASELINE_BATCHES)[1]}`, n=60 per config).\n\n"
        f"After-ladder and Δ columns: `{AFTER_REV}` via "
        f"`git show {AFTER_REV}:results/{{runs,phases}}.csv` "
        f"({LADDER_LABEL} batches `{sorted(AFTER_BATCHES)[0]}` / "
        f"`{sorted(AFTER_BATCHES)[1]}`, measured kernel "
        f"`{AFTER_SHA_PREFIX}`, n=60 per config). After-ladder is the "
        f"fast-boot median. Δ is after-ladder minus baseline fast. "
        f"Working-tree CSVs are not read. Regeneration: `just report-exhibits`.\n\n"
    )
    return caption + "\n".join(rows) + "\n" + "\n".join(share_lines) + "\n"


def edge_vals(
    rec: list[dict], phases: list[dict], config: str
) -> dict[str, list[float]]:
    cfg_rows = [r for r in rec if r["config"] == config]
    rec_keys = {(r["batch_id"], r["trial"]) for r in cfg_rows}
    e3g: list[float] = []
    doorbell: list[float] = []
    overhead: list[float] = []
    for p in phases:
        if int(p["warmup"]) != 0:
            continue
        if p["config"] != config:
            continue
        if (p["batch_id"], p["trial"]) not in rec_keys:
            continue
        if p["phase"] == "E3g":
            e3g.append(float(p["ns_since_e2"]))
        if p["phase"] == "E3g_doorbell":
            doorbell.append(float(p["delta_ns"]))
        if p["phase"] == "stamp_b" and config == FAST:
            overhead.append(float(p["delta_ns"]))
    connect = [float(r["e0_to_first_connect_ns"]) for r in cfg_rows]
    e3w = [float(r["e0_to_e3w_ns"]) for r in cfg_rows]
    e4 = [float(r["e0_to_e4_ns"]) for r in cfg_rows]
    gap = [a - b for a, b in zip(e4, e3w)]
    out: dict[str, list[float]] = {
        "E0→first-connect": connect,
        "E0→E3w": e3w,
        "E0→E4": e4,
        "E3w→E4": gap,
        "E2→E3g": e3g,
        "E3g_doorbell − E3g": doorbell,
    }
    if overhead:
        out["stamp overhead (`stamp_b`−`stamp_a`)"] = overhead
    return out


def edge_vals_new(
    rec: list[dict], phases: list[dict], config: str
) -> dict[str, list[float]]:
    """New-schema edges. Never E0→E3w / E3w→E4."""
    cfg_rows = [r for r in rec if r["config"] == config]
    rec_keys = {(r["batch_id"], r["trial"]) for r in cfg_rows}
    e3g: list[float] = []
    overhead: list[float] = []
    for p in phases:
        if int(p["warmup"]) != 0:
            continue
        if p["config"] != config:
            continue
        if (p["batch_id"], p["trial"]) not in rec_keys:
            continue
        if p["phase"] == "E3g":
            e3g.append(float(p["ns_since_e2"]))
        if p["phase"] == "stamp_b":
            overhead.append(float(p["delta_ns"]))
    out: dict[str, list[float]] = {
        "E0→first-connect (control)": [
            float(r["e0_to_first_connect_ns"]) for r in cfg_rows
        ],
        "E0→E4": [float(r["e0_to_e4_ns"]) for r in cfg_rows],
        "D_fin": [float(r["d_fin_ns"]) for r in cfg_rows],
        "D_ack": [float(r["d_ack_ns"]) for r in cfg_rows],
    }
    if cfg_rows and cfg_rows[0].get("system") == "whimbrel":
        out["W"] = [float(r["w_ns"]) for r in cfg_rows]
        out["E2→E3g"] = e3g
        if overhead:
            out["stamp overhead (`stamp_b`−`stamp_a`)"] = overhead
    return out


def append_edge_table(
    lines: list[str],
    rec: list[dict],
    phases: list[dict],
    title: str,
) -> None:
    lines.extend(
        [
            title,
            "",
            "| config | metric | n | median | IQR | min |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for cfg in (FAST, SAFE):
        vals = edge_vals(rec, phases, cfg)
        for metric in (
            "E0→first-connect",
            "E0→E3w",
            "E0→E4",
            "E3w→E4",
            "E2→E3g",
            "E3g_doorbell − E3g",
        ):
            med, iq, mn = stat(vals[metric])
            lines.append(
                f"| {cfg} | {metric} | {len(vals[metric])} | {fmt_ns(med)} | "
                f"{fmt_ns(iq)} | {fmt_ns(mn)} |"
            )
    overhead = edge_vals(rec, phases, FAST).get(
        "stamp overhead (`stamp_b`−`stamp_a`)"
    )
    if overhead:
        med, iq, mn = stat(overhead)
        lines.append(
            f"| {FAST} | stamp overhead (`stamp_b`−`stamp_a`) | "
            f"{len(overhead)} | {fmt_ns(med)} | {fmt_ns(iq)} | {fmt_ns(mn)} |"
        )
    lines.append("")


def write_edges(
    base_rec: list[dict],
    base_phases: list[dict],
    after_rec: list[dict],
    after_phases: list[dict],
    t48_rec: list[dict] | None = None,
    t48_phases: list[dict] | None = None,
) -> str:
    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        "Host-observed edges and guest E2→E3g. Warmup excluded, both "
        "batches of each freeze pooled (n=60 recorded per config). "
        "E3w is first-connect plus the pcap-relative SYN/ACK→HTTP "
        "interval (D-0043); E3w→E4 is `e0_to_e4_ns − e0_to_e3w_ns`. "
        "Those two metrics are retired (D-0070 / D-0071) and retained "
        "here only as the record of the mislabeling.",
        "",
        f"Baseline sourced from `git show {BASELINE_TAG}:results/"
        "{runs,phases}.csv`. After-ladder sourced from "
        f"`git show {AFTER_REV}:results/{{runs,phases}}.csv`.",
        "",
    ]
    append_edge_table(
        lines,
        base_rec,
        base_phases,
        f"### Baseline (`{BASELINE_TAG}`)",
    )
    append_edge_table(
        lines,
        after_rec,
        after_phases,
        f"### After-ladder ({LADDER_LABEL}, `{AFTER_REV[:12]}`)",
    )
    if t48_rec is not None and t48_phases is not None:
        lines.extend(
            [
                f"### T4.8 Whimbrel arms (`{T48_REV[:12]}`, new schema)",
                "",
                "Same host and QEMU as the cross-system campaign; three "
                "Linux arms interleaved. `csum=off` / TSO-family off on "
                "the shared virtio-net-device args (no-op for Whimbrel). "
                "E0→first-connect is a control. W is guest-boot wait "
                "(SYN/ACK − slirp ARP), Whimbrel-only — it does not "
                "appear on the cross-system table. Never E0→E3w / E3w→E4.",
                "",
                "| config | metric | n | median | IQR | min |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        metric_order = (
            "E0→first-connect (control)",
            "E0→E4",
            "E2→E3g",
            "D_fin",
            "W",
            "D_ack",
            "stamp overhead (`stamp_b`−`stamp_a`)",
        )
        for cfg in (FAST, SAFE):
            vals = edge_vals_new(t48_rec, t48_phases, cfg)
            for metric in metric_order:
                if metric not in vals:
                    continue
                med, iq, mn = stat(vals[metric])
                lines.append(
                    f"| {cfg} | {metric} | {len(vals[metric])} | "
                    f"{fmt_ns(med)} | {fmt_ns(iq)} | {fmt_ns(mn)} |"
                )
        lines.append("")
    return "\n".join(lines)


def cfg_median(rec: list[dict], config: str, field: str) -> float:
    vals = [float(r[field]) for r in rec if r["config"] == config]
    if not vals:
        raise ExhibitFail(f"TEST FAIL: no {field} rows for {config}")
    return statistics.median(vals)


def cfg_iqr(rec: list[dict], config: str, field: str) -> float:
    vals = [float(r[field]) for r in rec if r["config"] == config]
    return iqr(vals)


def write_cross_system(
    t48_rec: list[dict],
    t48_phases: list[dict],
    t46_rec: list[dict],
    t46_phases: list[dict],
) -> str:
    """T4.8 comparison table. No E3w-derived column. No W next to Linux."""
    e4 = {
        cfg: cfg_median(t48_rec, cfg, "e0_to_e4_ns") for _sys, cfg in T48_ARM_ORDER
    }
    fast_e4 = e4[FAST]
    trim_e4 = e4["trimmed"]
    stock_e4 = e4["stock"]
    instr_e4 = e4["trimmed-instrumented"]
    t48_e2 = e2e3g_median(t48_rec, t48_phases, FAST)
    t46_e2 = e2e3g_median(t46_rec, t46_phases, FAST)
    conn = {
        cfg: cfg_median(t48_rec, cfg, "e0_to_first_connect_ns")
        for _sys, cfg in T48_ARM_ORDER
    }
    conn_span = max(conn.values()) - min(conn.values())
    linux_w_trim = cfg_median(t48_rec, "trimmed", "w_ns")
    linux_w_trim_iqr = cfg_iqr(t48_rec, "trimmed", "w_ns")
    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        "T4.8 five-arm campaign. **RISC-V under QEMU TCG software "
        "emulation** (not x86, not KVM hardware virtualization). "
        f"Source: `git show {T48_REV}:results/{{runs,phases}}.csv` "
        f"(batches `{sorted(T48_BATCHES)[0]}` / "
        f"`{sorted(T48_BATCHES)[1]}`, measured kernel "
        f"`{T48_SHA_PREFIX}`, n={T48_N_PER_ARM} recorded per arm, "
        "warmup excluded). Working-tree CSVs are not read. "
        "Regeneration: `just report-exhibits`.",
        "",
        "E0→E4 is the comparison: two direct client-clock stamps. "
        "No E3w-derived column (D-0070 / D-0071). W is not in this "
        "table — it is the accepted connection waiting for the guest, "
        "and a cell next to Linux would be boot-wait in disguise. "
        "Whimbrel W lives in [edges.md](edges.md) (T4.8 section). "
        "E0→first-connect is a same-QEMU **control**, not a "
        "comparison. D_fin is the same pcap definition on every row "
        "(client FIN − HTTP frame). Linux guest decomposition is "
        "[linux-decomposition.md](linux-decomposition.md) (T4.8 "
        "instrumented serial plus D-0072 labels on the same Image; "
        "not a sixth arm).",
        "",
        "### Comparison (E0→E4)",
        "",
        "| system | config | n | E0→E4 median | IQR | min | D_fin median |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for sys, cfg in T48_ARM_ORDER:
        rows = [r for r in t48_rec if r["config"] == cfg]
        e4s = [float(r["e0_to_e4_ns"]) for r in rows]
        dfins = [float(r["d_fin_ns"]) for r in rows]
        med, iq, mn = stat(e4s)
        dmed = statistics.median(dfins)
        lines.append(
            f"| {sys} | {cfg} | {len(rows)} | {fmt_ns(med)} | "
            f"{fmt_ns(iq)} | {fmt_ns(mn)} | {fmt_ns(dmed)} |"
        )
    lines.extend(
        [
            "",
            "Ratios below are E0→E4 medians on **RISC-V under QEMU TCG "
            "software emulation**, same host, same QEMU, both arms. "
            "Published unikernel figures (2–3 ms) and Firecracker's "
            "~125 ms Linux boot are x86 with KVM hardware "
            "virtualization, where absolute times run roughly 5–10× "
            "lower. Those absolute numbers are not comparable to the "
            "medians in this table; the ratio is, because the "
            "emulation penalty applies to both arms.",
            "",
            f"- `release-fast-boot` / `trimmed` = "
            f"**{fmt_ratio(trim_e4, fast_e4)}**",
            f"- `release-fast-boot` / `stock` = "
            f"**{fmt_ratio(stock_e4, fast_e4)}**",
            "",
            "This is what a single-purpose VM's structure buys under "
            "those conditions, not a \"fastest\" claim. Whimbrel's "
            f"guest work is E2→E3g "
            f"{fmt_ns(t48_e2)} in this campaign "
            "([phase-decomposition.md](phase-decomposition.md) is "
            "the after-ladder breakdown of that interval).",
            "",
            "### Control (E0→first-connect)",
            "",
            "Listener-up during QEMU netdev init. Guest-independent. "
            "A miss fails the run; it is not \"Linux connects slower.\"",
            "",
            "| system | config | n | median | IQR | min |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for sys, cfg in T48_ARM_ORDER:
        rows = [r for r in t48_rec if r["config"] == cfg]
        vals = [float(r["e0_to_first_connect_ns"]) for r in rows]
        med, iq, mn = stat(vals)
        lines.append(
            f"| {sys} | {cfg} | {len(rows)} | {fmt_ns(med)} | "
            f"{fmt_ns(iq)} | {fmt_ns(mn)} |"
        )
    lines.extend(
        [
            "",
            f"Span of medians: {fmt_ns(conn_span)} (bound 1 ms).",
            "",
            "### Trim and observer cost (Linux, same campaign)",
            "",
            "| comparison | Δ E0→E4 (median − median) | what it is |",
            "|---|---:|---|",
            f"| `stock` − `trimmed` | {fmt_ns(stock_e4 - trim_e4)} | "
            "trim removed real work; tripwire did not fire |",
            f"| `trimmed-instrumented` − `trimmed` | "
            f"{fmt_ns(instr_e4 - trim_e4)} | "
            "`loglevel=7 printk.time=1 initcall_debug` on the same "
            "`Image-trimmed` binary |",
            "",
            "The published Linux row is `trimmed`. Config: "
            "`bench/linux/linux-trimmed.fragment` merged onto "
            "`qemu_riscv64_virt_defconfig` (Buildroot 2026.02.3, "
            "kernel 6.18.7, `bench/linux/PIN`). Same Image hash on "
            "`trimmed` and `trimmed-instrumented` "
            "(MANIFEST `Image-trimmed`).",
            "",
            "### Confound A evidence (Linux W, not a comparison)",
            "",
            f"`trimmed` W median {fmt_ns(linux_w_trim)}, IQR "
            f"{fmt_ns(linux_w_trim_iqr)}. An IQR of a few "
            "milliseconds at ~700 ms is incompatible with SYN "
            "arrival snapped to slirp's ≥1 s RTO grid. The campaign "
            "published: SYN-grid and RST gates fail-closed per Linux "
            "trial, including warmup.",
            "",
            "### S is per system, never pooled across systems",
            "",
            "S (pre-ARP QEMU-startup slice) is a batch-header "
            "diagnostic, not a `runs.csv` column and not a report "
            "number (D-0071). It is per host and per guest-image "
            "size: Whimbrel safe and fast may be pooled with each "
            "other (profile-independent on one ELF); they must not "
            "be pooled with Linux (Image load lands in S, D-0062). "
            "A five-arm pooled S, and a wide IQR on that pool, is "
            "two populations — not noise. Whimbrel's S in this "
            "campaign's header stays at the ~6.8 ms constant of "
            "[d0070-pcap.md](d0070-pcap.md) (S := −residual).",
            "",
            "### E2→E3g held across campaign shape",
            "",
            f"T4.8 `release-fast-boot` E2→E3g median {fmt_ns(t48_e2)}. "
            f"T4.6 after-ladder (dump-placement / edges pin "
            f"`{AFTER_REV[:12]}`) {fmt_ns(t46_e2)} "
            f"(Δ {fmt_delta(t48_e2 - t46_e2)}). Three extra Linux "
            "arms were interleaved in T4.8. This is reproducibility "
            "across a different campaign shape, not a new rung.",
            "",
        ]
    )
    return "\n".join(lines)


def write_cross_system_t48b(
    t48b_rec: list[dict],
    t48b_phases: list[dict],
    t48_rec: list[dict],
    t48_phases: list[dict],
) -> str:
    """T4.8b comparison + the D-0073 before/after. Same shape rules as
    T4.8: no E3w-derived column, no W next to Linux."""
    e4b = {
        cfg: cfg_median(t48b_rec, cfg, "e0_to_e4_ns")
        for _sys, cfg in T48_ARM_ORDER
    }
    e4a = {
        cfg: cfg_median(t48_rec, cfg, "e0_to_e4_ns")
        for _sys, cfg in T48_ARM_ORDER
    }
    fast_e4 = e4b[FAST]
    trim_e4 = e4b["trimmed"]
    stock_e4 = e4b["stock"]
    instr_e4 = e4b["trimmed-instrumented"]
    t48b_e2 = e2e3g_median(t48b_rec, t48b_phases, FAST)
    t48_e2 = e2e3g_median(t48_rec, t48_phases, FAST)
    conn = {
        cfg: cfg_median(t48b_rec, cfg, "e0_to_first_connect_ns")
        for _sys, cfg in T48_ARM_ORDER
    }
    conn_span = max(conn.values()) - min(conn.values())
    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        "T4.8b five-arm campaign (D-0073 after: FTRACE-swept "
        "`Image-trimmed`, D-0075 `/init`). **RISC-V under QEMU TCG "
        "software emulation.** Source: `git show "
        f"{T48B_REV}:results/{{runs,phases}}.csv` (batches "
        f"`{sorted(T48B_BATCHES)[0]}` / `{sorted(T48B_BATCHES)[1]}`, "
        f"measured kernel `{T48B_SHA_PREFIX}`, n={T48B_N_PER_ARM} "
        "recorded per arm, warmup excluded). The T4.8 pin "
        f"(`{T48_REV[:12]}`) stays the before. Working-tree CSVs are "
        "not read. Regeneration: `just report-exhibits`.",
        "",
        "Both Linux arms run the D-0075 `/init`: one RTM_SETNEIGHTBL "
        "round trip before the announce, a measured **2.87 ms** on "
        "the Linux side of every row (`T_NEIGH` stamp; a bias toward "
        "Whimbrel, identical on stock and trimmed, so the trim delta "
        "is unaffected). Threats item 20.",
        "",
        "### Comparison (E0→E4)",
        "",
        "| system | config | n | E0→E4 median | IQR | min | D_fin median |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for sys, cfg in T48_ARM_ORDER:
        rows = [r for r in t48b_rec if r["config"] == cfg]
        e4s = [float(r["e0_to_e4_ns"]) for r in rows]
        dfins = [float(r["d_fin_ns"]) for r in rows]
        med, iq, mn = stat(e4s)
        dmed = statistics.median(dfins)
        lines.append(
            f"| {sys} | {cfg} | {len(rows)} | {fmt_ns(med)} | "
            f"{fmt_ns(iq)} | {fmt_ns(mn)} | {fmt_ns(dmed)} |"
        )
    lines.extend(
        [
            "",
            "Ratios are E0→E4 medians under TCG; the emulation penalty "
            "applies to both arms (see the T4.8 exhibit for the "
            "KVM-comparability caveat, unchanged):",
            "",
            f"- `release-fast-boot` / `trimmed` = "
            f"**{fmt_ratio(trim_e4, fast_e4)}**",
            f"- `release-fast-boot` / `stock` = "
            f"**{fmt_ratio(stock_e4, fast_e4)}**",
            "",
            "### Before/after (T4.8 → T4.8b, E0→E4 medians)",
            "",
            "| config | T4.8 | T4.8b | Δ | why |",
            "|---|---:|---:|---:|---|",
        ]
    )
    why = {
        FAST: "same kernel; zero in-window serial (control)",
        SAFE: "same kernel; **day's serial-byte cost, not a "
        "regression** (D-0078 / threats 21)",
        "trimmed": "D-0073 FTRACE sweep (new Image) + D-0075 `/init`",
        "trimmed-instrumented": "same new Image, instrumented cmdline",
        "stock": "same Image both campaigns (parity control)",
    }
    for _sys, cfg in T48_ARM_ORDER:
        lines.append(
            f"| {cfg} | {fmt_ns(e4a[cfg])} | {fmt_ns(e4b[cfg])} | "
            f"{fmt_delta(e4b[cfg] - e4a[cfg])} | {why[cfg]} |"
        )
    lines.extend(
        [
            "",
            "The `stock` row is the cross-campaign parity control: the "
            "same Image, ~900 ms of TCG + virtio + slirp, moved "
            f"{fmt_delta(e4b['stock'] - e4a['stock'])} across host "
            "boots. General host drift is excluded by that cell; the "
            "`release-default` movement is the serial-byte variable "
            "(D-0078) and is quoted per campaign, never across.",
            "",
            "### D-0073 projection, settled",
            "",
            "Pre-registered orientation range for T4.8b trimmed E0→E4 "
            "was **540–740 ms** (point prediction refused per D-0069); "
            f"measured **{fmt_ns(trim_e4)}** — below the low end. The "
            "sweep removed more quiet-row work than the UART-inflated "
            "diagnostic could bound; the direction expected by D-0069 "
            "(projections flatter the estimate) was the direction "
            "observed, but the magnitude was larger than the range "
            "allowed for. Falsifiers: none fired "
            f"(trimmed {fmt_ns(trim_e4)} < T4.8 trimmed "
            f"{fmt_ns(e4a['trimmed'])}; trimmed < stock; both Image "
            "hashes as pinned; gates as T4.8).",
            "",
            "### Control (E0→first-connect)",
            "",
            "| system | config | n | median | IQR | min |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for sys, cfg in T48_ARM_ORDER:
        rows = [r for r in t48b_rec if r["config"] == cfg]
        vals = [float(r["e0_to_first_connect_ns"]) for r in rows]
        med, iq, mn = stat(vals)
        lines.append(
            f"| {sys} | {cfg} | {len(rows)} | {fmt_ns(med)} | "
            f"{fmt_ns(iq)} | {fmt_ns(mn)} |"
        )
    lines.extend(
        [
            "",
            f"Span of medians: {fmt_ns(conn_span)} (bound 1 ms).",
            "",
            "### Trim and observer cost (Linux, same campaign)",
            "",
            "| comparison | Δ E0→E4 (median − median) | what it is |",
            "|---|---:|---|",
            f"| `stock` − `trimmed` | {fmt_ns(stock_e4 - trim_e4)} | "
            "the D-0073 sweep, measured on the quiet row |",
            f"| `trimmed-instrumented` − `trimmed` | "
            f"{fmt_ns(instr_e4 - trim_e4)} | observer cost; contains "
            "~11 KB of in-window console output, so this cell is "
            "**day-scoped** (D-0078) and is not comparable to T4.8's |",
            "",
            "### Passive loss signature (D-0075, first campaign use)",
            "",
            "`guest_ftx_ns` / `guest_arp_req_n` recorded on all 330 "
            "trials; zero gate failures. `bench.py arp-signature`: one "
            "arm-relative outlier (stock, first TX +40.2 ms vs arm "
            "median, one ARP request, SYN-grid 22 µs, no cliff) — a "
            "slow boot in the tail, counted and published, not a loss "
            "event. `guest_arp_req_n` reads: Linux arms 1, Whimbrel "
            "arms structurally 2 (solicit + gratuitous ARP, D-0046).",
            "",
            "### E2→E3g held across campaign shape",
            "",
            f"T4.8b `release-fast-boot` E2→E3g median "
            f"{fmt_ns(t48b_e2)}; T4.8's on the same kernel "
            f"{fmt_ns(t48_e2)} (Δ {fmt_delta(t48b_e2 - t48_e2)}). "
            "The whimbrel arms carry no D-0073/D-0075 change, and "
            "fast-boot's window has no serial exposure (D-0078), so "
            "this is the cross-campaign hold that entitles the "
            "headline to span both tables.",
            "",
        ]
    )
    return "\n".join(lines)


def load_pin(
    rev: str, batches: frozenset[str], sha_prefix: str, label: str
) -> tuple[list[dict], list[dict]]:
    runs = read_csv_text(git_show(rev, "results/runs.csv"), f"{rev}:results/runs.csv")
    phases = read_csv_text(
        git_show(rev, "results/phases.csv"), f"{rev}:results/phases.csv"
    )
    validate(runs, phases, batches, sha_prefix, label)
    return recorded(runs), phases


def write_dump_placement() -> str:
    """T4.6 vs two D-0068 invocations. Not a ladder rung."""
    t46_rec, t46_ph = load_pin(
        AFTER_REV, AFTER_BATCHES, AFTER_SHA_PREFIX, "T4.6"
    )
    r1_rec, r1_ph = load_pin(
        D68_RUN1_REV, D68_RUN1_BATCHES, D68_RUN1_SHA_PREFIX, "D-0068 run 1"
    )
    r2_rec, r2_ph = load_pin(
        D68_RUN2_REV, D68_RUN2_BATCHES, D68_RUN2_SHA_PREFIX, "D-0068 run 2"
    )
    pins = (
        ("T4.6", t46_rec, t46_ph),
        ("run 1", r1_rec, r1_ph),
        ("run 2", r2_rec, r2_ph),
    )
    metrics = ("E2→E3g", "E0→E4", "E3w→E4", "E0→E3w")
    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        "D-0068 dump placement: T4.6 (dump immediately after `wait_tx`) "
        "versus two independent yield-then-dump invocations. Warmup "
        "excluded, n=60 recorded per config per pin. E3w→E4 is "
        "`e0_to_e4_ns − e0_to_e3w_ns` per trial, then median.",
        "",
        f"T4.6: `git show {AFTER_REV[:12]}:results/{{runs,phases}}.csv` "
        f"(batches `{sorted(AFTER_BATCHES)[0]}` / `{sorted(AFTER_BATCHES)[1]}`, "
        f"measured kernel `{AFTER_SHA_PREFIX}`).",
        f"Run 1: `git show {D68_RUN1_REV[:12]}` "
        f"(batches `{sorted(D68_RUN1_BATCHES)[0]}` / "
        f"`{sorted(D68_RUN1_BATCHES)[1]}`, measured kernel "
        f"`{D68_RUN1_SHA_PREFIX}`).",
        f"Run 2: `git show {D68_RUN2_REV[:12]}` "
        f"(batches `{sorted(D68_RUN2_BATCHES)[0]}` / "
        f"`{sorted(D68_RUN2_BATCHES)[1]}`, measured kernel "
        f"`{D68_RUN2_SHA_PREFIX}`).",
        "",
        "| config | metric | T4.6 median | D-0068 run 1 | D-0068 run 2 | run 2 − T4.6 | rel(run 1, run 2) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    rels: list[float] = []
    for cfg in (FAST, SAFE):
        stats = {name: {} for name, _, _ in pins}
        for name, rec, phases in pins:
            ev = edge_vals(rec, phases, cfg)
            for metric in metrics:
                stats[name][metric] = stat(ev[metric])[0]
        for metric in metrics:
            t46 = stats["T4.6"][metric]
            r1 = stats["run 1"][metric]
            r2 = stats["run 2"][metric]
            mean12 = (r1 + r2) / 2.0
            rel = abs(r2 - r1) / mean12 if mean12 else 0.0
            rels.append(rel)
            lines.append(
                "| "
                + " | ".join(
                    [
                        cfg,
                        metric,
                        fmt_ns(t46),
                        fmt_ns(r1),
                        fmt_ns(r2),
                        fmt_delta(r2 - t46),
                        f"{100.0 * rel:.3f}%",
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            f"Largest relative disagreement between the two D-0068 "
            f"invocations in this table is **{100.0 * max(rels):.3f}%**. "
            "Within-run stability is max(2%, 200 µs) on metrics ≥ 1 ms "
            "(D-0055).",
            "",
        ]
    )
    return "\n".join(lines)


PRINTK_RE = re.compile(r"^\[\s*(\d+)\.(\d+)\]\s(.*)$")
INIT_STAMP_RE = re.compile(r"^INIT (\w+) mono_ns=(\d+)\s*$")
INITCALL_RET_RE = re.compile(
    r"\binitcall\s+(?:0x)?([0-9a-fA-F]+)\s+returned\s+(-?\d+)\s+after\s+(\d+)\s+usecs"
)
PHASE_RE = re.compile(
    r"^PHASE (\S+) ticks=(\d+) ns=(\d+) since_start=(\d+) ns=(\d+) "
    r"delta=(\d+) ns=(\d+)"
)
INIT_STAMP_ORDER = (
    "listen",
    "ifup",
    "announce",
    "ready",
    "accept",
    "read",
    "response",
)


def printk_ns(sec: str, frac: str) -> int:
    return int(sec) * 1_000_000_000 + int(frac.ljust(9, "0")[:9])


def fmt_ms2(ns: float) -> str:
    return f"{ns / 1e6:.2f} ms"


def fmt_us_ms1(usecs: int) -> str:
    return f"{usecs / 1000.0:.1f} ms"


LABEL_ROW_RE = re.compile(
    r"^\| (\d+) \| (\d+) \| `([^`]+)` \| (-?\d+) \| ([0-9.]+) \|\s*$"
)


def manifest_image_trimmed_sha(text: str) -> str:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "artifact" and parts[1] == "Image-trimmed":
            return parts[2]
    raise ExhibitFail("TEST FAIL: MANIFEST missing artifact Image-trimmed")


def parse_initcall_label_file(
    text: str,
) -> tuple[list[dict], list[dict], dict[str, str]]:
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("# Image-trimmed sha256="):
            meta["image_sha"] = line.split("=", 1)[1].strip()
        elif line.startswith("# cmdline="):
            meta["cmdline"] = line.split("=", 1)[1]
        elif line.startswith("# qemu="):
            meta["qemu"] = line.split("=", 1)[1]
        elif line.startswith("# System.map sha256="):
            meta["smap_sha"] = line.split("=", 1)[1].split()[0]
    hole: list[dict] = []
    all_rows: list[dict] = []
    section: str | None = None
    for line in text.splitlines():
        if line.startswith("## Hole window"):
            section = "hole"
            continue
        if line.startswith("## All initcalls"):
            section = "all"
            continue
        m = LABEL_ROW_RE.match(line)
        if not m or section is None:
            continue
        rec = {
            "rank": int(m.group(1)),
            "usecs": int(m.group(2)),
            "symbol": m.group(3),
            "ret": int(m.group(4)),
            "printk_s": float(m.group(5)),
        }
        if section == "hole":
            hole.append(rec)
        else:
            all_rows.append(rec)
    if "image_sha" not in meta or "cmdline" not in meta or "qemu" not in meta:
        raise ExhibitFail("TEST FAIL: D-0072 label header missing sha/cmdline/qemu")
    if not hole:
        raise ExhibitFail("TEST FAIL: D-0072 label file has no hole-window rows")
    if not all_rows:
        raise ExhibitFail("TEST FAIL: D-0072 label file has no all-initcall rows")
    return hole, all_rows, meta


def lookup_initcall(rows: list[dict], symbol: str) -> dict:
    hits = [r for r in rows if r["symbol"] == symbol]
    if len(hits) != 1:
        raise ExhibitFail(
            f"TEST FAIL: want one {symbol} in initcall labels, got {len(hits)}"
        )
    return hits[0]


def parse_printk(text: str) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    for raw in text.splitlines():
        m = PRINTK_RE.match(raw.rstrip("\r"))
        if not m:
            continue
        ts = printk_ns(m.group(1), m.group(2))
        raw_ts = f"{m.group(1)}.{m.group(2)}"
        rows.append((ts, raw_ts, m.group(3)))
    return rows


def parse_init_stamps(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for raw in text.splitlines():
        m = INIT_STAMP_RE.match(raw.rstrip("\r"))
        if m:
            found[m.group(1)] = int(m.group(2))
    missing = [n for n in INIT_STAMP_ORDER if n not in found]
    if missing:
        raise ExhibitFail(
            f"TEST FAIL: Linux serial missing INIT stamps {missing}"
        )
    return found


def parse_phases_serial(text: str) -> list[dict]:
    rows: list[dict] = []
    for raw in text.splitlines():
        m = PHASE_RE.match(raw.rstrip("\r"))
        if not m:
            continue
        rows.append(
            {
                "name": m.group(1),
                "mtime_ns": int(m.group(3)),
                "since_start_ns": int(m.group(5)),
                "delta_ns": int(m.group(7)),
            }
        )
    if not rows:
        raise ExhibitFail("TEST FAIL: Whimbrel serial has no PHASE rows")
    return rows


def write_linux_decomposition(
    linux_text: str,
    whim_text: str,
    labels_text: str,
    fragment_text: str,
    manifest_text: str,
) -> str:
    """T4.8 instrumented serial vs Whimbrel dump, plus D-0072 labels.

    Kind, not magnitude. Diagnostic microseconds label the 327 ms
    cell; they do not replace it.
    """
    hole, all_initcalls, label_meta = parse_initcall_label_file(labels_text)
    want_sha = manifest_image_trimmed_sha(manifest_text)
    if label_meta["image_sha"] != want_sha:
        raise ExhibitFail(
            "TEST FAIL: D-0072 label Image-trimmed sha256 "
            f"{label_meta['image_sha']} != MANIFEST {want_sha}"
        )
    if "ignore_loglevel" not in label_meta["cmdline"]:
        raise ExhibitFail(
            "TEST FAIL: D-0072 label cmdline missing ignore_loglevel: "
            f"{label_meta['cmdline']!r}"
        )
    if "initcall_debug" not in label_meta["cmdline"]:
        raise ExhibitFail(
            "TEST FAIL: D-0072 label cmdline missing initcall_debug"
        )
    if "10.2.1" not in label_meta["qemu"]:
        raise ExhibitFail(
            "TEST FAIL: D-0072 label qemu is not 10.2.1: "
            f"{label_meta['qemu']!r}"
        )
    if hole[0]["rank"] != 1 or hole[0]["symbol"] != "trace_eval_sync":
        raise ExhibitFail(
            "TEST FAIL: hole rank 1 is not trace_eval_sync "
            f"(rank {hole[0]['rank']} {hole[0]['symbol']})"
        )
    if len(hole) < 29:
        raise ExhibitFail(
            f"TEST FAIL: hole has {len(hole)} initcalls, want ≥ 29"
        )
    ranks_2_29 = hole[1:29]
    ranks_2_29_us = sum(r["usecs"] for r in ranks_2_29)
    if ranks_2_29_us >= 20_000:
        raise ExhibitFail(
            "TEST FAIL: hole ranks 2–29 combined "
            f"{ranks_2_29_us} usecs, want < 20 ms"
        )
    of_serial = lookup_initcall(all_initcalls, "of_platform_serial_driver_init")
    virtio_net = lookup_initcall(all_initcalls, "virtio_net_driver_init")
    serial8250 = lookup_initcall(all_initcalls, "serial8250_init")
    virtio_mmio = lookup_initcall(all_initcalls, "virtio_mmio_init")
    hole_syms = {r["symbol"] for r in hole}
    if of_serial["symbol"] in hole_syms:
        raise ExhibitFail(
            "TEST FAIL: of_platform_serial_driver_init is inside the "
            "327 ms hole; the inversion claim would be wrong"
        )
    if virtio_net["symbol"] in hole_syms or virtio_mmio["symbol"] in hole_syms:
        raise ExhibitFail(
            "TEST FAIL: a virtio initcall is inside the 327 ms hole"
        )
    virtio_us = virtio_net["usecs"] + virtio_mmio["usecs"]
    if virtio_us >= 15_000:
        raise ExhibitFail(
            f"TEST FAIL: virtio pair {virtio_us} usecs, want ~5 ms"
        )
    if of_serial["usecs"] < 10 * serial8250["usecs"]:
        raise ExhibitFail(
            "TEST FAIL: of_platform_serial is not ≫ serial8250_init "
            f"({of_serial['usecs']} vs {serial8250['usecs']})"
        )
    # LABEL_REV fragment is the T4.8 Image. It must not mention FTRACE:
    # this exhibit records the miss. HEAD may unset FTRACE (D-0073);
    # this function reads git_show(LABEL_REV), not HEAD.
    if "CONFIG_FTRACE" in fragment_text:
        raise ExhibitFail(
            "TEST FAIL: linux-trimmed.fragment mentions FTRACE; "
            "the missed-trim claim needs a rewrite"
        )
    if "CONFIG_SERIAL_OF_PLATFORM=y" not in fragment_text:
        raise ExhibitFail(
            "TEST FAIL: fragment missing CONFIG_SERIAL_OF_PLATFORM=y keep"
        )

    printk = parse_printk(linux_text)
    if len(printk) < 100:
        raise ExhibitFail(
            f"TEST FAIL: Linux serial has {len(printk)} timestamped "
            "printk lines, want ≥ 100"
        )
    initcalls = [
        1
        for _ts, _raw, msg in printk
        if INITCALL_RET_RE.search(msg)
    ]
    if initcalls:
        raise ExhibitFail(
            "TEST FAIL: T4.8 instrumented serial has initcall-returned "
            "lines; that pin is loglevel=7 (D-0072)"
        )
    cmdline = next((msg for _ts, _raw, msg in printk if "Kernel command line:" in msg), "")
    if "initcall_debug" not in cmdline or "loglevel=7" not in cmdline:
        raise ExhibitFail(
            "TEST FAIL: Linux serial cmdline missing initcall_debug "
            f"or loglevel=7: {cmdline!r}"
        )
    if not any("PM: Calling 0x" in msg for _ts, _raw, msg in printk):
        raise ExhibitFail(
            "TEST FAIL: Linux serial missing PM: Calling 0x… "
            "(%pS-without-kallsyms witness)"
        )
    run_init = next(
        ((ts, raw) for ts, raw, msg in printk if "Run /init as init process" in msg),
        None,
    )
    if run_init is None:
        raise ExhibitFail("TEST FAIL: Linux serial missing Run /init")
    run_init_ns, _run_init_raw = run_init
    shutdown = next(
        (
            (ts, raw)
            for ts, raw, msg in printk
            if msg.endswith("shutdown") and "riscv-pmu-sbi" in msg
        ),
        None,
    )
    if shutdown is None:
        raise ExhibitFail("TEST FAIL: Linux serial missing pmu shutdown")
    shutdown_ns, _ = shutdown
    if "LINUX INIT OK" not in linux_text:
        raise ExhibitFail("TEST FAIL: Linux serial missing LINUX INIT OK")
    stamps = parse_init_stamps(linux_text)
    first_ts = printk[0][0]
    n_zero = sum(1 for ts, _raw, _msg in printk if ts == 0)
    sched = next(
        ((ts, raw, msg) for ts, raw, msg in printk if msg.startswith("sched_clock:")),
        None,
    )
    if sched is None:
        raise ExhibitFail("TEST FAIL: Linux serial missing sched_clock")
    sched_ns, _sched_raw, _sched_msg = sched
    prefix = linux_text.split("[    0.000000]", 1)[0]
    opensbi_lines = len(prefix.strip().splitlines())
    if "OpenSBI" not in prefix:
        raise ExhibitFail("TEST FAIL: Linux serial missing OpenSBI prefix")

    kernel_span = run_init_ns - first_ts
    if kernel_span <= 0:
        raise ExhibitFail("TEST FAIL: Run /init is not after the first printk")
    gaps: list[tuple[int, str, str, str, str]] = []
    kernel_printk = [(ts, raw, msg) for ts, raw, msg in printk if ts <= run_init_ns]
    for i in range(1, len(kernel_printk)):
        d = kernel_printk[i][0] - kernel_printk[i - 1][0]
        gaps.append(
            (
                d,
                kernel_printk[i - 1][2],
                kernel_printk[i][2],
                kernel_printk[i - 1][1],
                kernel_printk[i][1],
            )
        )
    gaps.sort(key=lambda g: g[0], reverse=True)
    top = gaps[:10]
    if len(top) < 10:
        raise ExhibitFail("TEST FAIL: fewer than 10 printk gaps before Run /init")
    top_sum = sum(g[0] for g in top)
    hole_from = "Key type dns_resolver registered"
    hole_to = "clk: Disabling unused clocks"
    if hole_from not in top[0][1] or hole_to not in top[0][2]:
        raise ExhibitFail(
            "TEST FAIL: largest printk gap is not dns_resolver → clk-disable "
            f"({top[0][1]!r} → {top[0][2]!r})"
        )
    serial8250_msg = next(
        (msg for _ts, _raw, msg in printk if msg.startswith("Serial: 8250")),
        None,
    )
    if serial8250_msg is None:
        raise ExhibitFail("TEST FAIL: T4.8 serial missing Serial: 8250 banner")
    tty_mmio_msg = next(
        (
            msg
            for _ts, _raw, msg in printk
            if "10000000.serial: ttyS0" in msg
        ),
        None,
    )
    if tty_mmio_msg is None:
        raise ExhibitFail(
            "TEST FAIL: T4.8 serial missing 10000000.serial: ttyS0"
        )
    gap1_ns = top[0][0]
    hole_rank1_share = 100.0 * hole[0]["usecs"] * 1000.0 / gap1_ns
    ranks_2_29_share = 100.0 * ranks_2_29_us * 1000.0 / gap1_ns
    serial_ratio = of_serial["usecs"] / serial8250["usecs"]

    listen = stamps["listen"]
    resp = stamps["response"]
    exec_to_listen = listen - run_init_ns
    listen_to_resp = resp - listen
    resp_to_shut = shutdown_ns - resp
    run_to_shut = shutdown_ns - run_init_ns
    # Rounding witness: the three pieces at 0.01 ms must sum to the span.
    pieces = (
        round(exec_to_listen / 1e4) / 100.0,
        round(listen_to_resp / 1e4) / 100.0,
        round(resp_to_shut / 1e4) / 100.0,
    )
    span_ms = round(run_to_shut / 1e4) / 100.0
    if abs(sum(pieces) - span_ms) > 0.011:
        raise ExhibitFail(
            f"TEST FAIL: clock cross-check {span_ms} != {pieces[0]}+"
            f"{pieces[1]}+{pieces[2]}"
        )

    phases = parse_phases_serial(whim_text)
    start = next((p for p in phases if p["name"] == "_start"), None)
    e3g = next((p for p in phases if p["name"] == "E3g"), None)
    if start is None or e3g is None:
        raise ExhibitFail("TEST FAIL: Whimbrel serial missing _start or E3g")
    if "OpenSBI" not in whim_text.split("HTTP READY", 1)[0]:
        raise ExhibitFail("TEST FAIL: Whimbrel serial missing OpenSBI prefix")
    if "HTTP READY" not in whim_text:
        raise ExhibitFail("TEST FAIL: Whimbrel serial missing HTTP READY")

    def clip(msg: str, n: int = 72) -> str:
        msg = md_cell(msg.strip())
        return msg if len(msg) <= n else msg[: n - 1] + "…"

    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        f"The virtio path Linux actually needs — `virtio_net_driver_init` "
        f"plus `virtio_mmio_init` — costs **{fmt_us_ms1(virtio_us)}** "
        "(UART-inflated; `ignore_loglevel` boot). The "
        f"**{fmt_ms2(gap1_ns)}** T4.8 hole is not that path: "
        f"`trace_eval_sync` is {hole_rank1_share:.1f}% of it, and "
        "full-file the other giant is generic serial-bus probing "
        f"(`of_platform_serial_driver_init` "
        f"{fmt_us_ms1(of_serial['usecs'])}, UART-inflated). Kind, "
        "not magnitude: named subsystems a single-purpose kernel "
        "never runs, not \"Linux is slower.\"",
        "",
        f"T4.8 source: `git show {SERIAL_REV}:results/serial/{{linux-trimmed-instrumented,whimbrel-fast}}-20260818T073023Z-1-t04.log` "
        f"(batch `20260818T073023Z-1`, trial 4, measured kernel "
        "`1005399`, same QEMU). Labels: "
        f"`git show {LABEL_REV}:{LABEL_PATH}` "
        "(same `Image-trimmed`, `ignore_loglevel`). "
        "**RISC-V under QEMU TCG software emulation.** Working-tree "
        "serials are not read. Regeneration: `just report-exhibits`. "
        "This is the T4.8 / pre-FTRACE Image. D-0073 / T4.8b is "
        "the after; those pins are not these.",
        "",
        "This exhibit is not a cross-system table and not a sixth "
        "arm. Diagnostic durations **label** the "
        f"{fmt_ms2(gap1_ns)} cell; they do not replace it.",
        "",
        "## Instrumentation limit",
        "",
        f"The cmdline is in the log (`{md_cell(cmdline[len('Kernel command line: '):] if cmdline.startswith('Kernel command line: ') else cmdline)}`). "
        f"{len(printk)} timestamped printk lines, **zero** "
        "`initcall … returned … after … usecs` lines.",
        "",
        "Two factors, this order (D-0072):",
        "",
        "1. **`loglevel=7` filters `KERN_DEBUG`.** Necessary and "
        "sufficient for the missing lines. Linux 6.18 prints "
        "initcall_debug at `KERN_DEBUG`; console emits levels "
        "strictly below `console_loglevel`; debug is 7.",
        "2. **`# CONFIG_KALLSYMS is not set`** "
        "(`linux-trimmed.fragment` line 71) affects **names only**. "
        "`%pS` still prints the pointer. Witness in this log: "
        "`PM: Calling 0xffffffff800614ec`.",
        "",
        "A kernel trimmed this hard cannot be fully instrumented by "
        "its own debug facility. That is a general observation about "
        "minimal-kernel measurement, not a mistake in the T4.8 setup. "
        "A sixth arm with kallsyms would describe a different binary "
        "than the trimmed row, and without `ignore_loglevel` would "
        "still print nothing.",
        "",
        "## Printk-visible kernel",
        "",
        f"Earliest timestamped line: `[{printk[0][1]}] {clip(printk[0][2], 80)}`. "
        f"{n_zero} lines sit at `0.000000`. First moving clock: "
        f"`[{sched[1]}] {clip(sched[2], 80)}` ({fmt_ns(sched_ns)} after "
        "the printk epoch). Printk epoch is kernel timekeeping, not "
        "firmware handoff.",
        "",
        "Milestones that *are* visible: dummy console; `PF_INET` / "
        "`TCP: Hash tables configured` (inet_init, not a listening "
        "socket); PLIC; 8250; `legacy console [ttyS0] enabled`; "
        f"`Run /init as init process` at {fmt_ms2(run_init_ns)}. "
        "Virtio-mmio probe and virtio-net ready are **not** on the "
        "T4.8 printk boot path — `virtio_net virtio0` and "
        "`10008000.virtio_mmio` appear only at shutdown. The "
        "diagnostic boot names those initcalls outside the hole "
        f"({fmt_us_ms1(virtio_us)} UART-inflated combined).",
        "",
        f"Printk-visible kernel is the span from the first timestamp "
        f"to `Run /init`: **{fmt_ms2(kernel_span)}**. The gaps "
        "between consecutive timestamps are the decomposition we have. "
        "Top ten, as a share of that span:",
        "",
        "| rank | from | to | duration | share of printk-visible kernel | notes |",
        "|---|---|---|---:|---:|---|",
    ]
    for i, (dur, src, dst, _t0, _t1) in enumerate(top, 1):
        note = (
            f"`trace_eval_sync` ({fmt_us_ms1(hole[0]['usecs'])} "
            "UART-inflated on the ignore_loglevel boot) labels "
            f"{hole_rank1_share:.1f}% of this {fmt_ms2(gap1_ns)} "
            "cell; it does not replace it"
            if i == 1
            else ""
        )
        share = 100.0 * dur / kernel_span
        lines.append(
            f"| {i} | {clip(src, 48)} | {clip(dst, 48)} | "
            f"{fmt_ms2(dur)} | {share:.1f}% | {note} |"
        )
    lines.extend(
        [
            "",
            f"Those ten gaps are **{100.0 * top_sum / kernel_span:.1f}%** "
            f"of {fmt_ms2(kernel_span)}. Gap 1 is after ttyS0 is enabled, "
            "so it is real silence, not a buffered early console. Around "
            "it the log still names general-purpose work the trim did "
            "not remove: NFS, 9p, USB, ALSA, SDHCI, mousedev, HugeTLB, "
            "audit, RPC.",
            "",
            "## Gap 1 named",
            "",
            "The D-0072 `ignore_loglevel` boot of the same "
            "`Image-trimmed` names the hole. Durations in this "
            "section are UART-inflated (extra `KERN_DEBUG` on the "
            f"console). They label the **{fmt_ms2(gap1_ns)}** T4.8 "
            "cell; they do not replace it.",
            "",
            "| what | duration (UART-inflated; ignore_loglevel boot) | share of the T4.8 hole |",
            "|---|---:|---:|",
            f"| hole rank 1: `trace_eval_sync` | {fmt_us_ms1(hole[0]['usecs'])} | {hole_rank1_share:.1f}% of {fmt_ms2(gap1_ns)} |",
            f"| hole ranks 2–29 combined ({len(ranks_2_29)} initcalls) | {fmt_us_ms1(ranks_2_29_us)} | {ranks_2_29_share:.1f}% of {fmt_ms2(gap1_ns)} |",
            "",
            "One initcall is essentially the whole anonymous region. "
            f"Ranks 2–29 inside the hole sum to under 20 ms combined "
            f"({fmt_us_ms1(ranks_2_29_us)}, UART-inflated).",
            "",
            "`trace_eval_sync` is `late_initcall_sync` in Linux 6.18 "
            "`kernel/trace/trace.c` (`obj-$(CONFIG_TRACING) += trace.o`). "
            "`trace_eval_init` (a `subsys_initcall`) queues "
            "`eval_map_work_func` on a tracing workqueue; that work "
            "walks `__start_ftrace_eval_maps` … `__stop_ftrace_eval_maps` "
            "and rewrites trace-event print formats so userspace "
            "parsers can decode `TRACE_DEFINE_ENUM` names. "
            "`trace_eval_sync` destroys the workqueue and therefore "
            "flushes the pass. This guest is one hart under TCG, so "
            "the \"background\" work runs at sync time on the boot CPU "
            "and shows up as one giant initcall.",
            "",
            "No tracing consumer is running. `/init` is a static musl "
            "HTTP server; `PROC_FS` and `SYSFS` are unset, so tracefs "
            "is not a usable ABI. The maps exist for a userspace that "
            "is not here. `CONFIG_TRACE_EVAL_MAP_FILE` only keeps a "
            "debugfs dump of the maps; it is not the gate for this "
            "initcall.",
            "",
            "The user-visible compile gate is `menuconfig FTRACE` "
            "(\"Tracers\"), which defaults y when "
            "`CONFIG_DEBUG_KERNEL=y`. Buildroot's "
            "`qemu_riscv64_virt_defconfig` uses the riscv "
            "`defconfig` (`BR2_LINUX_KERNEL_USE_ARCH_DEFAULT_CONFIG`), "
            "and that defconfig sets `DEBUG_KERNEL=y`. "
            "The T4.8 `linux-trimmed.fragment` (this pin) never "
            "unsets `FTRACE`, `TRACING`, or `DEBUG_KERNEL`. "
            "`FTRACE` is not EXPERT-gated, so "
            "`# CONFIG_FTRACE is not set` would have stuck. That "
            "is a further trim we **missed** on this Image, not a "
            "keep we documented. D-0062 already claims *a* minimal "
            "Linux, not *the* minimal Linux. **This exhibit stays "
            "the before.** D-0073 acts on the miss with a new Image "
            "and a T4.8b five-arm campaign. Do not retarget "
            "`ffb7ac7` / `d705ecb` / `93ab617`.",
            "",
            "## Full-file context",
            "",
            "Ranked across the whole diagnostic boot, not inside the "
            "hole. Every duration in this table is UART-inflated from "
            "the `ignore_loglevel` boot; none of them replace a T4.8 "
            "cell. `of_platform_serial_driver_init` finishes before "
            "`dns_resolver registered` — it is not the "
            f"{fmt_ms2(gap1_ns)} hole.",
            "",
            "| symbol | duration (UART-inflated; ignore_loglevel boot) | in the T4.8 hole? |",
            "|---|---:|---|",
            f"| `trace_eval_sync` | {fmt_us_ms1(hole[0]['usecs'])} | yes |",
            f"| `of_platform_serial_driver_init` | {fmt_us_ms1(of_serial['usecs'])} | no |",
            f"| `virtio_net_driver_init` | {fmt_us_ms1(virtio_net['usecs'])} | no |",
            f"| `serial8250_init` | {fmt_us_ms1(serial8250['usecs'])} | no |",
            f"| `virtio_mmio_init` | {fmt_us_ms1(virtio_mmio['usecs'])} | no |",
            "",
            f"`virtio_net_driver_init` + `virtio_mmio_init` = "
            f"**{fmt_us_ms1(virtio_us)}** (UART-inflated). That is "
            "the virtio path this comparison actually needs. The "
            "dominant costs are a tracing-infrastructure sync pass "
            "and generic serial-bus probing — work a single-purpose "
            "kernel never runs.",
            "",
            f"`serial8250_init` (`CONFIG_SERIAL_8250`, kept) is the "
            "8250/16550 core: `uart_register_driver` and the "
            "ISA/legacy port table (`nr_uarts`). The T4.8 log prints "
            f"`{md_cell(serial8250_msg)}` from that initcall. It does "
            "not probe the QEMU virt DT UART.",
            "",
            "`of_platform_serial_driver_init` "
            "(`CONFIG_SERIAL_OF_PLATFORM=y`, kept on purpose in "
            "`linux-trimmed.fragment` so ttyS0 comes from the virt "
            "board DT) is `module_platform_driver` → "
            "`of_platform_serial_probe` in `drivers/tty/serial/8250/8250_of.c`: "
            "ioremap, IRQ, `serial8250_register_8250_port` for "
            "`10000000.serial`. That is the MMIO + console "
            "registration the T4.8 log shows as "
            f"`{md_cell(tty_mmio_msg)}`. The "
            f"**{serial_ratio:.1f}×** gap (UART-inflated "
            f"{fmt_us_ms1(of_serial['usecs'])} vs "
            f"{fmt_us_ms1(serial8250['usecs'])}) is core-register "
            "versus DT-probe, not two copies of the same work. Kept, "
            "not missed.",
            "",
            "## `/init` stamps",
            "",
            f"**{fmt_ms2(listen)} of kernel boot before userspace**, then "
            f"**{fmt_ms2(listen_to_resp)} of server work** "
            "(listen → response).",
            "",
            "| stamp | CLOCK_MONOTONIC | Δ from previous |",
            "|---|---:|---:|",
            f"| listen | {fmt_ms2(stamps['listen'])} | — |",
        ]
    )
    prev = stamps["listen"]
    for name in INIT_STAMP_ORDER[1:]:
        cur = stamps[name]
        lines.append(
            f"| {name} | {fmt_ms2(cur)} | {fmt_ms2(cur - prev)} |"
        )
        prev = cur
    lines.extend(
        [
            "",
            "ready → accept is sub-millisecond: the SYN was already "
            "queued (confound A's announce). Virtio-net bring-up is "
            "not in this table; the diagnostic names it outside gap 1 "
            f"(`virtio_mmio_init` + `virtio_net_driver_init` = "
            f"{fmt_us_ms1(virtio_us)}, UART-inflated).",
            "",
            "## Clock cross-check",
            "",
            "Printk `Run /init` → first shutdown line versus "
            "`CLOCK_MONOTONIC` listen / response. Evidence the two "
            "clocks **agree**, not a claim they are the same clock:",
            "",
            f"`Run /init` → shutdown = **{fmt_ms2(run_to_shut)}** = "
            f"{fmt_ms2(exec_to_listen)} (exec → listen) + "
            f"{fmt_ms2(listen_to_resp)} (listen → response) + "
            f"{fmt_ms2(resp_to_shut)} (response → shutdown).",
            "",
            f"At 0.01 ms rounding that is {span_ms:.2f} = "
            f"{pieces[0]:.2f} + {pieces[1]:.2f} + {pieces[2]:.2f}. "
            "`READY` itself has no kernel timestamp; it sits in the "
            "printk hole between `Run /init` and shutdown by design "
            "(one 6-byte write on the measured path).",
            "",
            "## Unmeasured prefix",
            "",
            f"**{opensbi_lines} untimed OpenSBI lines** before the first "
            f"`[    0.000000]`, then **{n_zero} kernel lines collapsed "
            f"at `0.000000`** until `sched_clock` at {fmt_ns(sched_ns)}. "
            "Early asm, mem setup, and the cmdline are on the epoch, "
            "duration unknown. No E2 is constructed from the OpenSBI "
            "banner, from the 0.000000 cluster, or from a mixed-clock "
            "remainder against W. The prefix is unmeasured.",
            "",
            "## Whimbrel, same batch and trial",
            "",
            "Same OpenSBI v1.8 banner, still untimed on the serial. "
            f"Then `HTTP READY` and **{len(phases)} named `PHASE` "
            "deltas** on the 10 MHz mtime, including virtio as "
            f"`virtq_init` / `DRIVER_OK`. `_start` is at "
            f"{fmt_ms2(start['mtime_ns'])} mtime — OpenSBI is priced. "
            "Linux printk 0 is later and does not include that interval.",
            "",
            f"This trial's `_start` → `E3g` is {fmt_ms2(e3g['since_start_ns'])} "
            "(the campaign median 6.43 ms lives in "
            "[phase-decomposition.md](phase-decomposition.md); this dump "
            "is one trial, used here for kind). Every interval has a "
            "name. Linux's T4.8 printk never prints a virtio probe "
            "on the way up; the diagnostic names that work outside "
            "the hole.",
            "",
            "That is the comparison: twenty named deltas against "
            "named general-purpose work a single-purpose VM does not "
            "do — a tracing eval-map sync pass and leftover probes — "
            "not an unnamed virtio tax. The T4.8 instrumented serial "
            "could not name the hole; the diagnostic boot of the same "
            "Image did.",
            "",
        ]
    )
    return "\n".join(lines)


def phase_median_delta(
    rec: list[dict], phases: list[dict], config: str, phase: str
) -> float:
    keys = {
        (r["batch_id"], r["trial"], r["config"])
        for r in rec
        if r["config"] == config
    }
    vals = [
        float(p["delta_ns"])
        for p in phases
        if int(p["warmup"]) == 0
        and p["phase"] == phase
        and p["config"] == config
        and (p["batch_id"], p["trial"], p["config"]) in keys
    ]
    if not vals:
        raise ExhibitFail(
            f"TEST FAIL: no {phase} deltas for {config}"
        )
    return statistics.median(vals)


def write_t47_firmware(rec: list[dict], phases: list[dict]) -> str:
    """T4.7 firmware-removal exhibit. Every displayed quantity is
    computed from the pin. S is per-config; ΔS is the fast pair only."""
    batches = tuple(sorted({r["batch_id"] for r in rec}))
    b1, b2 = batches
    n = sum(1 for r in rec if r["config"] == FAST)
    n_per_batch = n // len(batches)
    canary = rec[0]
    s_fast = statistics.median(s_vals(rec, FAST))
    s_safe = statistics.median(s_vals(rec, SAFE))
    s_m_fast = statistics.median(s_vals(rec, M_FAST))
    s_m_safe = statistics.median(s_vals(rec, M_SAFE))
    delta_s = s_fast - s_m_fast
    if abs(delta_s) <= DS_EXPECT_ABS_NS:
        ds_expect = (
            f"inside the corrected window (ΔS ≈ 0, "
            f"|ΔS| ≤ {fmt_ns(DS_EXPECT_ABS_NS)})"
        )
    else:
        ds_expect = (
            f"outside the corrected window "
            f"(|ΔS| ≤ {fmt_ns(DS_EXPECT_ABS_NS)}); "
            "falsifiers still decide publication"
        )
    e2_fast = e2e3g_vals(rec, phases, FAST)
    e2_m_fast = e2e3g_vals(rec, phases, M_FAST)
    claim_a = statistics.median(e2_m_fast) - statistics.median(e2_fast)
    e4 = {}
    d_e4 = {}
    d_e2_b = {}
    guest_fw = {}
    for b in batches:
        e4[(FAST, b)] = cfg_median_batch(rec, FAST, "e0_to_e4_ns", b)
        e4[(M_FAST, b)] = cfg_median_batch(rec, M_FAST, "e0_to_e4_ns", b)
        d_e4[b] = e4[(M_FAST, b)] - e4[(FAST, b)]
        d_e2_b[b] = e2e3g_median(rec, phases, M_FAST, b) - e2e3g_median(
            rec, phases, FAST, b
        )
        guest_fw[b] = d_e4[b] - d_e2_b[b] - delta_s
    seams = []
    for phase in T47_SEAM_PHASES:
        open_med = phase_median_delta(rec, phases, FAST, phase)
        shim_med = phase_median_delta(rec, phases, M_FAST, phase)
        seams.append((phase, open_med, shim_med, shim_med - open_med))
    claim_a_b1 = d_e2_b[b1]
    claim_a_b2 = d_e2_b[b2]
    e2_fast_med, e2_fast_iqr, e2_fast_min = stat(e2_fast)
    e2_m_med, e2_m_iqr, e2_m_min = stat(e2_m_fast)
    ratio_b1 = fmt_ratio(e4[(FAST, b1)], e4[(M_FAST, b1)])
    ratio_b2 = fmt_ratio(e4[(FAST, b2)], e4[(M_FAST, b2)])
    shim_s_span = abs(s_m_fast - s_m_safe)
    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        "T4.7 firmware-removal campaign (D-0079). Source: `git show "
        f"{T47_REV}:results/{{runs,phases}}.csv` (batches "
        f"`{b1}` / `{b2}`, measured kernel `{T47_SHA_PREFIX}`, "
        f"n={n} recorded per arm, warmup excluded). Working-tree CSVs "
        "are not read. Regeneration: `just report-exhibits`.",
        "",
        "Same-campaign gate: both firmware lanes from one batch set; "
        "one canary in the shared header "
        f"(canary_stvec_ns={canary['canary_stvec_ns']} "
        f"canary_page_verify_ns={canary['canary_page_verify_ns']}). "
        "A pair whose lanes come from different campaigns does not "
        "generate.",
        "",
        "### Claim A — pooled ΔE2→E3g, stability-gated",
        "",
        "Guest work. Pooled across both batches because E2→E3g was "
        "stable on both compared arms. Per-batch figures sit beside "
        "the claim; they are not a second pooling rule.",
        "",
        "| config | n | E2→E3g median | IQR | min | batch 1 Δ vs OpenSBI | batch 2 Δ vs OpenSBI |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| `{FAST}` | {len(e2_fast)} | {fmt_ns(e2_fast_med)} | "
        f"{fmt_ns(e2_fast_iqr)} | {fmt_ns(e2_fast_min)} | — | — |",
        f"| `{M_FAST}` | {len(e2_m_fast)} | {fmt_ns(e2_m_med)} | "
        f"{fmt_ns(e2_m_iqr)} | {fmt_ns(e2_m_min)} | "
        f"{fmt_signed_ms3(claim_a_b1)} | {fmt_signed_ms3(claim_a_b2)} |",
        "",
        f"Claim A (shim − OpenSBI, pooled): **{fmt_signed_ms3(claim_a)}**.",
        "",
        "### Claim B — ΔE0→E4 per batch, never pooled",
        "",
        "The quantity being removed is the OpenSBI-side firmware "
        "window. That window moves across campaigns; per-trial `w_ns` "
        "records it. Claim B is therefore one number per batch, not a "
        "pooled median.",
        "",
        "| batch | OpenSBI fast E0→E4 | shim fast E0→E4 | ΔE0→E4 | ΔS (fast pair) |",
        "|---|---:|---:|---:|---:|",
        f"| `{b1}` | {fmt_ns(e4[(FAST, b1)])} | "
        f"{fmt_ns(e4[(M_FAST, b1)])} | {fmt_signed_ms3(d_e4[b1])} | "
        f"{fmt_signed_ms3(delta_s)} |",
        f"| `{b2}` | {fmt_ns(e4[(FAST, b2)])} | "
        f"{fmt_ns(e4[(M_FAST, b2)])} | {fmt_signed_ms3(d_e4[b2])} | "
        f"{fmt_signed_ms3(delta_s)} |",
        "",
        "ΔS is a campaign-header quantity (one fast-pair median, not "
        "per batch) sitting in the same table because it is a "
        "different kind of firmware cost from the guest window Claim B "
        "removes.",
        "",
        "### Decomposition — three terms, not one firmware number",
        "",
        "D-0079: the E0→E4 improvement is guest firmware execution "
        "removed + ΔS (host-side firmware load removed) + seam "
        "deltas. Those are different kinds of cost.",
        "",
        "| term | kind | how | batch 1 | batch 2 |",
        "|---|---|---|---:|---:|",
        "| guest firmware execution removed | guest M-mode runtime "
        "(OpenSBI init / banner / jump) | "
        "ΔE0→E4 − ΔE2→E3g − ΔS, per batch | "
        f"{fmt_signed_ms3(guest_fw[b1])} | "
        f"{fmt_signed_ms3(guest_fw[b2])} |",
        "| ΔS | host-side firmware load | "
        "S(`release-fast-boot`) − S(`m-release-fast-boot`); "
        "never pooled across lanes | "
        f"{fmt_signed_ms3(delta_s)} | {fmt_signed_ms3(delta_s)} |",
        "| seam envelope | guest work that contains the seams | "
        "Claim A (pooled ΔE2→E3g) | "
        f"{fmt_signed_ms3(claim_a)} | {fmt_signed_ms3(claim_a)} |",
        "",
        "Seam deltas itemised (D-0079 registered set "
        "`stvec`, `frame_init`, `E3g` — replaced-SBI call sites or "
        "in-window print). These are inside the Claim A envelope; "
        "they are not folded into the firmware window.",
        "",
        "| phase | OpenSBI fast | shim fast | Δ (shim − OpenSBI) |",
        "|---|---:|---:|---:|",
    ]
    for phase, open_med, shim_med, delta in seams:
        lines.append(
            f"| `{phase}` | {fmt_ns(open_med)} | {fmt_ns(shim_med)} | "
            f"{fmt_delta(delta)} |"
        )
    lines.extend(
        [
            "",
            "### Per-lane S (batch header, never pooled across lanes)",
            "",
            "S is not a CSV column. Each line is one config's recorded "
            "median of `(E0→E4 − E0→first_connect) − (W + SYN→HTTP + "
            "D_fin)`. OpenSBI and shim are different populations by "
            "construction (D-0062 / D-0071 / D-0079). The shim-safe "
            "arm is a known open item with no consumer — quoted, never "
            "gated, never pooled with the fast pair that carries ΔS.",
            "",
            f"`s_ns_fast={s_fast:.0f}` `s_ns_safe={s_safe:.0f}`",
            f"`s_ns_m_fast={s_m_fast:.0f}` `s_ns_m_safe={s_m_safe:.0f}`",
            f"ΔS = {fmt_signed_ms3(delta_s)} — {ds_expect}. "
            "Falsifiers (unchanged coarse sanity): "
            f"ΔS < {fmt_signed_ms3(DS_FALSIFY_SLOWER_NS)} or "
            f"|ΔS| > {fmt_ms3(DS_FALSIFY_ABS_NS)}.",
        ]
    )
    if shim_s_span > CONTROL_TOL_NS:
        lines.extend(
            [
                "",
                f"Shim-lane S is profile-dependent "
                f"(|s_ns_m_fast − s_ns_m_safe| = {fmt_ns(shim_s_span)}). "
                "D-0079 open item; no consumer.",
            ]
        )
    lines.extend(
        [
            "",
            "### Published claim",
            "",
            "Under QEMU TCG software emulation on RISC-V, with no KVM, "
            "on the pinned bench host (D-0055 controls: "
            f"{len(batches)} interleaved {n_per_batch}-trial batches, "
            "Claim A stability-gated, steal 0), replacing OpenSBI with "
            "the M-mode shim in the `-bios` slot cuts the fast-boot "
            "image's spawn-to-first-HTTP-byte median from "
            f"{fmt_ns(e4[(FAST, b1)])} / {fmt_ns(e4[(FAST, b2)])} to "
            f"{fmt_ns(e4[(M_FAST, b1)])} / {fmt_ns(e4[(M_FAST, b2)])} "
            f"per batch ({fmt_signed_ms3(d_e4[b1])} / "
            f"{fmt_signed_ms3(d_e4[b2])}, {ratio_b1} / {ratio_b2}), of "
            f"which only {fmt_signed_ms3(claim_a)} is guest-side work "
            "(pooled ΔE2→E3g); the remainder is the removed firmware "
            "window, whose OpenSBI-side size is the volatile quantity "
            "being removed and is therefore reported per batch, never "
            "pooled.",
            "",
        ]
    )
    return "\n".join(lines)


def cmd_selftest() -> int:
    """Planted failing inputs for validate / validate_t48 / validate_t47.

    Does not write exhibits.
    """
    fired: list[str] = []

    def expect_fail(fn, needle: str, label: str) -> None:
        try:
            fn()
            raise ExhibitFail(f"{label} did not fire")
        except ExhibitFail as e:
            if needle not in str(e):
                raise
            fired.append(f"{label}: {e}")

    batches = frozenset({"b-1", "b-2"})
    sha = "abc1234deadbeef"
    n = 2

    def old_run(**over: object) -> dict:
        row = {
            "batch_id": "b-1",
            "trial": "1",
            "warmup": "0",
            "system": "whimbrel",
            "config": SAFE,
            "git_sha": sha,
            "dirty": "0",
            "steal_ticks": "0",
            "virt": "none",
            "governor": "performance",
            "smt_control": "off",
            "cpufreq_boost": "0",
            "e0_to_e3w_ns": "1",
            "e0_to_e4_ns": "1",
            "e0_to_first_connect_ns": "1",
        }
        row.update(over)  # type: ignore[arg-type]
        return row

    def e3g_row(batch: str, trial: str, cfg: str) -> dict:
        return {
            "batch_id": batch,
            "trial": trial,
            "warmup": "0",
            "config": cfg,
            "phase": "E3g",
        }

    def old_campaign() -> tuple[list[dict], list[dict]]:
        runs: list[dict] = []
        phases: list[dict] = []
        for batch in ("b-1", "b-2"):
            for cfg in (SAFE, FAST):
                t = "1"
                runs.append(old_run(batch_id=batch, trial=t, config=cfg))
                phases.append(e3g_row(batch, t, cfg))
        return runs, phases

    def check_old(runs: list[dict], phases: list[dict], **kw: object) -> None:
        validate(
            runs,
            phases,
            batches,
            "abc1234",
            "old-fix",
            n_per_cfg=n,
            **kw,  # type: ignore[arg-type]
        )

    runs, phases = old_campaign()
    check_old(runs, phases)
    fired.append("validate accepts a clean old-schema fixture")

    newish = [dict(r, w_ns="1", d_ack_ns="1", d_fin_ns="1") for r in runs]
    for r in newish:
        del r["e0_to_e3w_ns"]
    expect_fail(
        lambda: check_old(newish, phases),
        "is not old-schema",
        "validate schema",
    )
    bad_batch = [dict(r, batch_id="other" if r["batch_id"] == "b-1" else r["batch_id"]) for r in runs]
    expect_fail(
        lambda: check_old(bad_batch, phases),
        "batch_id set",
        "validate batch-set",
    )
    mixed = [dict(r) for r in runs]
    mixed[0]["git_sha"] = "zzzzzzzdeadbeef"
    expect_fail(lambda: check_old(mixed, phases), "mixed git_sha", "validate mixed sha")
    expect_fail(
        lambda: validate(runs, phases, batches, "nope", "old-fix", n_per_cfg=n),
        "does not start with",
        "validate sha-prefix",
    )
    dirty = [dict(r, dirty="1") for r in runs]
    expect_fail(lambda: check_old(dirty, phases), "dirty-tree row", "validate dirty")
    one_cfg = [r for r in runs if r["config"] == SAFE]
    one_ph = [p for p in phases if p["config"] == SAFE]
    expect_fail(lambda: check_old(one_cfg, one_ph), "configs", "validate configs")
    short = runs[:-1]
    short_ph = phases[:-1]
    expect_fail(
        lambda: check_old(short, short_ph),
        "recorded trials, want 2",
        "validate n-per-arm",
    )
    stolen = [dict(r, steal_ticks="1") for r in runs]
    expect_fail(
        lambda: check_old(stolen, phases),
        "nonzero steal_ticks",
        "validate steal",
    )
    nogov = [dict(r) for r in runs]
    for r in nogov:
        del r["governor"]
    expect_fail(
        lambda: check_old(nogov, phases),
        "runs.csv missing governor",
        "validate missing host-control field",
    )
    gov = [dict(r, governor="powersave") for r in runs]
    expect_fail(
        lambda: check_old(gov, phases),
        "governor values",
        "validate governor",
    )
    expect_fail(
        lambda: check_old(runs, []),
        "recorded E3g rows",
        "validate E3g count",
    )

    stock_h = "a" * 64
    trim_h = "b" * 64
    man_text = (
        f"artifact Image-stock {stock_h}\n"
        f"artifact Image-trimmed {trim_h}\n"
        f"artifact rootfs.cpio {'c' * 64}\n"
        f"artifact init {'d' * 64}\n"
    )
    t48_batches = frozenset({"t-1", "t-2"})
    t48_sha = "1005399fixture"
    t48_n = 2

    def new_run(**over: object) -> dict:
        row = {
            "batch_id": "t-1",
            "trial": "1",
            "warmup": "0",
            "system": "whimbrel",
            "config": FAST,
            "git_sha": t48_sha,
            "dirty": "0",
            "steal_ticks": "0",
            "virt": "none",
            "governor": "performance",
            "smt_control": "off",
            "cpufreq_boost": "0",
            "kernel_sha256": "k" * 64,
            "w_ns": "1",
            "d_ack_ns": "1",
            "d_fin_ns": "1",
            "e0_to_e4_ns": "45000000",
            "e0_to_first_connect_ns": "18500000",
        }
        row.update(over)  # type: ignore[arg-type]
        return row

    def t48_campaign() -> tuple[list[dict], list[dict]]:
        runs: list[dict] = []
        phases: list[dict] = []
        hashes = {
            "stock": stock_h,
            "trimmed": trim_h,
            "trimmed-instrumented": trim_h,
        }
        e4 = {"stock": "50000000", "trimmed": "40000000"}
        for batch in ("t-1", "t-2"):
            for sys, cfg in T48_ARM_ORDER:
                runs.append(
                    new_run(
                        batch_id=batch,
                        trial="1",
                        system=sys,
                        config=cfg,
                        kernel_sha256=hashes.get(cfg, "k" * 64),
                        e0_to_e4_ns=e4.get(cfg, "45000000"),
                    )
                )
                if cfg in {FAST, SAFE}:
                    phases.append(
                        {
                            "batch_id": batch,
                            "trial": "1",
                            "warmup": "0",
                            "config": cfg,
                            "phase": "E3g",
                            "system": "whimbrel",
                        }
                    )
        return runs, phases

    def check_t48(runs: list[dict], phases: list[dict], **kw: object) -> None:
        validate_t48(
            runs,
            phases,
            rev="unused",
            batches=t48_batches,
            sha_prefix="1005399",
            n_per_arm=t48_n,
            label="t48-fix",
            manifest_text=man_text,
            **kw,  # type: ignore[arg-type]
        )

    t_runs, t_ph = t48_campaign()
    check_t48(t_runs, t_ph)
    fired.append("validate_t48 accepts a clean five-arm fixture")

    old_t = [dict(r, e0_to_e3w_ns="1") for r in t_runs]
    for r in old_t:
        del r["w_ns"]
        del r["d_ack_ns"]
        del r["d_fin_ns"]
    expect_fail(
        lambda: check_t48(old_t, t_ph),
        "is not new-schema",
        "validate_t48 schema",
    )
    t_batch = [dict(r, batch_id="x" if r["batch_id"] == "t-1" else r["batch_id"]) for r in t_runs]
    expect_fail(
        lambda: check_t48(t_batch, t_ph),
        "batch_id set",
        "validate_t48 batch-set",
    )
    t_mixed = [dict(r) for r in t_runs]
    t_mixed[0]["git_sha"] = "0" * 12
    expect_fail(
        lambda: check_t48(t_mixed, t_ph), "mixed git_sha", "validate_t48 mixed sha"
    )
    expect_fail(
        lambda: validate_t48(
            t_runs,
            t_ph,
            rev="unused",
            batches=t48_batches,
            sha_prefix="nope",
            n_per_arm=t48_n,
            label="t48-fix",
            manifest_text=man_text,
        ),
        "does not start with",
        "validate_t48 sha-prefix",
    )
    t_dirty = [dict(r, dirty="1") for r in t_runs]
    expect_fail(
        lambda: check_t48(t_dirty, t_ph), "dirty-tree row", "validate_t48 dirty"
    )
    t_cfg = [r for r in t_runs if r["config"] != "stock"]
    expect_fail(lambda: check_t48(t_cfg, t_ph), "configs", "validate_t48 configs")
    t_short = [r for r in t_runs if not (r["config"] == "stock" and r["batch_id"] == "t-2")]
    expect_fail(
        lambda: check_t48(t_short, t_ph),
        "stock has 1 recorded trials, want 2",
        "validate_t48 n-per-arm",
    )
    t_sys = [dict(r, system="whimbrel") if r["config"] == "stock" else dict(r) for r in t_runs]
    expect_fail(
        lambda: check_t48(t_sys, t_ph),
        "system",
        "validate_t48 arm system",
    )
    t_steal = [dict(r, steal_ticks="1") for r in t_runs]
    expect_fail(
        lambda: check_t48(t_steal, t_ph),
        "nonzero steal_ticks",
        "validate_t48 steal",
    )
    t_gov = [dict(r, governor="schedutil") for r in t_runs]
    expect_fail(
        lambda: check_t48(t_gov, t_ph),
        "governor values",
        "validate_t48 governor",
    )
    t_hash = [
        dict(r, kernel_sha256="e" * 64) if r["config"] == "stock" else dict(r)
        for r in t_runs
    ]
    expect_fail(
        lambda: check_t48(t_hash, t_ph),
        "kernel_sha256",
        "validate_t48 MANIFEST-hash",
    )
    expect_fail(
        lambda: validate_t48(
            t_runs,
            t_ph,
            rev="unused",
            batches=t48_batches,
            sha_prefix="1005399",
            n_per_arm=t48_n,
            label="t48-fix",
            manifest_text="artifact Image-stock " + stock_h + "\n",
        ),
        "MANIFEST missing",
        "validate_t48 MANIFEST incomplete",
    )
    t_span = [
        dict(r, e0_to_first_connect_ns="20500000") if r["config"] == FAST else dict(r)
        for r in t_runs
    ]
    expect_fail(
        lambda: check_t48(t_span, t_ph),
        "first-connect medians span",
        "validate_t48 span",
    )
    t_trip = [
        dict(r, e0_to_e4_ns="60000000") if r["config"] == "trimmed" else dict(r)
        for r in t_runs
    ]
    expect_fail(
        lambda: check_t48(t_trip, t_ph),
        "trimmed is not published",
        "validate_t48 tripwire",
    )
    linux_ph = t_ph + [
        {
            "batch_id": "t-1",
            "trial": "1",
            "warmup": "0",
            "config": "stock",
            "phase": "E3g",
            "system": "linux",
        }
    ]
    expect_fail(
        lambda: check_t48(t_runs, linux_ph),
        "Linux PHASE rows",
        "validate_t48 linux PHASE",
    )
    expect_fail(
        lambda: check_t48(t_runs, []),
        "recorded Whimbrel E3g rows",
        "validate_t48 E3g count",
    )

    t47_batches = frozenset({"g-1", "g-2"})
    t47_sha = "346f4c1fixture"
    t47_n = 2
    t47_bios = "c" * 64

    def t47_run(**over: object) -> dict:
        cfg = str(over.get("config", FAST))
        e4 = {
            FAST: "50000000",
            SAFE: "140000000",
            M_FAST: "27000000",
            M_SAFE: "120000000",
        }[cfg]
        w = {
            FAST: "25000000",
            SAFE: "25000000",
            M_FAST: "2000000",
            M_SAFE: "2000000",
        }[cfg]
        row = {
            "batch_id": "g-1",
            "trial": "1",
            "warmup": "0",
            "system": "whimbrel",
            "config": cfg,
            "git_sha": t47_sha,
            "dirty": "0",
            "steal_ticks": "0",
            "virt": "none",
            "governor": "performance",
            "smt_control": "off",
            "cpufreq_boost": "0",
            "w_ns": w,
            "d_ack_ns": "1",
            "d_fin_ns": "100000",
            "synack_to_http_ns": "500000",
            "e0_to_e4_ns": e4,
            "e0_to_first_connect_ns": "18500000",
            "canary_stvec_ns": "1000000",
            "canary_page_verify_ns": "12000000",
            "bios_sha256": t47_bios if cfg in T47_SHIM else "",
        }
        row.update(over)  # type: ignore[arg-type]
        return row

    def t47_phase(
        batch: str, cfg: str, phase: str, *, since: str, delta: str
    ) -> dict:
        return {
            "batch_id": batch,
            "trial": "1",
            "warmup": "0",
            "config": cfg,
            "phase": phase,
            "ns_since_e2": since,
            "delta_ns": delta,
        }

    def t47_campaign() -> tuple[list[dict], list[dict]]:
        runs: list[dict] = []
        phases: list[dict] = []
        e2 = {FAST: "6400000", SAFE: "90000000", M_FAST: "5700000", M_SAFE: "80000000"}
        for batch in ("g-1", "g-2"):
            for cfg in T47_ARM_ORDER:
                runs.append(t47_run(batch_id=batch, config=cfg))
                phases.append(
                    t47_phase(batch, cfg, "E3g", since=e2[cfg], delta="700000")
                )
                if cfg in (FAST, M_FAST):
                    phases.append(
                        t47_phase(batch, cfg, "stvec", since="200000", delta="200000")
                    )
                    phases.append(
                        t47_phase(
                            batch, cfg, "frame_init", since="300000", delta="100000"
                        )
                    )
        return runs, phases

    def check_t47(runs: list[dict], phases: list[dict], **kw: object) -> None:
        validate_t47(
            runs,
            phases,
            batches=t47_batches,
            sha_prefix="346f4c1",
            n_per_arm=t47_n,
            label="t47-fix",
            **kw,  # type: ignore[arg-type]
        )

    g_runs, g_ph = t47_campaign()
    check_t47(g_runs, g_ph)
    fired.append("validate_t47 accepts a clean four-arm fixture")
    g_md = write_t47_firmware(recorded(g_runs), g_ph)
    if "QEMU TCG software emulation" not in g_md or "no KVM" not in g_md:
        raise ExhibitFail("TEST FAIL: T4.7 exhibit missing TCG substrate")
    tcg_ok = any(
        "QEMU TCG software emulation" in sent
        and "no KVM" in sent
        and "×" in sent
        for sent in re.split(r"(?<=\.)\s+", g_md)
    )
    if not tcg_ok:
        raise ExhibitFail(
            "TEST FAIL: T4.7 exhibit ratio is not in the same sentence "
            "as QEMU TCG / no KVM"
        )
    if re.search(r"s_ns=\d", g_md):
        raise ExhibitFail(
            "TEST FAIL: T4.7 exhibit pooled an s_ns= line across lanes"
        )
    if "+0.1" in g_md or "1.5 ms" in g_md:
        raise ExhibitFail(
            "TEST FAIL: T4.7 exhibit used the retired ΔS load-model window"
        )
    fired.append("write_t47_firmware accepts a clean fixture")

    old_g = [dict(r, e0_to_e3w_ns="1") for r in g_runs]
    for r in old_g:
        del r["w_ns"]
        del r["d_ack_ns"]
        del r["d_fin_ns"]
    expect_fail(
        lambda: check_t47(old_g, g_ph),
        "is not new-schema",
        "validate_t47 schema",
    )
    no_canary = [dict(r) for r in g_runs]
    for r in no_canary:
        del r["canary_stvec_ns"]
        del r["canary_page_verify_ns"]
    expect_fail(
        lambda: check_t47(no_canary, g_ph),
        "missing canary column",
        "validate_t47 missing canary",
    )
    empty_canary = [dict(r, canary_stvec_ns="") for r in g_runs]
    expect_fail(
        lambda: check_t47(empty_canary, g_ph),
        "empty canary columns",
        "validate_t47 empty canary",
    )
    split_canary = [
        dict(r, canary_stvec_ns="999") if r["config"] in T47_SHIM else dict(r)
        for r in g_runs
    ]
    expect_fail(
        lambda: check_t47(split_canary, g_ph),
        "canary values",
        "validate_t47 split canary",
    )
    cross = []
    cross_ph = []
    for r in g_runs:
        row = dict(r)
        if row["config"] in T47_SHIM:
            row["batch_id"] = "x-1" if row["batch_id"] == "g-1" else "x-2"
        cross.append(row)
    for p in g_ph:
        row = dict(p)
        if row["config"] in T47_SHIM:
            row["batch_id"] = "x-1" if row["batch_id"] == "g-1" else "x-2"
        cross_ph.append(row)
    expect_fail(
        lambda: check_t47(cross, cross_ph),
        "lanes from different batch sets",
        "validate_t47 cross-campaign",
    )
    wrong_pin = [dict(r, batch_id="z-1" if r["batch_id"] == "g-1" else "z-2") for r in g_runs]
    wrong_ph = [dict(p, batch_id="z-1" if p["batch_id"] == "g-1" else "z-2") for p in g_ph]
    expect_fail(
        lambda: check_t47(wrong_pin, wrong_ph),
        "batch_id set",
        "validate_t47 batch-set",
    )
    mixed_sha = [dict(r) for r in g_runs]
    mixed_sha[0]["git_sha"] = "zzzzzzzdeadbeef"
    expect_fail(
        lambda: check_t47(mixed_sha, g_ph),
        "mixed git_sha",
        "validate_t47 mixed sha",
    )
    expect_fail(
        lambda: validate_t47(
            g_runs, g_ph, batches=t47_batches, sha_prefix="nope", n_per_arm=t47_n,
            label="t47-fix",
        ),
        "does not start with",
        "validate_t47 sha-prefix",
    )
    dirty = [dict(r, dirty="1") for r in g_runs]
    expect_fail(lambda: check_t47(dirty, g_ph), "dirty-tree row", "validate_t47 dirty")
    one_lane = [r for r in g_runs if r["config"] in T47_OPENSBI]
    one_ph = [p for p in g_ph if p["config"] in T47_OPENSBI]
    expect_fail(lambda: check_t47(one_lane, one_ph), "configs", "validate_t47 configs")
    short = g_runs[:-1]
    short_ph = g_ph[:-1]
    expect_fail(
        lambda: check_t47(short, short_ph),
        "recorded trials, want 2",
        "validate_t47 n-per-arm",
    )
    stolen = [dict(r, steal_ticks="1") for r in g_runs]
    expect_fail(
        lambda: check_t47(stolen, g_ph),
        "nonzero steal_ticks",
        "validate_t47 steal",
    )
    nogov = [dict(r) for r in g_runs]
    for r in nogov:
        del r["governor"]
    expect_fail(
        lambda: check_t47(nogov, g_ph),
        "runs.csv missing governor",
        "validate_t47 missing host-control field",
    )
    gov = [dict(r, governor="powersave") for r in g_runs]
    expect_fail(
        lambda: check_t47(gov, g_ph),
        "governor values",
        "validate_t47 governor",
    )
    linux_sys = [
        dict(r, system="linux") if r["config"] == FAST else dict(r) for r in g_runs
    ]
    expect_fail(
        lambda: check_t47(linux_sys, g_ph),
        "system",
        "validate_t47 system",
    )
    no_bios = [
        dict(r, bios_sha256="") if r["config"] in T47_SHIM else dict(r) for r in g_runs
    ]
    expect_fail(
        lambda: check_t47(no_bios, g_ph),
        "shim bios_sha256",
        "validate_t47 bios",
    )
    span = [
        dict(r, e0_to_first_connect_ns="20500000") if r["config"] == M_FAST else dict(r)
        for r in g_runs
    ]
    expect_fail(
        lambda: check_t47(span, g_ph),
        "first-connect medians span",
        "validate_t47 span",
    )
    expect_fail(
        lambda: check_t47(g_runs, []),
        "recorded E3g rows",
        "validate_t47 E3g count",
    )
    no_seam = [p for p in g_ph if p["phase"] != "stvec"]
    expect_fail(
        lambda: check_t47(g_runs, no_seam),
        "stvec rows",
        "validate_t47 seam count",
    )
    unstable = []
    for p in g_ph:
        row = dict(p)
        if (
            row["config"] == M_FAST
            and row["phase"] == "E3g"
            and row["batch_id"] == "g-2"
        ):
            row["ns_since_e2"] = "9000000"
        unstable.append(row)
    expect_fail(
        lambda: check_t47(g_runs, unstable),
        "E2→E3g not stable",
        "validate_t47 Claim A stability",
    )
    ds_slow = [
        dict(r, w_ns="1000000") if r["config"] == M_FAST else dict(r) for r in g_runs
    ]
    expect_fail(
        lambda: check_t47(ds_slow, g_ph),
        "< −0.3 ms",
        "validate_t47 ΔS slower",
    )
    ds_wide = [
        dict(r, w_ns="20000000") if r["config"] == FAST else dict(r) for r in g_runs
    ]
    expect_fail(
        lambda: check_t47(ds_wide, g_ph),
        "> 3 ms",
        "validate_t47 |ΔS|",
    )

    t47c_runs = read_csv_text(
        git_show(T47_REV, "results/runs.csv"),
        f"{T47_REV}:results/runs.csv",
    )
    t47c_ph = read_csv_text(
        git_show(T47_REV, "results/phases.csv"),
        f"{T47_REV}:results/phases.csv",
    )
    t47b_runs = read_csv_text(
        git_show(T47B_REV, "results/runs.csv"),
        f"{T47B_REV}:results/runs.csv",
    )
    t47b_ph = read_csv_text(
        git_show(T47B_REV, "results/phases.csv"),
        f"{T47B_REV}:results/phases.csv",
    )
    mixed_pin = [r for r in t47c_runs if r["config"] in T47_OPENSBI] + [
        r for r in t47b_runs if r["config"] in T47_SHIM
    ]
    mixed_pin_ph = [p for p in t47c_ph if p["config"] in T47_OPENSBI] + [
        p for p in t47b_ph if p["config"] in T47_SHIM
    ]
    expect_fail(
        lambda: validate_t47(mixed_pin, mixed_pin_ph),
        "lanes from different batch sets",
        "validate_t47 planted t47c/t47b pair",
    )

    print("TEST PASS: report-exhibits fail-closed selftest")
    for line in fired:
        print(f"  fired: {line}")
    return 0


def main() -> int:
    try:
        base_runs = read_csv_text(
            git_show(BASELINE_TAG, "results/runs.csv"),
            f"{BASELINE_TAG}:results/runs.csv",
        )
        base_phases = read_csv_text(
            git_show(BASELINE_TAG, "results/phases.csv"),
            f"{BASELINE_TAG}:results/phases.csv",
        )
        after_runs = read_csv_text(
            git_show(AFTER_REV, "results/runs.csv"),
            f"{AFTER_REV}:results/runs.csv",
        )
        after_phases = read_csv_text(
            git_show(AFTER_REV, "results/phases.csv"),
            f"{AFTER_REV}:results/phases.csv",
        )
        baseline_summary = git_show(BASELINE_TAG, "results/baseline-summary.txt")
        validate(
            base_runs, base_phases, BASELINE_BATCHES, BASELINE_SHA_PREFIX, "baseline"
        )
        validate(
            after_runs, after_phases, AFTER_BATCHES, AFTER_SHA_PREFIX, "after"
        )
        t48_runs = read_csv_text(
            git_show(T48_REV, "results/runs.csv"),
            f"{T48_REV}:results/runs.csv",
        )
        t48_phases = read_csv_text(
            git_show(T48_REV, "results/phases.csv"),
            f"{T48_REV}:results/phases.csv",
        )
        validate_t48(t48_runs, t48_phases)
        t48b_runs = read_csv_text(
            git_show(T48B_REV, "results/runs.csv"),
            f"{T48B_REV}:results/runs.csv",
        )
        t48b_phases = read_csv_text(
            git_show(T48B_REV, "results/phases.csv"),
            f"{T48B_REV}:results/phases.csv",
        )
        validate_t48(
            t48b_runs,
            t48b_phases,
            rev=T48B_REV,
            batches=T48B_BATCHES,
            sha_prefix=T48B_SHA_PREFIX,
            n_per_arm=T48B_N_PER_ARM,
            label="T4.8b",
        )
        t47_runs = read_csv_text(
            git_show(T47_REV, "results/runs.csv"),
            f"{T47_REV}:results/runs.csv",
        )
        t47_phases = read_csv_text(
            git_show(T47_REV, "results/phases.csv"),
            f"{T47_REV}:results/phases.csv",
        )
        validate_t47(t47_runs, t47_phases)
        base_rec = recorded(base_runs)
        after_rec = recorded(after_runs)
        t48_rec = recorded(t48_runs)
        t48b_rec = recorded(t48b_runs)
        t47_rec = recorded(t47_runs)
        e2e3g_after_fast = e2e3g_median(after_rec, after_phases, FAST)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "machine-spec.md").write_text(
            write_machine_spec(
                base_rec, after_rec, baseline_summary, t48_rec=t48_rec
            ),
            encoding="utf-8",
        )
        (OUT_DIR / "phase-decomposition.md").write_text(
            write_phase_table(
                base_rec,
                base_phases,
                after_rec,
                after_phases,
                e2e3g_after_fast,
            ),
            encoding="utf-8",
        )
        (OUT_DIR / "edges.md").write_text(
            write_edges(
                base_rec,
                base_phases,
                after_rec,
                after_phases,
                t48_rec=t48_rec,
                t48_phases=t48_phases,
            ),
            encoding="utf-8",
        )
        (OUT_DIR / "dump-placement.md").write_text(
            write_dump_placement(),
            encoding="utf-8",
        )
        (OUT_DIR / "cross-system.md").write_text(
            write_cross_system(
                t48_rec, t48_phases, after_rec, after_phases
            ),
            encoding="utf-8",
        )
        (OUT_DIR / "cross-system-t48b.md").write_text(
            write_cross_system_t48b(
                t48b_rec, t48b_phases, t48_rec, t48_phases
            ),
            encoding="utf-8",
        )
        linux_serial = git_show(SERIAL_REV, LINUX_SERIAL_PATH)
        whim_serial = git_show(SERIAL_REV, WHIMBREL_SERIAL_PATH)
        labels_text = git_show(LABEL_REV, LABEL_PATH)
        fragment_text = git_show(LABEL_REV, FRAGMENT_PATH)
        manifest_text = git_show(LABEL_REV, MANIFEST_PATH)
        (OUT_DIR / "linux-decomposition.md").write_text(
            write_linux_decomposition(
                linux_serial,
                whim_serial,
                labels_text,
                fragment_text,
                manifest_text,
            ),
            encoding="utf-8",
        )
        (OUT_DIR / "t47-firmware.md").write_text(
            write_t47_firmware(t47_rec, t47_phases),
            encoding="utf-8",
        )
        print(
            f"TEST PASS: exhibits from {BASELINE_TAG} + {AFTER_REV} + "
            f"{T48_REV[:12]} + {T48B_REV[:12]} + {SERIAL_REV[:12]} + "
            f"{LABEL_REV[:12]} + {T47_REV[:12]} → {OUT_DIR}"
        )
        print((OUT_DIR / "machine-spec.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "phase-decomposition.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "edges.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "dump-placement.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "cross-system.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "cross-system-t48b.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "linux-decomposition.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "t47-firmware.md").read_text(encoding="utf-8"))
        return 0
    except ExhibitFail as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["selftest"]:
        try:
            sys.exit(cmd_selftest())
        except ExhibitFail as e:
            print(e, file=sys.stderr)
            sys.exit(1)
    if len(sys.argv) > 1:
        print("usage: report-exhibits.py [selftest]", file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
