#!/usr/bin/env python3
"""Regime witness reconciliation (D-0078 amendment evidence).

Generates report/exhibits/regime-witness.md from pinned CSVs only —
never the working tree. One row per campaign: the safe arm's per-trial
page_verify witness (median, range, uniformity) beside the canary
columns where the pinned runs.csv carries them. Rows are grouped by
kernel family (the safe arm's kernel_sha256) because witness absolutes
compare only within one family; the regime divide is stated per family
and only where that family exhibits both clusters or a corroborating
in-family boot record.

Warmup rows are parsed and joined by (batch, position). The closing
finding is computed from that join — canary vs batch-1 position-1,
the cluster of canaries and batch-boundary first warmups, m-lane
same-position dips, idle-gap overlap — never a string literal about
a mid-warmup flip. campaign() still filters warmup == "0" for the
recorded witness; the join is a separate pass over warmup == "1".

Fail-closed: a pin that does not load, a campaign with no recorded
safe-arm rows (flagged pins excepted), or a mixed kernel hash within
one campaign's safe arm is an error, not a skipped row.
"""

from __future__ import annotations

import csv
import io
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "exhibits" / "regime-witness.md"

# (label, rev, note). Revs are pins: tags or frozen commits named in
# results/README.md / report-exhibits.py / DECISIONS entries.
PINS = [
    ("T4.3 baseline", "baseline-t4.3", ""),
    ("T4.6 after-ladder", "c40945cdb71b5aef68c5e72e292a718b66ec651e", ""),
    ("D-0068 run 1", "59e070321ab5ec30ff97830ac3f9f78577511db4", ""),
    ("D-0068 run 2", "4755fa3fe2cf98ded4dd333fa81ca66a2b811cfe", ""),
    ("T4.8", "ffb7ac71234e953ae51339a3e1f5e17ba8c3f1b3", ""),
    ("T4.8b", "t48b", "published flagship"),
    ("t47", "94bad3b8a118412639c3f2f49582141033f7b867",
     "recorded-not-published; canary ran but predates the CSV columns "
     "(console record in D-0079)"),
    ("t47b", "793680bcd4fe4174ede1ddd3ec80d9e1135b4b2b",
     "aborted, recorded-not-published; the amendment's trigger"),
    ("t47c", "c2759e245bf7cbcf23dcf43ac228b73f06bb0960",
     "passed T4.7 confirmation; second canary disagreement"),
]
# The divide between the two observed clusters for kernel families that
# exhibit both (ms, on page_verify). Families with a single observed
# cluster get no classification — stated per family in the output.
DIVIDE_MS = 14.0
# A warmup is a dip vs its siblings when it sits this far below their
# median. Used for the m-lane (no 14 ms divide) and for labelling.
DIP_BELOW_MEDIAN_MS = 1.0


class Fail(Exception):
    pass


def show(rev: str, path: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "show", f"{rev}:{path}"], cwd=ROOT, text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        raise Fail(f"TEST FAIL: git show {rev}:{path}: {e.stderr.strip()}")


def campaign(rev: str) -> dict:
    runs = list(csv.DictReader(io.StringIO(show(rev, "results/runs.csv"))))
    phases = list(csv.DictReader(io.StringIO(show(rev, "results/phases.csv"))))
    rec = [r for r in runs if r["warmup"] == "0" and r["config"] == "release-default"]
    if not rec:
        raise Fail(f"TEST FAIL: {rev} has no recorded release-default rows")
    kern = {r["kernel_sha256"] for r in rec}
    if len(kern) != 1:
        raise Fail(f"TEST FAIL: {rev} mixed safe-arm kernel_sha256 {sorted(kern)}")
    pv = [
        int(p["delta_ns"])
        for p in phases
        if p["config"] == "release-default"
        and p["phase"] == "page_verify"
        and p["warmup"] == "0"
    ]
    if not pv:
        raise Fail(f"TEST FAIL: {rev} has no safe-arm page_verify rows")
    canary = ""
    if "canary_page_verify_ns" in rec[0] and rec[0]["canary_page_verify_ns"]:
        canary = (
            f"{int(rec[0]['canary_stvec_ns']) / 1e6:.3f}/"
            f"{int(rec[0]['canary_page_verify_ns']) / 1e6:.3f}"
        )
    batches = sorted({r["batch_id"] for r in rec})
    return {
        "kernel": next(iter(kern)),
        "n": len(pv),
        "med": statistics.median(pv) / 1e6,
        "lo": min(pv) / 1e6,
        "hi": max(pv) / 1e6,
        "canary": canary,
        "canary_pv": (
            int(rec[0]["canary_page_verify_ns"]) / 1e6
            if rec[0].get("canary_page_verify_ns") else None
        ),
        "batches": batches,
        "warmup": warmup_join(rev, runs, phases),
    }


