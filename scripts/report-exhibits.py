#!/usr/bin/env python3
"""Generate the M4 report exhibits from two git revisions (D-0064 / D-0067).

The harness overwrites `results/runs.csv` and `results/phases.csv` per
run; they are not an append-only history. Baseline columns therefore
come from tag `baseline-t4.3` via `git show`, and after-ladder / Δ
columns come from `HEAD` via `git show`. The working-tree files are not
read — a local `just bench` leftover cannot become an exhibit.

Never type the numbers this script prints.
`just report-exhibits` regenerates report/exhibits/.
"""

from __future__ import annotations

import csv
import io
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "report" / "exhibits"

BASELINE_TAG = "baseline-t4.3"
AFTER_REV = "HEAD"

BASELINE_BATCHES = frozenset({"20260817T041311Z-1", "20260817T041311Z-2"})
BASELINE_SHA_PREFIX = "35861f3"
AFTER_BATCHES = frozenset({"20260817T061753Z-1", "20260817T061753Z-2"})
AFTER_SHA_PREFIX = "76830e13"
# Caption label for the after-ladder CSV pin (not the baseline freeze).
LADDER_LABEL = "superpages"

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


def md_cell(s: str) -> str:
    return s.replace("|", "\\|")


def recorded(rows: list[dict]) -> list[dict]:
    return [r for r in rows if int(r["warmup"]) == 0]


def validate(
    runs: list[dict],
    phases: list[dict],
    want_batches: frozenset[str],
    want_sha_prefix: str,
    label: str,
) -> None:
    rec = recorded(runs)
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
        *csv_field_block(after_rec, f"{LADDER_LABEL} HEAD"),
        f"rev:                   {AFTER_REV}",
        f"n_recorded:            {len(after_rec)}",
        f"batches:               {', '.join(sorted(AFTER_BATCHES))}",
        f"source:                git show {AFTER_REV}:results/runs.csv",
    ]
    return (
        "<!-- generated by scripts/report-exhibits.py — do not edit -->\n\n"
        + "\n".join(header)
        + "\n"
        + "\n".join(extra)
        + "\n"
        + "\n".join(after_block)
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
) -> str:
    lines = [
        "<!-- generated by scripts/report-exhibits.py — do not edit -->",
        "",
        "Host-observed edges and guest E2→E3g. Warmup excluded, both "
        "batches of each freeze pooled (n=60 recorded per config). "
        "E3w is first-connect plus the pcap-relative SYN/ACK→HTTP "
        "interval (D-0043); E3w→E4 is `e0_to_e4_ns − e0_to_e3w_ns`.",
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
        f"### After-ladder ({LADDER_LABEL}, `HEAD`)",
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
        base_rec = recorded(base_runs)
        after_rec = recorded(after_runs)
        e2e3g_after_fast = e2e3g_median(after_rec, after_phases, FAST)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "machine-spec.md").write_text(
            write_machine_spec(base_rec, after_rec, baseline_summary),
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
            write_edges(base_rec, base_phases, after_rec, after_phases),
            encoding="utf-8",
        )
        print(
            f"TEST PASS: exhibits from {BASELINE_TAG} + {AFTER_REV} → {OUT_DIR}"
        )
        print((OUT_DIR / "machine-spec.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "phase-decomposition.md").read_text(encoding="utf-8"))
        print((OUT_DIR / "edges.md").read_text(encoding="utf-8"))
        return 0
    except ExhibitFail as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
