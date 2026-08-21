#!/usr/bin/env python3
"""Resolve initcall_debug addresses against System.map (D-0072).

Diagnostic labeling pass for the 327 ms printk hole. Not a campaign
arm. Durations on an ignore_loglevel boot are UART-inflated labels;
they do not replace the T4.8 327 ms cell.

Usage:
  python3 scripts/label-linux-initcalls.py SERIAL System.map
  python3 scripts/label-linux-initcalls.py selftest
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRINTK_RE = re.compile(
    r"^\[\s*(\d+)\.(\d+)\]\s(.*)$",
)
INITCALL_RET_RE = re.compile(
    r"\binitcall\s+(?:0x)?([0-9a-fA-F]+)\s+returned\s+(-?\d+)\s+after\s+(\d+)\s+usecs",
)
HOLE_START_SUB = "Key type dns_resolver registered"
HOLE_END_SUB = "clk: Disabling unused clocks"
MAX_OFFSET = 64 * 1024


class LabelFail(Exception):
    pass


def printk_ns(sec: str, frac: str) -> int:
    return int(sec) * 1_000_000_000 + int(frac.ljust(9, "0")[:9])


def load_system_map(text: str) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 3:
            continue
        try:
            addr = int(parts[0], 16)
        except ValueError:
            continue
        entries.append((addr, parts[2]))
    if not entries:
        raise LabelFail("TEST FAIL: System.map has no symbol rows")
    entries.sort()
    return entries


def resolve(addr: int, entries: list[tuple[int, str]]) -> tuple[str, int]:
    lo, hi = 0, len(entries) - 1
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if entries[mid][0] <= addr:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best < 0:
        raise LabelFail(
            f"TEST FAIL: initcall 0x{addr:x} is below System.map "
            f"(first 0x{entries[0][0]:x})"
        )
    base, name = entries[best]
    off = addr - base
    if off >= MAX_OFFSET:
        raise LabelFail(
            f"TEST FAIL: initcall 0x{addr:x} resolved to {name}+0x{off:x} "
            f"(offset ≥ 64 KiB; wrong System.map for this Image)"
        )
    return name, off


def parse_serial(text: str) -> tuple[list[dict], int | None, int | None]:
    """Return (initcalls, hole_start_ns, hole_end_ns)."""
    rows: list[dict] = []
    hole_start: int | None = None
    hole_end: int | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = PRINTK_RE.match(line)
        if not m:
            continue
        ts = printk_ns(m.group(1), m.group(2))
        msg = m.group(3)
        if HOLE_START_SUB in msg:
            hole_start = ts
        if HOLE_END_SUB in msg:
            hole_end = ts
        ret = INITCALL_RET_RE.search(msg)
        if not ret:
            continue
        rows.append(
            {
                "addr": int(ret.group(1), 16),
                "ret": int(ret.group(2)),
                "usecs": int(ret.group(3)),
                "ns": ts,
                "line": msg,
            }
        )
    return rows, hole_start, hole_end


def label(
    serial_text: str, map_text: str
) -> tuple[list[dict], list[dict]]:
    entries = load_system_map(map_text)
    rows, hole_start, hole_end = parse_serial(serial_text)
    if not rows:
        raise LabelFail(
            "TEST FAIL: zero initcall-returned lines "
            "(ignore_loglevel did not take, or this is the T4.8 loglevel=7 log)"
        )
    if hole_start is None or hole_end is None:
        raise LabelFail(
            "TEST FAIL: diagnostic serial missing dns_resolver / "
            "clk-disabling-unused-clocks window"
        )
    if hole_end <= hole_start:
        raise LabelFail("TEST FAIL: hole window is empty or inverted")
    labeled: list[dict] = []
    for r in rows:
        name, off = resolve(r["addr"], entries)
        rec = dict(r)
        rec["name"] = name
        rec["offset"] = off
        rec["symbol"] = f"{name}+0x{off:x}" if off else name
        rec["in_hole"] = hole_start < r["ns"] <= hole_end
        labeled.append(rec)
    labeled.sort(key=lambda r: r["usecs"], reverse=True)
    hole = [r for r in labeled if r["in_hole"]]
    if not hole:
        raise LabelFail(
            "TEST FAIL: no initcall-returned lines inside the "
            "dns_resolver → clk-disable window"
        )
    return hole, labeled


def fmt_table(rows: list[dict]) -> list[str]:
    lines = [
        "| rank | usecs | symbol | ret | printk_s |",
        "|---|---:|---|---:|---:|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['usecs']} | `{r['symbol']}` | {r['ret']} | "
            f"{r['ns'] / 1e9:.6f} |"
        )
    return lines


def selftest() -> None:
    # Exact match + offset. Addresses chosen so 0x1000 is start of foo,
    # 0x1010 is foo+0x10, 0x2000 is bar.
    smap = (
        "0000000000001000 T foo\n"
        "0000000000002000 T bar\n"
        "0000000000003000 T baz\n"
    )
    serial = "\n".join(
        [
            "[    0.100000] Key type dns_resolver registered",
            "[    0.200000] initcall 0x1000 returned 0 after 5000 usecs",
            "[    0.250000] initcall 1010 returned 0 after 20000 usecs",
            "[    0.300000] clk: Disabling unused clocks",
            "[    0.400000] initcall 0x2000 returned 0 after 90000 usecs",
            "",
        ]
    )
    hole, all_rows = label(serial, smap)
    if [r["symbol"] for r in hole] != ["foo+0x10", "foo"]:
        raise LabelFail(f"TEST FAIL: hole symbols { [r['symbol'] for r in hole] }")
    if hole[0]["usecs"] != 20000:
        raise LabelFail("TEST FAIL: hole not sorted by usecs")
    if not any(r["symbol"] == "bar" and not r["in_hole"] for r in all_rows):
        raise LabelFail("TEST FAIL: bar should be outside the hole")
    try:
        label(serial.replace("initcall", "xinitcall"), smap)
    except LabelFail as e:
        if "zero initcall-returned" not in str(e):
            raise LabelFail(f"TEST FAIL: wrong zero-line error: {e}") from e
    else:
        raise LabelFail("TEST FAIL: expected zero-line failure")
    far = serial.replace("0x1000", "0x20000")
    try:
        label(far, smap)
    except LabelFail as e:
        if "64 KiB" not in str(e):
            raise LabelFail(f"TEST FAIL: wrong offset error: {e}") from e
    else:
        raise LabelFail("TEST FAIL: expected 64 KiB offset failure")
    print("TEST PASS: label-linux-initcalls selftest")


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            selftest()
            return 0
        if len(argv) != 2:
            print(
                "usage: label-linux-initcalls.py SERIAL System.map",
                file=sys.stderr,
            )
            return 2
        serial_path, map_path = Path(argv[0]), Path(argv[1])
        if not serial_path.is_file():
            raise LabelFail(f"TEST FAIL: serial missing: {serial_path}")
        if not map_path.is_file():
            raise LabelFail(f"TEST FAIL: System.map missing: {map_path}")
        hole, all_rows = label(
            serial_path.read_text(encoding="utf-8", errors="replace"),
            map_path.read_text(encoding="utf-8", errors="replace"),
        )
        sys.stdout.write(
            "\n".join(
                [
                    "## Hole window (labels for gap 1), sorted by usecs",
                    "",
                    *fmt_table(hole),
                    "",
                    "## All initcalls, sorted by usecs (UART-inflated; not a report number)",
                    "",
                    *fmt_table(all_rows),
                    "",
                ]
            )
        )
        return 0
    except LabelFail as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
