#!/usr/bin/env python3
"""Classify merge_config.sh output (D-0073 gate split).

Linux 6.18 merge_config.sh prints two different things that used to
flow through one annotation check:

  "Value of CONFIG_X is redefined by fragment …"
      The fragment changed stock's value. Every real trim line does
      this. Informational; do not require annotation.

  "Value of CONFIG_X is redundant by fragment …"
      Same value as stock (−r only). Informational.

  "Value requested for CONFIG_X not in final .config"
      After alldefconfig, requested != actual. If the symbol
      *survived* (still y/m/a value), that is the silent-trim-failure
      case: require `# merge-override SYM:` or abort. If the request
      was unset and actual is empty, the menu vanished — that is a
      successful unset, not a survival.

Intent notes (`# FTRACE: …`) are not merge-overrides. They used to
satisfy the redefined check by accident, which is how a genuine
override gets rubber-stamped.

Usage:
  python3 scripts/linux-merge-warnings.py FRAGMENT MERGE_LOG
  python3 scripts/linux-merge-warnings.py selftest
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REDEFINED_RE = re.compile(
    r"^Value of (CONFIG_[A-Z0-9_]+) is redefined by fragment "
)
REDUNDANT_RE = re.compile(
    r"^Value of (CONFIG_[A-Z0-9_]+) is redundant by fragment "
)
NOT_FINAL_RE = re.compile(
    r"^Value requested for (CONFIG_[A-Z0-9_]+) not in final \.config"
)
REQUESTED_RE = re.compile(r"^Requested value:\s*(.*)$")
ACTUAL_RE = re.compile(r"^Actual value:\s*(.*)$")
MERGE_OVERRIDE_RE = re.compile(r"# merge-override (?:CONFIG_)?([A-Z0-9_]+)\b")


class MergeWarnFail(Exception):
    pass


def bare(cfg: str) -> str:
    return cfg[len("CONFIG_") :] if cfg.startswith("CONFIG_") else cfg


def is_unset_line(val: str, cfg: str) -> bool:
    v = val.strip()
    return v == f"# {cfg} is not set"


def merge_overrides(frag: str) -> set[str]:
    found: set[str] = set()
    for raw in frag.splitlines():
        m = MERGE_OVERRIDE_RE.match(raw.strip())
        if m:
            found.add(m.group(1))
    return found


def survival_needs_override(cfg: str, requested: str, actual: str) -> bool:
    """True iff kconfig refused the request and the symbol is still present."""
    req, act = requested.strip(), actual.strip()
    if req == act:
        return False
    # Requested unset, symbol absent from the final .config: the menu
    # vanished (parent off). Successful unset, not a re-enable.
    if is_unset_line(req, cfg) and act == "":
        return False
    return True


def classify(frag: str, log: str) -> tuple[list[str], list[str]]:
    """Return (info_lines, fail_lines). fail_lines non-empty → abort."""
    overrides = merge_overrides(frag)
    info: list[str] = []
    fails: list[str] = []
    lines = log.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = REDEFINED_RE.match(line)
        if m:
            info.append(f"redefined (informational): {m.group(1)}")
            i += 1
            continue
        m = REDUNDANT_RE.match(line)
        if m:
            info.append(f"redundant (informational): {m.group(1)}")
            i += 1
            continue
        m = NOT_FINAL_RE.match(line)
        if m:
            cfg = m.group(1)
            requested = ""
            actual = ""
            j = i + 1
            while j < len(lines) and j <= i + 4:
                rm = REQUESTED_RE.match(lines[j])
                if rm:
                    requested = rm.group(1)
                    j += 1
                    continue
                am = ACTUAL_RE.match(lines[j])
                if am:
                    actual = am.group(1)
                    j += 1
                    break
                if lines[j].strip() == "":
                    j += 1
                    break
                j += 1
            if survival_needs_override(cfg, requested, actual):
                want = "unset" if is_unset_line(requested, cfg) else requested
                got = actual.strip() or "absent"
                if bare(cfg) in overrides:
                    info.append(
                        f"annotated merge-override: {cfg} requested {want}, "
                        f"final {got}"
                    )
                else:
                    fails.append(
                        f"TEST FAIL: merge did not stick: {cfg} requested "
                        f"{want}, final {got} (unannotated; need "
                        f"# merge-override {bare(cfg)}: <why>)"
                    )
            else:
                info.append(
                    f"not in final after unset (menu vanished): {cfg}"
                )
            i = j
            continue
        i += 1
    return info, fails


def check_file(frag_path: Path, log_path: Path) -> int:
    frag = frag_path.read_text()
    log = log_path.read_text()
    info, fails = classify(frag, log)
    for line in info:
        print(f"linux-build: {line}", file=sys.stderr)
    for line in fails:
        print(line, file=sys.stderr)
    if fails:
        return 1
    return 0


def selftest() -> None:
    # The T4.8b false positive: RTC_CLASS redefined, note says "RTC:"
    # not "RTC_CLASS", no merge-override. Must PASS after the split.
    frag_rtc = (
        "# RTC: goldfish_rtc registered as rtc0\n"
        "# CONFIG_RTC_CLASS is not set\n"
        "# WATCHDOG: leftover\n"
        "# CONFIG_WATCHDOG is not set\n"
    )
    log_redefined = (
        "Value of CONFIG_RTC_CLASS is redefined by fragment /tmp/frag:\n"
        "Previous value: CONFIG_RTC_CLASS=y\n"
        "New value: # CONFIG_RTC_CLASS is not set\n"
        "\n"
        "Value of CONFIG_WATCHDOG is redefined by fragment /tmp/frag:\n"
        "Previous value: CONFIG_WATCHDOG=y\n"
        "New value: # CONFIG_WATCHDOG is not set\n"
        "\n"
        "Value of CONFIG_FTRACE is redefined by fragment /tmp/frag:\n"
        "Previous value: CONFIG_FTRACE=y\n"
        "New value: # CONFIG_FTRACE is not set\n"
    )
    info, fails = classify(frag_rtc, log_redefined)
    if fails:
        raise MergeWarnFail(
            f"TEST FAIL: redefined must not abort; got {fails}"
        )
    if not any("CONFIG_RTC_CLASS" in s for s in info):
        raise MergeWarnFail("TEST FAIL: RTC_CLASS redefined not logged")

    # Intent note must not rubber-stamp a genuine survival.
    frag_intent_only = "# FTRACE: missed trim\n# CONFIG_FTRACE is not set\n"
    log_survived = (
        "Value requested for CONFIG_FTRACE not in final .config\n"
        "Requested value: # CONFIG_FTRACE is not set\n"
        "Actual value: CONFIG_FTRACE=y\n"
        "\n"
    )
    info, fails = classify(frag_intent_only, log_survived)
    if not fails:
        raise MergeWarnFail(
            "TEST FAIL: FTRACE survival with only an intent note must abort"
        )

    # merge-override accepts a survival (EFI).
    frag_efi = (
        "# CONFIG_EFI is not set\n"
        "# merge-override EFI: PORTABLE select EFI\n"
    )
    log_efi = (
        "Value requested for CONFIG_EFI not in final .config\n"
        "Requested value: # CONFIG_EFI is not set\n"
        "Actual value: CONFIG_EFI=y\n"
        "\n"
    )
    info, fails = classify(frag_efi, log_efi)
    if fails:
        raise MergeWarnFail(f"TEST FAIL: annotated EFI must pass; got {fails}")
    if not any("annotated merge-override" in s and "EFI" in s for s in info):
        raise MergeWarnFail("TEST FAIL: annotated EFI not logged")

    # Unset whose symbol vanished from .config is success.
    frag_swap = "# CONFIG_SWAP is not set\n"
    log_swap = (
        "Value requested for CONFIG_SWAP not in final .config\n"
        "Requested value: # CONFIG_SWAP is not set\n"
        "Actual value: \n"
        "\n"
    )
    info, fails = classify(frag_swap, log_swap)
    if fails:
        raise MergeWarnFail(
            f"TEST FAIL: vanished-after-unset must pass; got {fails}"
        )

    # Requested y, gone from .config: did not stick.
    frag_keep = "CONFIG_SERIAL_OF_PLATFORM=y\n"
    log_gone = (
        "Value requested for CONFIG_SERIAL_OF_PLATFORM not in final .config\n"
        "Requested value: CONFIG_SERIAL_OF_PLATFORM=y\n"
        "Actual value: \n"
        "\n"
    )
    info, fails = classify(frag_keep, log_gone)
    if not fails:
        raise MergeWarnFail("TEST FAIL: lost keep must abort")

    print("TEST PASS: linux-merge-warnings selftest")


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "selftest":
        try:
            selftest()
        except MergeWarnFail as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if len(sys.argv) != 3:
        print(
            "usage: linux-merge-warnings.py FRAGMENT MERGE_LOG\n"
            "       linux-merge-warnings.py selftest",
            file=sys.stderr,
        )
        return 2
    return check_file(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    sys.exit(main())