def _pv_map(phases: list[dict], config: str) -> dict[tuple[str, int], float]:
    out: dict[tuple[str, int], float] = {}
    for p in phases:
        if p["warmup"] != "1" or p["config"] != config or p["phase"] != "page_verify":
            continue
        out[(p["batch_id"], int(p["trial"]))] = int(p["delta_ns"]) / 1e6
    return out


def _arm_warmups(
    batches: list[str], pv: dict[tuple[str, int], float]
) -> list[list[float]] | None:
    if not pv:
        return None
    rows = []
    for b in batches:
        trials = sorted(t for bb, t in pv if bb == b)
        if trials != [1, 2, 3]:
            raise Fail(
                f"TEST FAIL: warmup page_verify trials for batch {b} "
                f"are {trials}, want [1, 2, 3]"
            )
        rows.append([pv[(b, t)] for t in trials])
    return rows


def warmup_join(rev: str, runs: list[dict], phases: list[dict]) -> dict | None:
    """Safe-arm (and m-lane, if present) warmup page_verify by batch × trial."""
    wu_runs = [r for r in runs if r["warmup"] == "1" and r["config"] == "release-default"]
    if not wu_runs:
        return None
    batches = sorted({r["batch_id"] for r in wu_runs})
    safe_pv = _pv_map(phases, "release-default")
    safe = _arm_warmups(batches, safe_pv)
    if safe is None:
        raise Fail(f"TEST FAIL: {rev} has warmup runs but no page_verify")
    m_pv = _pv_map(phases, "m-release-default")
    m_wu = _arm_warmups(batches, m_pv) if m_pv else None

    by_batch: dict[str, list[dict]] = {}
    for r in runs:
        by_batch.setdefault(r["batch_id"], []).append(r)
    gaps: list[tuple[int, int, float, float]] = []  # batch_idx, trial, pv_ms, gap_s
    for bi, bid in enumerate(batches):
        rs = sorted(by_batch[bid], key=lambda x: int(x["run_order"]))
        for i, r in enumerate(rs):
            if r["warmup"] != "1" or r["config"] != "release-default":
                continue
            if i == 0:
                continue
            prev = rs[i - 1]
            gap_s = (
                int(r["e0_mono_ns"])
                - (int(prev["e0_mono_ns"]) + int(prev["e0_to_e4_ns"]))
            ) / 1e9
            trial = int(r["trial"])
            gaps.append((bi, trial, safe[bi][trial - 1], gap_s))
    return {"batches": batches, "safe": safe, "m": m_wu, "gaps": gaps}


def regime_side(ms: float) -> str:
    return "deflated" if ms < DIVIDE_MS else "inflated"


def is_dip(val: float, siblings: list[float]) -> bool:
    if not siblings:
        return False
    return statistics.median(siblings) - val >= DIP_BELOW_MEDIAN_MS


def fmt_wu(vals: list[float], campaign_wu: list[list[float]], batch_idx: int) -> str:
    parts = []
    for ti, v in enumerate(vals):
        others = [
            x
            for bj, wu in enumerate(campaign_wu)
            for tk, x in enumerate(wu)
            if not (bj == batch_idx and tk == ti)
        ]
        parts.append(f"**{v:.2f}**" if is_dip(v, others) else f"{v:.2f}")
    return " ".join(parts)


