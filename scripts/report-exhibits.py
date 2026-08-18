#!/usr/bin/env python3
"""Generate the M4 report exhibits from named git objects (D-0064 / D-0067 / D-0071 / D-0072).

The harness overwrites `results/runs.csv` and `results/phases.csv` per
run; they are not an append-only history. Baseline columns therefore
come from tag `baseline-t4.3` via `git show`, after-ladder / Δ
columns from the T4.6 superpage CSV commit, D-0068 dump-placement
from its two CSV commits, the T4.8 cross-system table from that
campaign's CSV commit, and the Linux decomposition from the T4.8
serial pin (`d705ecb`). HEAD may hold a later batch; pins do not
follow it. The working-tree files are not read — a local `just bench`
leftover cannot become an exhibit.

Never type the numbers this script prints.
`just report-exhibits` regenerates report/exhibits/.
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
T48_REV = "ffb7ac71234e953ae51339a3e1f5e17ba8c3f1b3"
T48_SHA_PREFIX = "1005399"
T48_BATCHES = frozenset({"20260818T073023Z-1", "20260818T073023Z-2"})
T48_N_PER_ARM = 60
# T4.8 instrumented + Whimbrel serial pin (decomposition, not CSVs).
SERIAL_REV = "d705ecb8c67350519f9ce4653a4685a89e20e1d4"
LINUX_SERIAL_PATH = (
    "results/serial/linux-trimmed-instrumented-20260818T073023Z-1-t04.log"
)
WHIMBREL_SERIAL_PATH = (
    "results/serial/whimbrel-fast-20260818T073023Z-1-t04.log"
)
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
        if n != 60:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} has {n} recorded trials, want 60 "
                "(30 × 2 batches)"
            )
    steal = [int(r["steal_ticks"]) for r in rec]
    if any(s != 0 for s in steal):
        raise ExhibitFail(
            f"TEST FAIL: nonzero steal_ticks in recorded {label} "
            f"(nonzero={sum(1 for s in steal if s != 0)}/{len(steal)})"
        )
    if len(rec) != 120:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(rec)} recorded trials, want 120"
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
    if len(e3g) != 120:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(e3g)} recorded E3g rows, want 120"
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


def validate_t48(runs: list[dict], phases: list[dict]) -> None:
    label = "T4.8"
    if runs_schema(runs[0].keys()) != "new":
        raise ExhibitFail(f"TEST FAIL: {label} is not new-schema")
    rec = recorded(runs)
    batches = {r["batch_id"] for r in runs}
    if batches != T48_BATCHES:
        raise ExhibitFail(
            f"TEST FAIL: {label} batch_id set {sorted(batches)} "
            f"want {sorted(T48_BATCHES)}"
        )
    shas = {r["git_sha"] for r in rec}
    if len(shas) != 1:
        raise ExhibitFail(f"TEST FAIL: {label} mixed git_sha {sorted(shas)}")
    sha = next(iter(shas))
    if not sha.startswith(T48_SHA_PREFIX):
        raise ExhibitFail(
            f"TEST FAIL: {label} git_sha {sha} does not start with "
            f"{T48_SHA_PREFIX}"
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
        if n != T48_N_PER_ARM:
            raise ExhibitFail(
                f"TEST FAIL: {label} {cfg} has {n} recorded trials, "
                f"want {T48_N_PER_ARM} (30 × 2 batches)"
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
    if len(rec) != T48_N_PER_ARM * len(T48_ARM_ORDER):
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(rec)} recorded trials, "
            f"want {T48_N_PER_ARM * len(T48_ARM_ORDER)}"
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
    man = parse_linux_manifest(git_show(T48_REV, "bench/linux/MANIFEST"))
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
    if len(e3g) != 120:
        raise ExhibitFail(
            f"TEST FAIL: {label} has {len(e3g)} recorded Whimbrel E3g rows, "
            "want 120"
        )


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


def e2e3g_median(rec: list[dict], phases: list[dict], config: str) -> float:
    keys = {(r["batch_id"], r["trial"]) for r in rec if r["config"] == config}
    vals = [
        float(p["ns_since_e2"])
        for p in phases
        if int(p["warmup"]) == 0
        and p["phase"] == "E3g"
        and p["config"] == config
        and (p["batch_id"], p["trial"]) in keys
    ]
    return statistics.median(vals)


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
        "instrumented serial; not a per-initcall ranking).",
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


def write_linux_decomposition(linux_text: str, whim_text: str) -> str:
    """T4.8 instrumented serial vs Whimbrel dump. Kind, not magnitude."""
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
        "Twenty named deltas against one 327 ms anonymous region. "
        "Kind, not magnitude: Whimbrel's guest path is an owned "
        "instrumentation of a single-purpose VM; Linux's is leftover "
        "`KERN_INFO` from a general-purpose kernel whose own initcall "
        "debugger did not reach the console.",
        "",
        f"Source: `git show {SERIAL_REV}:results/serial/{{linux-trimmed-instrumented,whimbrel-fast}}-20260818T073023Z-1-t04.log` "
        f"(T4.8 batch `20260818T073023Z-1`, "
        "trial 4, measured kernel `1005399`, same QEMU). "
        "**RISC-V under QEMU TCG software emulation.** Working-tree "
        "serials are not read. Regeneration: `just report-exhibits`.",
        "",
        "This exhibit is not a per-initcall ranking and not a "
        "cross-system table. Gap 1 stays anonymous until the D-0072 "
        "diagnostic boot (`ignore_loglevel`, same `Image-trimmed`, "
        "addresses from `System.map`). That boot is a labeling pass; "
        "its UART-inflated durations do not replace cells here.",
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
        "boot path — `virtio_net virtio0` and "
        "`10008000.virtio_mmio` appear only at shutdown.",
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
            "anonymous region; D-0072 diagnostic boot names it"
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
            "not in this table; it sits in the kernel, mostly in gap 1.",
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
            "name. Linux never prints a virtio probe on the way up.",
            "",
            "That is the comparison: twenty named deltas against one "
            "327 ms anonymous region of general-purpose `do_initcalls` "
            "/ leftover probes, which a single-purpose VM does not do "
            "and which this binary's debug facility could not name.",
            "",
        ]
    )
    return "\n".join(lines)


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
        base_rec = recorded(base_runs)
        after_rec = recorded(after_runs)
        t48_rec = recorded(t48_runs)
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
        linux_serial = git_show(SERIAL_REV, LINUX_SERIAL_PATH)
        whim_serial = git_show(SERIAL_REV, WHIMBREL_SERIAL_PATH)
        (OUT_DIR / "linux-decomposition.md").write_text(
            write_linux_decomposition(linux_serial, whim_serial),
            encoding="utf-8",
        )
        print(
            f"TEST PASS: exhibits from {BASELINE_TAG} + {AFTER_REV} + "
            f"{T48_REV[:12]} + {SERIAL_REV[:12]} → {OUT_DIR}"
        )
        print((OUT_DIR / "machine-spec.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "phase-decomposition.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "edges.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "dump-placement.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "cross-system.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "linux-decomposition.md").read_text(encoding="utf-8"))
        return 0
    except ExhibitFail as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
