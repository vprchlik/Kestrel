#!/usr/bin/env python3
"""Classify merge_config.sh output (D-0073 gate).

Linux 6.18 merge_config.sh diffs the concatenated stock+fragment
against the final .config. That is not "what the fragment asked."

  "Value of CONFIG_X is redefined by fragment …"
      The fragment changed stock's value. Informational.

  "Value of CONFIG_X is redundant by fragment …"
      Same value as stock (−r only). Informational.

  "Value requested for CONFIG_X not in final .config"
      Three cases, discriminated on whether X is a kconfig line in
      linux-trimmed.fragment:

      1. Fragment requested unset → final =y (or another value).
         Real survival. `# merge-override SYM:` or abort. (EFI)
      2. Fragment requested unset → final absent.
         Menu vanished. Success.
      3. Requested =y → final absent, X not in the fragment.
         Dependent drop from a parent we unset (SCSI_MOD after
         BLOCK, NFS_FS after NETWORK_FILESYSTEMS, …). Success,
         informational, no annotation.

      Requested =y → final absent, X *is* in the fragment: a keep
      we asked for is gone. Abort. The D-0062 keeps list is also
      asserted on the final .config as its own check — do not try
      to police every cascade.

Intent notes (`# FTRACE: …`) are not merge-overrides and do not
put a symbol "in the fragment."

Usage:
  python3 scripts/linux-merge-warnings.py FRAGMENT MERGE_LOG
  python3 scripts/linux-merge-warnings.py keeps FINAL_CONFIG
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
FRAG_Y_RE = re.compile(r"^CONFIG_([A-Z0-9_]+)=(.*)$")
FRAG_N_RE = re.compile(r"^# CONFIG_([A-Z0-9_]+) is not set$")
CFG_Y_RE = re.compile(r"^CONFIG_([A-Z0-9_]+)=(.*)$")
CFG_N_RE = re.compile(r"^# CONFIG_([A-Z0-9_]+) is not set$")

# D-0062 keeps: serial, virtio-mmio/net, IPv4 TCP, initramfs, DEVTMPFS,
# FUTEX. Policed on the final .config, not via merge_config cascades.
KEEP_Y = (
    "TTY",
    "SERIAL_8250",
    "SERIAL_8250_CONSOLE",
    "SERIAL_OF_PLATFORM",
    "PRINTK",
    "NETDEVICES",
    "VIRTIO_MENU",
    "VIRTIO_MMIO",
    "VIRTIO_NET",
    "NET",
    "INET",
    "BLK_DEV_INITRD",
    "BINFMT_ELF",
    "DEVTMPFS",
    "FUTEX",
)


class MergeWarnFail(Exception):
    pass


def bare(cfg: str) -> str:
    return cfg[len("CONFIG_") :] if cfg.startswith("CONFIG_") else cfg


def is_unset_line(val: str, cfg: str) -> bool:
    v = val.strip()
    return v == "" or v == f"# {cfg} is not set"


def fragment_symbols(frag: str) -> dict[str, str]:
    """Kconfig lines only. Intent notes do not count."""
    out: dict[str, str] = {}
    for raw in frag.splitlines():
        s = raw.strip()
        m = FRAG_Y_RE.match(s)
        if m:
            out[m.group(1)] = m.group(2).strip()
            continue
        m = FRAG_N_RE.match(s)
        if m:
            out[m.group(1)] = "unset"
    return out


def merge_overrides(frag: str) -> set[str]:
    found: set[str] = set()
    for raw in frag.splitlines():
        m = MERGE_OVERRIDE_RE.match(raw.strip())
        if m:
            found.add(m.group(1))
    return found


def parse_final_config(text: str) -> dict[str, str]:
    final: dict[str, str] = {}
    for line in text.splitlines():
        m = CFG_Y_RE.match(line)
        if m:
            final[m.group(1)] = m.group(2).strip()
            continue
        m = CFG_N_RE.match(line)
        if m:
            final[m.group(1)] = "unset"
    return final


def check_keeps(cfg_text: str) -> tuple[list[str], list[str]]:
    """Return (table_lines, fail_lines)."""
    final = parse_final_config(cfg_text)
    table = ["===== 3c. D-0062 keeps must be y ====="]
    fails: list[str] = []
    for sym in KEEP_Y:
        got = final.get(sym, "absent")
        ok = got == "y"
        status = "PASS" if ok else "FAIL"
        table.append(f"CONFIG_{sym}: final {got}  {status}")
        if not ok:
            fails.append(
                f"TEST FAIL: keep CONFIG_{sym} is {got}, want y"
            )
    return table, fails


def classify(
    frag: str, log: str
) -> tuple[list[str], list[str], list[str]]:
    """Return (info_lines, fail_lines, dependent_drops)."""
    in_frag = fragment_symbols(frag)
    overrides = merge_overrides(frag)
    info: list[str] = []
    fails: list[str] = []
    drops: list[str] = []
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
            _record_not_final(
                cfg,
                requested,
                actual,
                in_frag,
                overrides,
                info,
                fails,
                drops,
            )
            i = j
            continue
        i += 1
    if drops:
        info.append(
            f"dependent drop (informational): {len(drops)} symbols "
            f"not in linux-trimmed.fragment"
        )
    return info, fails, drops


def _record_not_final(
    cfg: str,
    requested: str,
    actual: str,
    in_frag: dict[str, str],
    overrides: set[str],
    info: list[str],
    fails: list[str],
    drops: list[str],
) -> None:
    req, act = requested.strip(), actual.strip()
    sym = bare(cfg)
    asked = sym in in_frag
    want = "unset" if is_unset_line(req, cfg) else req
    got = act if act else "absent"

    # Case 2: fragment (or merged file) requested unset, symbol gone.
    if is_unset_line(req, cfg) and is_unset_line(act, cfg):
        info.append(f"not in final after unset (menu vanished): {cfg}")
        return

    # Case 1: requested unset, symbol survived as a value.
    if is_unset_line(req, cfg) and not is_unset_line(act, cfg):
        if asked:
            if sym in overrides:
                info.append(
                    f"annotated merge-override: {cfg} requested {want}, "
                    f"final {got}"
                )
            else:
                fails.append(
                    f"TEST FAIL: merge did not stick: {cfg} requested "
                    f"{want}, final {got} (unannotated; need "
                    f"# merge-override {sym}: <why>)"
                )
            return
        # Stock had it unset; we never asked. Not a fragment survival.
        info.append(f"unsolicited final value (not in fragment): {cfg}")
        return

    # Requested a value (typically =y), final absent / unset.
    if not is_unset_line(req, cfg) and is_unset_line(act, cfg):
        if not asked:
            # Case 3: stock =y, parent unset dropped it. We never asked.
            drops.append(cfg)
            return
        # We asked for =y and it is gone — lost keep.
        if sym in overrides:
            info.append(
                f"annotated merge-override: {cfg} requested {want}, "
                f"final {got}"
            )
        else:
            fails.append(
                f"TEST FAIL: merge did not stick: {cfg} requested "
                f"{want}, final {got} (unannotated; need "
                f"# merge-override {sym}: <why>)"
            )
        return

    # Other mismatch (y vs m, …).
    if asked:
        if sym in overrides:
            info.append(
                f"annotated merge-override: {cfg} requested {want}, "
                f"final {got}"
            )
        else:
            fails.append(
                f"TEST FAIL: merge did not stick: {cfg} requested "
                f"{want}, final {got} (unannotated; need "
                f"# merge-override {sym}: <why>)"
            )
        return
    drops.append(cfg)


def check_merge_log(frag_path: Path, log_path: Path) -> int:
    info, fails, _drops = classify(
        frag_path.read_text(), log_path.read_text()
    )
    for line in info:
        print(f"linux-build: {line}", file=sys.stderr)
    for line in fails:
        print(line, file=sys.stderr)
    return 1 if fails else 0


def check_keeps_file(cfg_path: Path) -> int:
    table, fails = check_keeps(cfg_path.read_text())
    print("\n".join(table))
    for line in fails:
        print(line, file=sys.stderr)
    return 1 if fails else 0


def selftest() -> None:
    # Redefined is still informational (RTC_CLASS false positive).
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
    )
    info, fails, drops = classify(frag_rtc, log_redefined)
    if fails or drops:
        raise MergeWarnFail(
            f"TEST FAIL: redefined must not abort; got fails={fails} "
            f"drops={drops}"
        )
    if not any("CONFIG_RTC_CLASS" in s for s in info):
        raise MergeWarnFail("TEST FAIL: RTC_CLASS redefined not logged")

    # Case 1: fragment unset → final y, no merge-override. Abort.
    frag_ftrace = "# FTRACE: missed trim\n# CONFIG_FTRACE is not set\n"
    log_survived = (
        "Value requested for CONFIG_FTRACE not in final .config\n"
        "Requested value: # CONFIG_FTRACE is not set\n"
        "Actual value: CONFIG_FTRACE=y\n"
        "\n"
    )
    info, fails, drops = classify(frag_ftrace, log_survived)
    if not fails:
        raise MergeWarnFail(
            "TEST FAIL: case 1 FTRACE survival must abort"
        )
    if drops:
        raise MergeWarnFail("TEST FAIL: case 1 must not be a dependent drop")

    # Case 1 annotated (EFI).
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
    info, fails, drops = classify(frag_efi, log_efi)
    if fails or drops:
        raise MergeWarnFail(f"TEST FAIL: annotated EFI must pass; {fails}")
    if not any("annotated merge-override" in s and "EFI" in s for s in info):
        raise MergeWarnFail("TEST FAIL: annotated EFI not logged")

    # Case 2: fragment unset → final absent. Success.
    frag_swap = "# CONFIG_SWAP is not set\n"
    log_swap = (
        "Value requested for CONFIG_SWAP not in final .config\n"
        "Requested value: # CONFIG_SWAP is not set\n"
        "Actual value: \n"
        "\n"
    )
    info, fails, drops = classify(frag_swap, log_swap)
    if fails or drops:
        raise MergeWarnFail(
            f"TEST FAIL: case 2 vanished-after-unset must pass; "
            f"fails={fails} drops={drops}"
        )

    # Case 3: stock =y, not in fragment, final absent. Dependent drop.
    frag_parents = (
        "# CONFIG_BLOCK is not set\n"
        "# CONFIG_NETWORK_FILESYSTEMS is not set\n"
        "# CONFIG_USB_SUPPORT is not set\n"
        "# CONFIG_SOUND is not set\n"
        "# CONFIG_RTC_CLASS is not set\n"
        "CONFIG_SERIAL_OF_PLATFORM=y\n"
    )
    log_case3 = (
        "Value requested for CONFIG_SCSI_MOD not in final .config\n"
        "Requested value: CONFIG_SCSI_MOD=y\n"
        "Actual value: \n"
        "\n"
        "Value requested for CONFIG_NFS_FS not in final .config\n"
        "Requested value: CONFIG_NFS_FS=y\n"
        "Actual value: \n"
        "\n"
        "Value requested for CONFIG_USB_XHCI_HCD not in final .config\n"
        "Requested value: CONFIG_USB_XHCI_HCD=y\n"
        "Actual value: \n"
        "\n"
        "Value requested for CONFIG_SND_PCM not in final .config\n"
        "Requested value: CONFIG_SND_PCM=y\n"
        "Actual value: \n"
        "\n"
        "Value requested for CONFIG_RTC_LIB not in final .config\n"
        "Requested value: CONFIG_RTC_LIB=y\n"
        "Actual value: \n"
        "\n"
        "Value requested for CONFIG_SECURITY_SELINUX not in final .config\n"
        "Requested value: CONFIG_SECURITY_SELINUX=y\n"
        "Actual value: \n"
        "\n"
    )
    info, fails, drops = classify(frag_parents, log_case3)
    if fails:
        raise MergeWarnFail(
            f"TEST FAIL: case 3 dependent drops must not abort; {fails}"
        )
    want_drops = {
        "CONFIG_SCSI_MOD",
        "CONFIG_NFS_FS",
        "CONFIG_USB_XHCI_HCD",
        "CONFIG_SND_PCM",
        "CONFIG_RTC_LIB",
        "CONFIG_SECURITY_SELINUX",
    }
    if set(drops) != want_drops:
        raise MergeWarnFail(
            f"TEST FAIL: case 3 drops {drops}, want {sorted(want_drops)}"
        )
    if not any("dependent drop" in s and "6 symbols" in s for s in info):
        raise MergeWarnFail(
            f"TEST FAIL: case 3 summary missing; info={info}"
        )

    # An intent note naming SCSI_MOD does not put it in the fragment.
    frag_note_only = "# SCSI_MOD: leftover\n# CONFIG_BLOCK is not set\n"
    log_scsi = (
        "Value requested for CONFIG_SCSI_MOD not in final .config\n"
        "Requested value: CONFIG_SCSI_MOD=y\n"
        "Actual value: \n"
        "\n"
    )
    info, fails, drops = classify(frag_note_only, log_scsi)
    if fails or drops != ["CONFIG_SCSI_MOD"]:
        raise MergeWarnFail(
            f"TEST FAIL: intent note must not make SCSI_MOD a keep; "
            f"fails={fails} drops={drops}"
        )

    # Requested =y in the fragment, final absent: lost keep, not case 3.
    frag_keep = "CONFIG_SERIAL_OF_PLATFORM=y\n"
    log_gone = (
        "Value requested for CONFIG_SERIAL_OF_PLATFORM not in final .config\n"
        "Requested value: CONFIG_SERIAL_OF_PLATFORM=y\n"
        "Actual value: \n"
        "\n"
    )
    info, fails, drops = classify(frag_keep, log_gone)
    if not fails:
        raise MergeWarnFail("TEST FAIL: lost keep in fragment must abort")
    if drops:
        raise MergeWarnFail("TEST FAIL: lost keep must not be a dependent drop")

    # Mixed log: case 1 + case 3 together. Abort only on case 1.
    frag_mixed = (
        "# CONFIG_FTRACE is not set\n"
        "# CONFIG_BLOCK is not set\n"
    )
    log_mixed = log_survived + log_scsi
    info, fails, drops = classify(frag_mixed, log_mixed)
    if not any("FTRACE" in s for s in fails):
        raise MergeWarnFail("TEST FAIL: mixed log must still catch FTRACE")
    if "CONFIG_SCSI_MOD" not in drops:
        raise MergeWarnFail("TEST FAIL: mixed log must still drop SCSI_MOD")

    # Keeps check: all y passes; a missing keep fails.
    cfg_ok = "\n".join(f"CONFIG_{s}=y" for s in KEEP_Y) + "\n"
    table, kfails = check_keeps(cfg_ok)
    if kfails:
        raise MergeWarnFail(f"TEST FAIL: complete keeps must pass; {kfails}")
    if not any("3c." in s for s in table):
        raise MergeWarnFail("TEST FAIL: keeps table missing banner")
    cfg_bad = cfg_ok.replace("CONFIG_VIRTIO_NET=y\n", "")
    table, kfails = check_keeps(cfg_bad)
    if not any("VIRTIO_NET" in s for s in kfails):
        raise MergeWarnFail("TEST FAIL: missing VIRTIO_NET must fail keeps")

    print("TEST PASS: linux-merge-warnings selftest")


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "selftest":
        try:
            selftest()
        except MergeWarnFail as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "keeps":
        return check_keeps_file(Path(sys.argv[2]))
    if len(sys.argv) == 3:
        return check_merge_log(Path(sys.argv[1]), Path(sys.argv[2]))
    print(
        "usage: linux-merge-warnings.py FRAGMENT MERGE_LOG\n"
        "       linux-merge-warnings.py keeps FINAL_CONFIG\n"
        "       linux-merge-warnings.py selftest",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