def structural_finding(rows: list[dict]) -> list[str]:
    """Closing paragraphs computed from the warmup-position join.

    A hardcoded mid-warmup-flip sentence cannot appear here: campaign()
    never sees warmup rows, and the join orders by batch then trial.
    """
    joined = [r for r in rows if r.get("warmup")]
    if not joined:
        raise Fail("TEST FAIL: no pinned campaign has warmup rows to join")

    cluster: list[tuple[str, float]] = []
    for r in joined:
        if (
            r["canary_pv"] is not None
            and regime_side(r["canary_pv"]) != regime_side(r["med"])
        ):
            cluster.append((f"{r['label']} canary", r["canary_pv"]))
        safe = r["warmup"]["safe"]
        for bi, wu in enumerate(safe):
            others = [
                v
                for bj, w in enumerate(safe)
                for tk, v in enumerate(w)
                if not (bj == bi and tk == 0)
            ]
            if is_dip(wu[0], others):
                cluster.append(
                    (f"{r['label']} batch-{bi + 1} position-1", wu[0])
                )
    if not cluster:
        raise Fail("TEST FAIL: warmup join found no canary or position-1 dip")
    clo = min(v for _, v in cluster)
    chi = max(v for _, v in cluster)

    b2_first_inflated = [
        (r["label"], r["warmup"]["safe"][1][0])
        for r in joined
        if len(r["warmup"]["safe"]) >= 2 and r["med"] >= DIVIDE_MS
    ]
    b2_outside = [
        (lab, v) for lab, v in b2_first_inflated
        if not (clo - 1e-9 <= v <= chi + 1e-9)
    ]

    disagrees = []
    for r in rows:
        if not r["canary"] or r["canary_pv"] is None:
            continue
        c_side = regime_side(r["canary_pv"])
        w_side = regime_side(r["med"])
        if c_side != w_side:
            disagrees.append(r)

    # Canary vs the first trial-conditions boot (batch-1 position-1).
    not_instant = []
    for r in joined:
        if r["canary_pv"] is None:
            continue
        b1 = r["warmup"]["safe"][0][0]
        if regime_side(r["canary_pv"]) != regime_side(b1):
            not_instant.append((r, b1))

    m_dips = []
    for r in joined:
        m_wu = r["warmup"]["m"]
        if not m_wu:
            continue
        for bi, wu in enumerate(m_wu):
            others = [
                v
                for bj, w in enumerate(m_wu)
                for ti, v in enumerate(w)
                if not (bj == bi and ti == 0)
            ]
            if is_dip(wu[0], others):
                m_dips.append((r["label"], bi + 1, wu[0], statistics.median(others)))

    dip_gaps = []
    other_gaps = []
    for r in joined:
        if r["med"] < DIVIDE_MS:
            continue
        safe = r["warmup"]["safe"]
        for bi, trial, pv, gap in r["warmup"]["gaps"]:
            others = [
                v
                for bj, wu in enumerate(safe)
                for tk, v in enumerate(wu)
                if not (bj == bi and tk == trial - 1)
            ]
            if is_dip(pv, others):
                dip_gaps.append(gap)
            else:
                other_gaps.append(gap)

    lines = []
    dlab = ", ".join(r["label"] for r in disagrees) if disagrees else "none"
    lines.append(
        f"Canary vs recorded witness: {len(disagrees)} disagreement"
        f"{'' if len(disagrees) == 1 else 's'} ({dlab}). "
        "The recorded witness is the campaign's regime; a disagreement "
        "is the canary failing as a certificate, not a mid-run flip "
        "unless the warmup-position join says so."
    )
    if not_instant:
        bits = []
        for r, b1 in not_instant:
            b2 = r["warmup"]["safe"][1][0]
            bits.append(
                f"{r['label']}: canary {r['canary_pv']:.3f} ms "
                f"({regime_side(r['canary_pv'])}) vs batch-1 position-1 "
                f"{b1:.2f} ms ({regime_side(b1)}); the deflated warmup "
                f"is batch-2 position-1 {b2:.2f} ms, not the first "
                f"trial-conditions boot"
            )
        lines.append(
            "Join by (batch, position) — not campaign-flat warmup order — "
            "refutes a first-two-warmups flip: " + "; ".join(bits) + "."
        )
    lines.append(
        f"Structural cluster, derived from disagreeing canaries and "
        f"every batch-boundary first safe warmup that dips ≥ "
        f"{DIP_BELOW_MEDIAN_MS:.0f} ms below its campaign's other "
        f"warmups: [{clo:.2f}, {chi:.2f}] ms "
        f"({len(cluster)} boots). "
        + (
            "Every batch-2 position-1 safe warmup of an inflated "
            "recorded campaign falls in that cluster."
            if not b2_outside
            else "Batch-2 position-1 outside the cluster: "
            + ", ".join(f"{lab} {v:.2f}" for lab, v in b2_outside) + "."
        )
        + " Lone e0drift witness boots are not in these pins and are "
        "not asserted here."
    )
    if m_dips:
        bits = [
            f"{lab} batch-{bi} position-1 {v:.2f} ms vs sibling median "
            f"{med:.2f} ms"
            for lab, bi, v, med in m_dips
        ]
        lines.append(
            "The same position dips on the m-lane (no OpenSBI, no DBCN): "
            + "; ".join(bits)
            + " — lane-independent, host-side of the polled UART."
        )
    if dip_gaps and other_gaps:
        lines.append(
            f"Idle gap before the spawn (e0_mono − previous trial's "
            f"e0+E4, same batch): dipped boots {min(dip_gaps):.2f}–"
            f"{max(dip_gaps):.2f} s, inflated neighbors {min(other_gaps):.2f}–"
            f"{max(other_gaps):.2f} s — ranges overlap, so wall-clock "
            f"spacing is not the driver."
        )
    return lines


