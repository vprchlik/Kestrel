#!/usr/bin/env python3
"""Generate the T4.3 report exhibits from frozen baseline CSVs (D-0064).

Tables are computed from the T4.3 freeze CSVs in results/{runs,phases}.csv
(tag baseline-t4.3). Never type the numbers this script prints.
`just report-exhibits` regenerates report/exhibits/.
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREEZE_DIR = ROOT / "results"
OUT_DIR = ROOT / "report" / "exhibits"
SUMMARY = FREEZE_DIR / "baseline-summary.txt"

# The freeze (D-0055). Wrong CSVs must not silently become the exhibit.
WANT_BATCHES = frozenset({"20260817T041311Z-1", "20260817T041311Z-2"})
WANT_SHA_PREFIX = "35861f3"
WANT_TAG = "baseline-t4.3"
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
    "frame_init": "eager free-list link of remaining RAM (+ DTB check)",
    "task_init": "fabricate four task frames (three Exited)",
    "page_build": "Sv39 identity map, 4 KiB leaves",
    "page_verify": "full second walk of the map (D-0043 paranoia)",
    "activate": "`satp` write + `sfence.vma`",
    "virtq_init": "first virtqueue program+verify (wiped by later reset)",
    "DRIVER_OK": "virtio-net reset, second program+verify, DRIVER_OK",
    "first_rx": "gateway ARP reply arrived (slirp RTT, not kernel)",
    "serving_ready": "gateway MAC learned; earliest serve point",
    "net_init_done": "GARP + diagnostic `ping_gateway` done",
    "heap_init": "kernel heap init (idle in production images)",
    "accounting": "`free_count()` walk of the free list",
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
    "frame_init": "an allocator, not this O(n) link (D-0058 rung 1)",
    "task_init": "yes — U-mode task slots",
    "page_build": "yes — Sv39",
    "page_verify": "no — paranoia; kept as its own line (D-0043)",
    "activate": "yes — paging on",
    "virtq_init": "no — doubled with DRIVER_OK (finding 4)",
    "DRIVER_OK": "yes — the NIC",
    "first_rx": "no — slirp RTT",
    "serving_ready": "ARP wait; not kernel compute",
    "net_init_done": "no — ping is diagnostic",
    "heap_init": "no — heap is idle (finding 11)",
    "accounting": "no — paranoia; O(1) is D-0060",
    "freeze": "the bool, not a second walk",
    "sret": "yes — U-mode",
    "syn_rx": "external arrival",
    "established": "protocol",
    "E3g": "yes — the byte",
    "E3g_doorbell": "the notify; priced separately (D-0056.2)",
}


class ExhibitFail(Exception):
    pass


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


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise ExhibitFail(f"TEST FAIL: missing {path}")
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ExhibitFail(f"TEST FAIL: empty {path}")
    return rows


def fmt_ns(ns: float) -> str:
    if ns >= 1_000_000:
        return f"{ns / 1e6:.2f} ms"
    if ns >= 1_000:
        return f"{ns / 1e3:.1f} µs"
    return f"{ns:.0f} ns"


def md_cell(s: str) -> str:
    return s.replace("|", "\\|")


def recorded(rows: list[dict]) -> list[dict]:
    return [r for r in rows if int(r["warmup"]) == 0]


def load_baseline() -> tuple[Path, list[dict], list[dict]]:
    d = FREEZE_DIR
    runs = read_csv(d / "runs.csv")
    phases = read_csv(d / "phases.csv")
    validate(runs, phases)
    return d, runs, phases


def validate(runs: list[dict], phases: list[dict]) -> None:
    rec = recorded(runs)
    batches = {r["batch_id"] for r in runs}
    if batches != WANT_BATCHES:
        raise ExhibitFail(
            f"TEST FAIL: batch_id set {sorted(batches)} want {sorted(WANT_BATCHES)}"
        )
    shas = {r["git_sha"] for r in rec}
    if len(shas) != 1:
        raise ExhibitFail(f"TEST FAIL: mixed git_sha {sorted(shas)}")
    sha = next(iter(shas))
    if not sha.startswith(WANT_SHA_PREFIX):
        raise ExhibitFail(
            f"TEST FAIL: git_sha {sha} does not start with {WANT_SHA_PREFIX}"
        )
    if any(int(r["dirty"]) != 0 for r in rec):
        raise ExhibitFail("TEST FAIL: dirty-tree row in baseline")
    cfgs = {r["config"] for r in rec}
    if cfgs != {SAFE, FAST}:
        raise ExhibitFail(f"TEST FAIL: configs {sorted(cfgs)} want {SAFE}, {FAST}")
    for cfg in (SAFE, FAST):
        n = sum(1 for r in rec if r["config"] == cfg)
        if n != 60:
            raise ExhibitFail(
                f"TEST FAIL: {cfg} has {n} recorded trials, want 60 "
                "(30 × 2 batches)"
            )
    steal = [int(r["steal_ticks"]) for r in rec]
    if any(s != 0 for s in steal):
        raise ExhibitFail(
            f"TEST FAIL: nonzero steal_ticks in recorded baseline "
            f"(nonzero={sum(1 for s in steal if s != 0)}/{len(steal)})"
        )
    if len(rec) != 120:
        raise ExhibitFail(f"TEST FAIL: {len(rec)} recorded trials, want 120")
    for field, want in (
        ("virt", "none"),
        ("governor", "performance"),
        ("smt_control", "off"),
        ("cpufreq_boost", "0"),
    ):
        if field not in rec[0]:
            raise ExhibitFail(f"TEST FAIL: runs.csv missing {field}")
        vals = {r[field] for r in rec}
        if vals != {want}:
            raise ExhibitFail(
                f"TEST FAIL: {field} values {sorted(vals)} want {{{want!r}}}"
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
        raise ExhibitFail(f"TEST FAIL: {len(e3g)} recorded E3g rows, want 120")


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


def write_machine_spec(runs: list[dict]) -> str:
    """Verbatim harness header from baseline-summary.txt; CSV is the check."""
    if not SUMMARY.is_file():
        raise ExhibitFail(f"TEST FAIL: missing {SUMMARY}")
    header: list[str] = []
    for line in SUMMARY.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            break
        header.append(line.rstrip())
    while header and header[-1] == "":
        header.pop()
    rec = recorded(runs)
    extra = [
        "",
        f"tag:                   {WANT_TAG}",
        f"n_recorded:            {len(rec)}",
        f"batches:               {', '.join(sorted(WANT_BATCHES))}",
    ]
    return (
        f"<!-- generated by scripts/report-exhibits.py from {SUMMARY.name} — do not edit -->\n\n"
        + "\n".join(header)
        + "\n"
        + "\n".join(extra)
        + "\n"
    )


def write_phase_table(
    rec: list[dict], phases: list[dict], e2e3g_fast: float
) -> str:
    safe = phase_deltas(rec, phases, SAFE)
    fast = phase_deltas(rec, phases, FAST)
    header = (
        "| phase | what the work is | safe median | fast median | "
        "fast IQR | fast min | after-ladder median | Δ vs baseline | "
        "structurally necessary? |"
    )
    sep = "|---|---|---:|---:|---:|---:|---|---|---|"
    rows = [header, sep]
    for name in PHASE_ORDER:
        if name not in fast and name not in safe:
            continue
        s_med = stat(safe[name])[0] if name in safe else None
        f_med, f_iqr, f_min = stat(fast[name]) if name in fast else (None, None, None)
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
                    "—",
                    "— (this is the baseline)",
                    md_cell(PHASE_NECESSARY.get(name, "")),
                ]
            )
            + " |"
        )
    # Derived share of E2→E3g on the fast path (ratio of medians).
    share_lines = [
        "",
        f"Fast-boot E2→E3g median is **{fmt_ns(e2e3g_fast)}**. "
        "Share column is (phase median) / (E2→E3g median), not a median of ratios.",
        "",
        "| phase | fast median | share of E2→E3g |",
        "|---|---:|---:|",
    ]
    ranked = []
    for name in PHASE_ORDER:
        if name not in fast:
            continue
        med = stat(fast[name])[0]
        ranked.append((med, name))
    ranked.sort(reverse=True)
    for med, name in ranked:
        share = 100.0 * med / e2e3g_fast if e2e3g_fast else 0.0
        share_lines.append(
            f"| {md_cell(name)} | {fmt_ns(med)} | {share:.0f}% |"
        )
    return (
        f"<!-- generated by scripts/report-exhibits.py — do not edit -->\n\n"
        f"Pooled recorded trials from batches `{sorted(WANT_BATCHES)[0]}` and "
        f"`{sorted(WANT_BATCHES)[1]}` (n=60 per config). After-ladder and "
        f"Δ columns are empty until a rung lands. Regeneration: "
        f"`just report-exhibits`.\n\n"
        + "\n".join(rows)
        + "\n"
        + "\n".join(share_lines)
        + "\n"
    )


def write_edges(rec: list[dict], phases: list[dict]) -> str:
    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        "Host-observed edges and guest E2→E3g. n=60 recorded per config, "
        "warmup excluded, both freeze batches pooled.",
        "",
        "| config | metric | n | median | IQR | min |",
        "|---|---|---:|---:|---:|---:|",
    ]
    rec_keys = {(r["batch_id"], r["trial"], r["config"]) for r in rec}
    e3g = defaultdict(list)
    doorbell = defaultdict(list)
    overhead = []
    for p in phases:
        if int(p["warmup"]) != 0:
            continue
        if (p["batch_id"], p["trial"], p["config"]) not in rec_keys:
            continue
        if p["phase"] == "E3g":
            e3g[p["config"]].append(float(p["ns_since_e2"]))
        if p["phase"] == "E3g_doorbell":
            doorbell[p["config"]].append(float(p["delta_ns"]))
        if p["phase"] == "stamp_b" and p["config"] == FAST:
            overhead.append(float(p["delta_ns"]))
    for cfg in (FAST, SAFE):
        cfg_rows = [r for r in rec if r["config"] == cfg]
        for metric, vals in (
            ("E0→first-connect", [float(r["e0_to_first_connect_ns"]) for r in cfg_rows]),
            ("E0→E4", [float(r["e0_to_e4_ns"]) for r in cfg_rows]),
            ("E2→E3g", e3g[cfg]),
            ("E3g_doorbell − E3g", doorbell[cfg]),
        ):
            med, iq, mn = stat(vals)
            lines.append(
                f"| {cfg} | {metric} | {len(vals)} | {fmt_ns(med)} | "
                f"{fmt_ns(iq)} | {fmt_ns(mn)} |"
            )
    if overhead:
        med, iq, mn = stat(overhead)
        lines.append(
            f"| {FAST} | stamp overhead (`stamp_b`−`stamp_a`) | "
            f"{len(overhead)} | {fmt_ns(med)} | {fmt_ns(iq)} | {fmt_ns(mn)} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    try:
        src, runs, phases = load_baseline()
        rec = recorded(runs)
        rec_keys_fast = {
            (r["batch_id"], r["trial"]) for r in rec if r["config"] == FAST
        }
        fast_e3g = [
            float(p["ns_since_e2"])
            for p in phases
            if int(p["warmup"]) == 0
            and p["phase"] == "E3g"
            and p["config"] == FAST
            and (p["batch_id"], p["trial"]) in rec_keys_fast
        ]
        e2e3g_fast = statistics.median(fast_e3g)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "machine-spec.md").write_text(
            write_machine_spec(runs), encoding="utf-8"
        )
        (OUT_DIR / "phase-decomposition.md").write_text(
            write_phase_table(rec, phases, e2e3g_fast), encoding="utf-8"
        )
        (OUT_DIR / "edges.md").write_text(
            write_edges(rec, phases), encoding="utf-8"
        )
        print(f"TEST PASS: exhibits from {src} → {OUT_DIR}")
        print((OUT_DIR / "machine-spec.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "phase-decomposition.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "edges.md").read_text(encoding="utf-8"))
        return 0
    except ExhibitFail as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