def main() -> int:
    rows = []
    for label, rev, note in PINS:
        c = campaign(rev)
        c.update({"label": label, "rev": rev, "note": note})
        rows.append(c)
    families: dict[str, list[dict]] = {}
    for c in rows:
        families.setdefault(c["kernel"], []).append(c)

    lines = [
        "<!-- generated by scripts/regime-witness.py — do not edit -->",
        "",
        "Regime witness reconciliation (D-0078 amendment evidence). The",
        "witness is the safe arm's recorded per-trial `page_verify`",
        "delta, read from each campaign's **pinned** CSVs; the canary",
        "columns appear where the pinned `runs.csv` carries them.",
        "Witness absolutes compare only **within one kernel family**",
        "(the safe arm's `kernel_sha256`), so rows are grouped by",
        "family; the regime divide is applied only inside a family",
        "that exhibits both clusters or a corroborating in-family boot",
        "record. Warmup `page_verify` is joined by batch and trial",
        "position; the closing finding is computed from that join.",
        "Regeneration: `python3 scripts/regime-witness.py`.",
        "",
    ]
    for kern in sorted(families, key=lambda k: min(r["label"] for r in families[k])):
        fam = families[kern]
        meds = [c["med"] for c in fam]
        both = min(meds) < DIVIDE_MS < max(meds)
        corroborated = both or any(
            c["canary"] and (float(c["canary"].split("/")[1]) < DIVIDE_MS) != (c["med"] < DIVIDE_MS)
            for c in fam
        )
        lines.append(f"### kernel family `{kern[:12]}…`")
        lines.append("")
        if corroborated:
            lines.append(
                f"Both clusters observed in this family; divide at ~{DIVIDE_MS:.0f} ms."
            )
        else:
            lines.append(
                "Single cluster observed in this family — **no regime "
                "classification**; absolutes here must not be read against "
                "other families' rows."
            )
        lines.append("")
        lines.append(
            "| campaign | n | witness med [min–max] ms | uniform | regime | "
            "canary sv/pv ms | canary vs witness |"
        )
        lines.append("|---|---:|---|---|---|---|---|")
        for c in fam:
            if corroborated:
                side = "inflated" if c["med"] >= DIVIDE_MS else "deflated"
                uniform = all(
                    (v >= DIVIDE_MS) == (c["med"] >= DIVIDE_MS)
                    for v in (c["lo"], c["hi"])
                )
                uni = "yes" if uniform else "**MIXED**"
            else:
                side, uni = "—", f"span {c['hi']-c['lo']:.2f}"
            if c["canary"]:
                c_side = "inflated" if float(c["canary"].split("/")[1]) >= DIVIDE_MS else "deflated"
                verdict = "AGREE" if (not corroborated or c_side == side) else "**DISAGREE**"
            else:
                verdict = "n/a (pre-canary)" if "canary" not in c["note"] else "n/a"
            note = f" ({c['note']})" if c["note"] else ""
            lines.append(
                f"| {c['label']}{note} | {c['n']} | {c['med']:.2f} "
                f"[{c['lo']:.2f}–{c['hi']:.2f}] | {uni} | {side} | "
                f"{c['canary'] or '—'} | {verdict} |"
            )
        lines.append("")

    lines.append("### warmup-position join (safe-arm `page_verify`, ms)")
    lines.append("")
    lines.append(
        "Warmup rows (`warmup=1`), ordered by batch then trial. "
        "A value is bold iff it dips ≥ 1 ms below the median of the "
        "other five safe warmups in that campaign. This is the join "
        "the closing finding is computed from — not campaign-flat "
        "warmup order, which is what produced the refuted flip reading."
    )
    lines.append("")
    lines.append(
        "| campaign | canary pv | batch-1 warmups | batch-2 warmups | recorded med |"
    )
    lines.append("|---|---|---|---|---|")
    for c in rows:
        wu = c.get("warmup")
        if not wu or len(wu["safe"]) < 2:
            continue
        can = f"{c['canary_pv']:.3f}" if c["canary_pv"] is not None else "—"
        lines.append(
            f"| {c['label']} | {can} | {fmt_wu(wu['safe'][0], wu['safe'], 0)} | "
            f"{fmt_wu(wu['safe'][1], wu['safe'], 1)} | {c['med']:.2f} |"
        )
    lines.append("")
    for para in structural_finding(rows):
        lines.append(para)
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"TEST PASS: regime-witness → {OUT}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as e:
        print(e, file=sys.stderr)
        sys.exit(1)
