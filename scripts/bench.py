#!/usr/bin/env python3
"""M4 benchmark harness (D-0055 / T4.1 / D-0071 / T4.8).

Long/tidy CSV: one run row per trial, one phase row per trial × PHASE
line. T4.2 adding stamps is more rows, not more columns. Phase names are
parsed from serial — this file is not a fourth copy of the justfile list
(finding 26). New batches drop e0_to_e3w_ns and record pcap-internal
w_ns / d_ack_ns / d_fin_ns via the D-0070 extract.

T4.8: per-system QEMU argv and hang-watchdog; one uniform client recv
timeout; PHASE-presence gated on system; Linux boot gate + SYN-grid/RST.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# D-0070 extract (W / D_ack / D_fin on one pcap clock). Import the
# implementation; do not clone the filters (D-0071). After T4.8 the
# extract lives in pcap_http.py; d0070-pcap-pass.py re-exports it.
_D0070_SPEC = importlib.util.spec_from_file_location(
    "d0070_pcap_pass", ROOT / "scripts" / "d0070-pcap-pass.py"
)
if _D0070_SPEC is None or _D0070_SPEC.loader is None:
    raise RuntimeError("cannot import scripts/d0070-pcap-pass.py")
_D0070 = importlib.util.module_from_spec(_D0070_SPEC)
_D0070_SPEC.loader.exec_module(_D0070)

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from pcap_http import (  # noqa: E402
    ARP_FILTER,
    GUEST_ARP_REQ_FILTER,
    GUEST_TX_FILTER,
    PcapExtractError,
    SLIRP_MAC,
    SYN_IN_FILTER,
    _time_ns,
    _time_s,
    assert_no_rst,
    assert_syn_grid,
    tshark_table,
)

SAFE_CONFIG = "release-default"
FAST_CONFIG = "release-fast-boot"
CONTROL_TOL_NS = 1_000_000
WHIMBREL_DFIN_FAIL_NS = 10_000_000
HTTP_LEN_PIN = 92
NEW_RUN_METRICS = (
    "e0_to_first_connect_ns",
    "e0_to_e4_ns",
    "w_ns",
    "d_ack_ns",
    "d_fin_ns",
)
OLD_RUN_METRICS = (
    "e0_to_first_connect_ns",
    "e0_to_e3w_ns",
    "e0_to_e4_ns",
)
PHASE_HEADER_RE = re.compile(r"^PHASE ticks \(")
PHASE_UNSET_RE = re.compile(r"^PHASE (\S+) unset\s*$")
PHASE_ROW_RE = re.compile(
    r"^PHASE (\S+) ticks=(\d+) ns=(\d+) since_start=(\d+) ns=(\d+) "
    r"delta=(\d+) ns=(\d+)\s*$"
)
RUNS_FIELDS = [
    "batch_id",
    "trial",
    "warmup",
    "system",
    "config",
    "git_sha",
    "dirty",
    "kernel_sha256",
    "bios_sha256",
    "qemu_version",
    "qemu_hash",
    "host_kernel",
    "cpu_model",
    "governor",
    "smt_control",
    "cpufreq_boost",
    "virt",
    "steal_start_ticks",
    "loadavg_1m",
    "canary_stvec_ns",
    "canary_page_verify_ns",
    "qemu_cpu",
    "client_cpu",
    "client_granularity_ns",
    "shuffle_seed",
    "run_order",
    "steal_ticks",
    "steal_ns",
    "e0_mono_ns",
    "e0_wall_ns",
    "e0_to_first_connect_ns",
    "e0_to_e4_ns",
    "w_ns",
    "d_ack_ns",
    "d_fin_ns",
    "synack_to_http_ns",
    "guest_ftx_ns",
    "guest_arp_req_n",
    "attempts",
    "pcap_path",
]
# D-0077. Every gate on the trial path raises before the row is built,
# so a failing trial used to vanish from the record entirely — which
# defeats D-0075 item 4 exactly when it matters. These rows are
# diagnostic: they never enter runs.csv and never reach aggregation.
GATE_FAILURE_FIELDS = [
    "batch_id",
    "trial",
    "warmup",
    "system",
    "config",
    "run_order",
    "guest_ftx_ns",
    "guest_arp_req_n",
    "syn_grid_dt_ns",
    "pcap_path",
    "gate",
]
PHASES_FIELDS = [
    "batch_id",
    "trial",
    "warmup",
    "system",
    "config",
    "phase",
    "ticks",
    "ns_since_e2",
    "delta_ticks",
    "delta_ns",
    "source",
]

LINUX_APPEND_QUIET = "console=ttyS0 quiet loglevel=0 rdinit=/init"
LINUX_APPEND_INSTRUMENTED = (
    "console=ttyS0 loglevel=7 printk.time=1 initcall_debug rdinit=/init"
)
WHIMBREL_QEMU_FLOOR_S = 12.0
LINUX_QEMU_FLOOR_S = 60.0
ARTIFACT_RE = re.compile(r"^artifact (\S+) ([0-9a-f]{64})$")
APPEND_RE = re.compile(r"^append (quiet|instrumented) (.+)$")


@dataclass(frozen=True)
class Arm:
    config: str
    system: str
    features: tuple[str, ...] = ()
    env_extra: tuple[tuple[str, str], ...] = ()
    cargo_extra: tuple[str, ...] = ()
    linux_image: str | None = None
    linux_append: str | None = None
    # D-0079: "mshim" = boot under the extracted M-mode shim blob in
    # QEMU's -bios slot; None = -bios default (OpenSBI). Whimbrel only.
    qemu_bios: str | None = None


class BenchFail(Exception):
    pass


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def tshark_bin() -> str:
    return os.environ.get("BENCH_TSHARK", "tshark")


def require_tshark() -> str:
    name = tshark_bin()
    path = name if os.path.sep in name else shutil.which(name)
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise BenchFail(
            f"TEST FAIL: tshark not installed ({name}); see docs/SETUP.md"
        )
    return path


def qemu_argv(
    pcap: str, port: int = 8080, bios: str | None = None
) -> tuple[str, list[str]]:
    script = ROOT / "scripts" / "qemu-args.sh"
    env = os.environ.copy()
    if bios is not None:
        env["QEMU_BIOS"] = bios  # D-0079: the shim blob in the -bios slot
    line = subprocess.check_output(
        ["bash", str(script), pcap, str(port)], text=True, env=env
    ).strip()
    args = line.split()
    qemu = os.environ.get("QEMU", "qemu-system-riscv64")
    return qemu, args


def campaign_timeout_s(kind: str) -> float:
    env = os.environ.get("BENCH_TIMEOUT_S")
    if env:
        return float(env)
    return 60.0 if kind == "t48" else 12.0


def qemu_timeout_s(system: str, client_timeout_s: float) -> float:
    floor = LINUX_QEMU_FLOOR_S if system == "linux" else WHIMBREL_QEMU_FLOOR_S
    return max(client_timeout_s, floor) + 2.0


def linux_art_dir() -> Path:
    return ROOT / "bench" / "linux" / "artifacts"


def linux_manifest_path() -> Path:
    return ROOT / "bench" / "linux" / "MANIFEST"


def parse_linux_manifest(text: str) -> dict:
    artifacts: dict[str, str] = {}
    appends: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ARTIFACT_RE.match(line)
        if m:
            artifacts[m.group(1)] = m.group(2)
            continue
        m = APPEND_RE.match(line)
        if m:
            appends[m.group(1)] = m.group(2).strip()
            continue
        raise BenchFail(f"TEST FAIL: malformed MANIFEST line: {line}")
    want_art = ("Image-stock", "Image-trimmed", "rootfs.cpio", "init")
    missing = [n for n in want_art if n not in artifacts]
    if missing:
        raise BenchFail(
            f"TEST FAIL: MANIFEST missing artifact lines: {missing}"
        )
    if appends.get("quiet") != LINUX_APPEND_QUIET:
        raise BenchFail(
            f"TEST FAIL: MANIFEST append quiet {appends.get('quiet')!r} "
            f"want {LINUX_APPEND_QUIET!r}"
        )
    if appends.get("instrumented") != LINUX_APPEND_INSTRUMENTED:
        raise BenchFail(
            f"TEST FAIL: MANIFEST append instrumented "
            f"{appends.get('instrumented')!r} "
            f"want {LINUX_APPEND_INSTRUMENTED!r}"
        )
    return {"artifacts": artifacts, "appends": appends}


def verify_linux_artifacts(names: list[str] | None = None) -> dict:
    man_path = linux_manifest_path()
    if not man_path.is_file() or man_path.stat().st_size == 0:
        raise BenchFail(f"TEST FAIL: linux artifact missing: {man_path}")
    parsed = parse_linux_manifest(man_path.read_text(encoding="utf-8"))
    art = linux_art_dir()
    check = names or list(parsed["artifacts"])
    for name in check:
        if name not in parsed["artifacts"]:
            raise BenchFail(
                f"TEST FAIL: linux artifact missing: MANIFEST has no {name}"
            )
        path = art / name
        if not path.is_file() or path.stat().st_size == 0:
            raise BenchFail(f"TEST FAIL: linux artifact missing: {path}")
        got = sha256_file(path)
        want = parsed["artifacts"][name]
        if got != want:
            raise BenchFail(
                f"TEST FAIL: linux artifact mismatch: {path}\n"
                f"  sha256={got} want {want}"
            )
    return parsed


def guest_qemu_extra(
    arm: Arm, kernel: Path, cpio: Path | None, append: str | None
) -> list[str]:
    if arm.system == "whimbrel":
        return ["-kernel", str(kernel)]
    if arm.system == "linux":
        if cpio is None or append is None:
            raise BenchFail("TEST FAIL: Linux argv requires -initrd and -append")
        return [
            "-kernel",
            str(kernel),
            "-initrd",
            str(cpio),
            "-append",
            append,
        ]
    raise BenchFail(f"TEST FAIL: unknown system {arm.system}")


def linux_append_for(arm: Arm) -> str | None:
    if arm.system != "linux":
        return None
    if arm.linux_append == "quiet":
        return LINUX_APPEND_QUIET
    if arm.linux_append == "instrumented":
        return LINUX_APPEND_INSTRUMENTED
    raise BenchFail(f"TEST FAIL: linux arm {arm.config} has no append kind")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_identity() -> tuple[str, int]:
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    porcelain = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True
    )
    dirty = 1 if porcelain.strip() else 0
    return sha, dirty


def host_meta() -> dict:
    kernel = os.uname().release
    cpu = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if line.lower().startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    loadavg = Path("/proc/loadavg").read_text().split()[0]
    qemu = os.environ.get("QEMU", "qemu-system-riscv64")
    qpath = shutil.which(qemu)
    if not qpath:
        raise BenchFail(f"TEST FAIL: {qemu} not on PATH")
    ver = subprocess.check_output([qpath, "--version"], text=True).splitlines()[0]
    return {
        "host_kernel": kernel,
        "cpu_model": cpu,
        "loadavg_1m": loadavg,
        "qemu_version": ver,
        "qemu_hash": sha256_file(Path(qpath)),
        "qemu_path": qpath,
    }


def require_port_free(port: int) -> None:
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
    except OSError as e:
        raise BenchFail(f"TEST FAIL: port {port} is busy ({e})") from e
    finally:
        s.close()


def pin_cpus() -> tuple[int, int]:
    n = os.cpu_count() or 1
    if n < 2:
        raise BenchFail(f"TEST FAIL: need ≥2 CPUs to pin QEMU and client, have {n}")
    qemu_cpu = int(os.environ.get("BENCH_QEMU_CPU", str(n - 2)))
    client_cpu = int(os.environ.get("BENCH_CLIENT_CPU", str(n - 1)))
    if qemu_cpu == client_cpu:
        raise BenchFail("TEST FAIL: QEMU and client must pin to separate cores")
    return qemu_cpu, client_cpu


def steal_ticks_from_stat(text: str) -> int:
    """Aggregate `cpu` line, steal column (field 8 after the `cpu` token)."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            parts = line.split()
            if len(parts) < 9:
                raise BenchFail(
                    "TEST FAIL: /proc/stat cpu line has no steal column"
                )
            return int(parts[8])
    raise BenchFail("TEST FAIL: /proc/stat has no cpu line")


def read_steal_ticks() -> int:
    path = Path("/proc/stat")
    if not path.is_file():
        raise BenchFail("TEST FAIL: /proc/stat missing (cannot record steal)")
    return steal_ticks_from_stat(path.read_text())


def steal_ticks_to_ns(ticks: int) -> int:
    hz = os.sysconf("SC_CLK_TCK")
    if hz <= 0:
        raise BenchFail("TEST FAIL: SC_CLK_TCK is not positive")
    return int(ticks) * 1_000_000_000 // int(hz)


def read_sysfs(path: Path) -> str:
    """Missing or unreadable sysfs is `unavailable`, never a silent skip."""
    if not path.is_file():
        return "unavailable"
    try:
        text = path.read_text().strip()
    except OSError:
        return "unavailable"
    return text if text else "unavailable"


def detect_virt() -> str:
    exe = shutil.which("systemd-detect-virt")
    if not exe:
        return "unavailable"
    proc = subprocess.run([exe], capture_output=True, text=True)
    text = (proc.stdout or "").strip()
    return text if text else "unavailable"


def host_controls() -> dict:
    """Snapshot of the five dedicated-host controls (D-0055 / SETUP.md §7)."""
    return {
        "governor": read_sysfs(
            Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        ),
        "smt_control": read_sysfs(Path("/sys/devices/system/cpu/smt/control")),
        "cpufreq_boost": read_sysfs(
            Path("/sys/devices/system/cpu/cpufreq/boost")
        ),
        "virt": detect_virt(),
        "steal_start_ticks": read_steal_ticks(),
    }


# Governor / SMT / boost are volatile (sysfs; power-profiles-daemon can
# flip the governor from a desktop menu). Virt and steal are the
# dedicated-host predicates. Missing sysfs is `unavailable`, not a pass.
HOST_CONTROL_WANT = {
    "governor": "performance",
    "smt_control": "off",
    "cpufreq_boost": "0",
    "virt": "none",
    "steal_start_ticks": 0,
}


def require_host_controls(ctrl: dict | None = None) -> dict:
    """Fail closed if a dedicated-host control is not in force.

    Names the failing control in the message. Call at batch start; do
    not call from `selftest` against the live machine (cloud agents
    must still exercise the other fail-closed checks).
    """
    ctrl = host_controls() if ctrl is None else ctrl
    for key, want in HOST_CONTROL_WANT.items():
        got = ctrl[key]
        if got != want:
            raise BenchFail(
                f"TEST FAIL: host control {key}={got!r} (want {want!r})"
            )
    return ctrl


def git_current_branch() -> str:
    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if not branch or branch == "HEAD":
        raise BenchFail(
            "TEST FAIL: detached HEAD (no current branch to compare to origin)"
        )
    return branch


def fetch_origin() -> None:
    proc = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or (
            f"exit {proc.returncode}"
        )
        raise BenchFail(f"TEST FAIL: git fetch origin failed: {err}")


def origin_ref_sha(branch: str) -> str:
    ref = f"origin/{branch}"
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BenchFail(f"TEST FAIL: {ref} does not exist after fetch")
    return proc.stdout.strip()


def require_origin_sync(state: dict | None = None) -> dict:
    """Fail closed if HEAD is not origin/<branch> after fetch.

    `git_sha` in runs.csv is forensic; this is preventive. A failed
    pull must not let a batch run on a stale or unpushed tree. Inject
    `state` in selftest so the live fetch is not required there.
    """
    if state is None:
        fetch_origin()
        branch = git_current_branch()
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        origin = origin_ref_sha(branch)
        state = {"branch": branch, "head": head, "origin": origin}
    head = state["head"]
    origin = state["origin"]
    branch = state["branch"]
    if head != origin:
        raise BenchFail(
            f"TEST FAIL: HEAD {head} != origin/{branch} {origin}"
        )
    return state


def recorded_schedule(
    configs: list[str], n: int, warmup: int, seed: int, batch_i: int
) -> list[tuple[str, int]]:
    """Shuffled (config, trial) pairs for recorded trials in one batch.

    Trial numbers stay per-config (warmup+1 .. warmup+n) so CSV identity
    is unchanged. Wall-clock order is `run_order`, not `trial`.
    """
    schedule = [
        (cfg, trial)
        for cfg in configs
        for trial in range(warmup + 1, warmup + n + 1)
    ]
    rng = random.Random(seed + batch_i)
    rng.shuffle(schedule)
    return schedule


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0.0 or dy == 0.0:
        return None
    return num / (dx * dy)


def _average_ranks(vals: list[float]) -> list[float]:
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(_average_ranks(xs), _average_ranks(ys))


def parse_phases(serial_text: str) -> list[dict]:
    rows = []
    for raw in serial_text.splitlines():
        line = raw.replace("\r", "").strip()
        if not line.startswith("PHASE"):
            continue
        if PHASE_HEADER_RE.match(line):
            continue
        if PHASE_UNSET_RE.match(line):
            raise BenchFail(f"TEST FAIL: malformed PHASE line (unset): {line}")
        m = PHASE_ROW_RE.match(line)
        if not m:
            raise BenchFail(f"TEST FAIL: malformed PHASE line: {line}")
        name, ticks, _ns, since, since_ns, delta, delta_ns = m.groups()
        rows.append(
            {
                "phase": name,
                "ticks": int(ticks),
                "ns_since_e2": int(since_ns),
                "delta_ticks": int(delta),
                "delta_ns": int(delta_ns),
                "source": "serial",
                "_since_ticks": int(since),
            }
        )
    if not rows:
        raise BenchFail("TEST FAIL: no PHASE rows in serial")
    names = [r["phase"] for r in rows]
    if names[0] != "_start":
        raise BenchFail(
            f"TEST FAIL: first PHASE row is {names[0]!r}, want _start (E2)"
        )
    if "E3g" not in names:
        raise BenchFail("TEST FAIL: PHASE E3g missing")
    if "stamp_a" not in names or "stamp_b" not in names:
        raise BenchFail("TEST FAIL: stamp_a/stamp_b missing (overhead pair)")
    assert_phases_sum_to_e3g(rows)
    return rows


def phases_from_serial(serial_text: str, system: str) -> list[dict]:
    """PHASE-presence is Whimbrel-only. Linux serial has no PHASE lines."""
    if system != "whimbrel":
        return []
    return parse_phases(serial_text)


TICK_NS = 100


def stamp_overhead_ns(rows: list[dict]) -> int:
    by = {r["phase"]: r for r in rows}
    return max(int(by["stamp_b"]["delta_ns"]), TICK_NS)


def assert_phases_sum_to_e3g(rows: list[dict]) -> int:
    """Sum of deltas up to E3g must equal E2→E3g within the stamp floor."""
    overhead = stamp_overhead_ns(rows)
    e3g = next(r for r in rows if r["phase"] == "E3g")
    e2e3g = int(e3g["ns_since_e2"])
    total = sum(
        int(r["delta_ns"]) for r in rows if int(r["ns_since_e2"]) <= e2e3g
    )
    if abs(total - e2e3g) > overhead:
        raise BenchFail(
            f"TEST FAIL: phase deltas sum to {total} ns, E2→E3g is {e2e3g} ns "
            f"(overhead floor {overhead} ns)"
        )
    return overhead


def runs_schema(fieldnames) -> str:
    """old = e0_to_e3w_ns without W/D_*; new = W/D_* without e0_to_e3w_ns.

    Mixed or incomplete headers fail closed (D-0071). Historical git
    objects keep the old schema; this does not rewrite them.
    """
    fields = set(fieldnames)
    has_e3w = "e0_to_e3w_ns" in fields
    new_cols = {"w_ns", "d_ack_ns", "d_fin_ns"}
    has_new = new_cols <= fields
    if has_e3w and has_new:
        raise BenchFail(
            "TEST FAIL: mixed runs.csv schema "
            "(e0_to_e3w_ns and w_ns/d_ack_ns/d_fin_ns both present)"
        )
    if has_e3w:
        extra = new_cols & fields
        if extra:
            raise BenchFail(
                "TEST FAIL: mixed runs.csv schema "
                f"(e0_to_e3w_ns with partial new columns {sorted(extra)})"
            )
        return "old"
    if has_new:
        return "new"
    raise BenchFail(
        "TEST FAIL: incomplete runs.csv schema "
        "(need e0_to_e3w_ns without w_ns/d_ack_ns/d_fin_ns, "
        "or w_ns/d_ack_ns/d_fin_ns without e0_to_e3w_ns)"
    )


def require_pcap_intervals(
    pcap: Path, tshark: str, *, system: str
) -> dict[str, int]:
    """Per-trial W / D_ack / D_fin via the D-0070 extract. Warmup included."""
    try:
        extracted = _D0070.extract_pcap(pcap, tshark)
    except _D0070.PassFail as e:
        raise BenchFail(str(e)) from e
    http_len = int(extracted["http_len"])
    if http_len != HTTP_LEN_PIN:
        raise BenchFail(
            f"TEST FAIL: HTTP tcp.len={http_len} want {HTTP_LEN_PIN} in {pcap}"
        )
    for key in ("w_ns", "d_ack_ns", "d_fin_ns"):
        if extracted[key] < 0:
            raise BenchFail(
                f"TEST FAIL: {key} is negative ({extracted[key]}) in {pcap}"
            )
    if system == "whimbrel" and extracted["d_fin_ns"] >= WHIMBREL_DFIN_FAIL_NS:
        raise BenchFail(
            f"TEST FAIL: d_fin_ns={extracted['d_fin_ns']} ≥ 10 ms "
            f"(D-0070 falsify line) in {pcap}"
        )
    try:
        assert_no_rst(pcap, tshark)
    except PcapExtractError as e:
        raise BenchFail(str(e)) from e
    return extracted


def require_first_connect_control(runs: list[dict]) -> None:
    """Listener-up is guest-independent. A miss fails the run, not a cell."""
    rec = [r for r in runs if int(r["warmup"]) == 0]
    if not rec:
        return
    by_cfg: dict[str, list[int]] = {}
    by_sys: dict[str, list[int]] = {}
    for r in rec:
        v = int(r["e0_to_first_connect_ns"])
        by_cfg.setdefault(r["config"], []).append(v)
        by_sys.setdefault(r["system"], []).append(v)
    if SAFE_CONFIG in by_cfg and FAST_CONFIG in by_cfg:
        ms = statistics.median(by_cfg[SAFE_CONFIG])
        mf = statistics.median(by_cfg[FAST_CONFIG])
        delta = abs(ms - mf)
        if delta > CONTROL_TOL_NS:
            raise BenchFail(
                f"TEST FAIL: first-connect control |safe − fast| = {delta:.0f} ns "
                f"(safe={ms:.0f} fast={mf:.0f}) > 1 ms"
            )
    if len(by_sys) > 1:
        names = sorted(by_sys)
        meds = {s: statistics.median(by_sys[s]) for s in names}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                delta = abs(meds[a] - meds[b])
                if delta > CONTROL_TOL_NS:
                    raise BenchFail(
                        f"TEST FAIL: first-connect control |{a} − {b}| = "
                        f"{delta:.0f} ns ({a}={meds[a]:.0f} {b}={meds[b]:.0f}) "
                        f"> 1 ms"
                    )


# D-0078 act-on: one release-default boot before trial 1 of any
# campaign. Its stvec / page_verify deltas are that day's serial-byte
# cost made visible in the batch header — the safe profile prints
# ~13.1 KB inside its measured window, so those two deltas move ~14 %
# / ~36 % between serial-cost regimes while fast-boot moves 0. The
# canary is not a trial: it never enters runs.csv.
CANARY_PHASES = ("stvec", "page_verify")


def canary_values(phases: list[dict]) -> dict:
    """Fail-closed: a canary without its PHASE deltas aborts the campaign."""
    by = {p["phase"]: int(p["delta_ns"]) for p in phases}
    missing = [n for n in CANARY_PHASES if n not in by]
    if missing:
        raise BenchFail(
            f"TEST FAIL: canary boot produced no PHASE dump for {missing} "
            "(D-0078; refusing to start a campaign without the day's "
            "serial-cost measurement)"
        )
    return {f"canary_{n}_ns": by[n] for n in CANARY_PHASES}


def canary_header_lines(canary: dict | None) -> list[str]:
    if not canary:
        return []
    return [
        f"canary_stvec_ns={canary['canary_stvec_ns']} "
        f"canary_page_verify_ns={canary['canary_page_verify_ns']} "
        "(D-0078: one release-default boot before trial 1; that day's "
        "serial-byte regime. T4.8 regime ~1.03/11.9 ms, T4.8b ~1.17/16.2 "
        "ms. Safe-profile numbers compare across campaigns only when "
        "these agree.)"
    ]


def s_trial_ns(row: dict) -> int:
    """S = (E4 − first_connect) − pcap(ARP → FIN). Not a CSV column."""
    syn = row.get("synack_to_http_ns", "")
    if syn is None or syn == "":
        extracted = require_pcap_intervals(
            ROOT / row["pcap_path"],
            require_tshark(),
            system=row.get("system", "whimbrel"),
        )
        syn_ns = int(extracted["synack_to_http_ns"])
        w_ns = int(extracted["w_ns"])
        d_fin_ns = int(extracted["d_fin_ns"])
    else:
        syn_ns = int(syn)
        w_ns = int(row["w_ns"])
        d_fin_ns = int(row["d_fin_ns"])
    return (int(row["e0_to_e4_ns"]) - int(row["e0_to_first_connect_ns"])) - (
        w_ns + syn_ns + d_fin_ns
    )


def s_header_lines(runs: list[dict]) -> list[str]:
    rec = [r for r in runs if int(r["warmup"]) == 0]
    if not rec:
        raise BenchFail("TEST FAIL: no recorded trials for s_ns header")
    all_vals = [(r, s_trial_ns(r)) for r in rec]
    # D-0079: S is never pooled across firmware lanes — the shim lane
    # has no fw_dynamic load, so its S is a different population by
    # construction. The pooled line covers the default lane only; the
    # shim lane gets its own.
    vals = [s for r, s in all_vals if not r["config"].startswith("m-")]
    m_vals = [s for r, s in all_vals if r["config"].startswith("m-")]
    by_cfg: dict[str, list[int]] = {}
    for r, s in all_vals:
        by_cfg.setdefault(r["config"], []).append(s)
    med = statistics.median(vals)
    lines = [
        f"s_ns={med:.0f} iqr={iqr([float(v) for v in vals]):.0f} n={len(vals)}",
    ]
    if m_vals:
        lines.append(
            f"s_ns_mshim={statistics.median(m_vals):.0f} "
            f"iqr={iqr([float(v) for v in m_vals]):.0f} n={len(m_vals)} "
            "(D-0079: shim lane, never pooled with the line above)"
        )
    fast_vals = by_cfg.get(FAST_CONFIG, [])
    safe_vals = by_cfg.get(SAFE_CONFIG, [])
    fast_s = f"{statistics.median(fast_vals):.0f}" if fast_vals else "absent"
    safe_s = f"{statistics.median(safe_vals):.0f}" if safe_vals else "absent"
    lines.append(f"s_ns_fast={fast_s} s_ns_safe={safe_s}")
    m_fast = by_cfg.get("m-" + FAST_CONFIG, [])
    m_safe = by_cfg.get("m-" + SAFE_CONFIG, [])
    if m_fast and m_safe:
        mf, ms = statistics.median(m_fast), statistics.median(m_safe)
        flag = ""
        if abs(mf - ms) > CONTROL_TOL_NS:
            # Measured, not assumed: t47's first run showed the S model
            # is NOT profile-independent on the shim lane (m-safe reads
            # ~1.9 ms low; the anchor-comparable fast pair is clean and
            # carries the ΔS falsifiers). Recorded as an open item in
            # D-0079 rather than enforced — shim-lane S has no consumer
            # and gating a campaign on a diagnostic quantity whose
            # model does not transfer would block real numbers over a
            # number nothing reads.
            flag = " (PROFILE-DEPENDENT: D-0079 open item, no consumer)"
        lines.append(f"s_ns_m_fast={mf:.0f} s_ns_m_safe={ms:.0f}{flag}")
    if fast_vals and safe_vals:
        mf = statistics.median(fast_vals)
        ms = statistics.median(safe_vals)
        delta = abs(mf - ms)
        if delta > CONTROL_TOL_NS:
            raise BenchFail(
                f"TEST FAIL: |s_ns_fast − s_ns_safe| = {delta:.0f} ns "
                f"(fast={mf:.0f} safe={ms:.0f}) > 1 ms"
            )
    return lines


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        raise BenchFail("TEST FAIL: percentile of empty metric")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def iqr(vals: list[float]) -> float:
    s = sorted(vals)
    return percentile(s, 0.75) - percentile(s, 0.25)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def append_csv(path: Path, fields: list[str], row: dict) -> None:
    """Append one row, writing the header if the file is new."""
    new_file = not path.is_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def gate_failure_signature(pcap: Path) -> dict:
    """Best effort, never raises. A recorder must not need a gate to pass."""
    out: dict = {}
    try:
        tshark = require_tshark()
        rows = tshark_table(pcap, tshark, GUEST_TX_FILTER)
        arp = tshark_table(pcap, tshark, GUEST_ARP_REQ_FILTER)
        slirp_arp = tshark_table(pcap, tshark, ARP_FILTER)
        out["guest_arp_req_n"] = len(arp)
        if rows and slirp_arp:
            t_tx = _time_ns(rows[0])
            out["guest_ftx_ns"] = t_tx - _time_ns(slirp_arp[0])
            syn = tshark_table(pcap, tshark, SYN_IN_FILTER)
            flushed = [r for r in syn if _time_ns(r) >= t_tx]
            if flushed:
                out["syn_grid_dt_ns"] = _time_ns(flushed[0]) - t_tx
    except Exception:  # noqa: BLE001 — diagnostics must not mask the gate
        pass
    return out


def gate_failures_path() -> Path:
    return ROOT / "results" / "gate-failures.csv"


def record_gate_failure(
    *, batch_id: str, trial: int, is_warmup: int, system: str, config: str,
    run_order: int, pcap: Path, gate: str, out_path: Path | None = None,
) -> None:
    row = {
        "batch_id": batch_id,
        "trial": trial,
        "warmup": is_warmup,
        "system": system,
        "config": config,
        "run_order": run_order,
        "pcap_path": os.path.relpath(pcap, ROOT) if pcap.is_file() else "",
        "gate": " ".join(gate.split())[:240],
    }
    row.update(gate_failure_signature(pcap))
    dest = out_path or gate_failures_path()
    append_csv(dest, GATE_FAILURE_FIELDS, row)
    print(
        f"bench: GATE FAILURE recorded to {os.path.relpath(dest, ROOT)} "
        f"({system}/{config} trial={trial}): {row['gate'][:100]}",
        flush=True,
    )


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise BenchFail(f"TEST FAIL: {path} missing")
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def assert_aggregatable(runs: list[dict], *, allow_dirty: bool = False) -> None:
    if not runs:
        raise BenchFail("TEST FAIL: zero-trial CSV (nothing to aggregate)")
    runs_schema(runs[0].keys())
    recorded = [r for r in runs if int(r["warmup"]) == 0]
    if not recorded:
        raise BenchFail("TEST FAIL: zero-trial CSV (all warmup or empty recorded)")
    for r in recorded:
        if int(r["dirty"]) != 0 and not allow_dirty:
            raise BenchFail(
                f"TEST FAIL: refusing to aggregate dirty-tree row "
                f"batch={r['batch_id']} trial={r['trial']}"
            )
    qemus = {r["qemu_version"] for r in recorded}
    if len(qemus) != 1:
        raise BenchFail(
            f"TEST FAIL: QEMU version mismatch in batch: {sorted(qemus)}"
        )
    shas = {r["git_sha"] for r in recorded}
    if len(shas) != 1:
        raise BenchFail(
            f"TEST FAIL: git SHA mismatch in batch: {sorted(shas)}"
        )
    for field in (
        "governor",
        "smt_control",
        "cpufreq_boost",
        "virt",
        "steal_start_ticks",
    ):
        if field not in recorded[0]:
            continue
        vals = {r[field] for r in recorded}
        if len(vals) != 1:
            raise BenchFail(
                f"TEST FAIL: {field} mismatch in batch: {sorted(vals)}"
            )


def metric_table(
    runs: list[dict], phases: list[dict], schema: str | None = None
) -> dict[str, list[float]]:
    recorded_runs = [r for r in runs if int(r["warmup"]) == 0]
    if schema is None:
        schema = runs_schema(recorded_runs[0].keys() if recorded_runs else runs[0].keys())
    run_keys = NEW_RUN_METRICS if schema == "new" else OLD_RUN_METRICS
    metrics: dict[str, list[float]] = {k: [] for k in run_keys}
    for r in recorded_runs:
        for k in metrics:
            metrics[k].append(float(r[k]))
    rec_keys = {(r["batch_id"], r["trial"], r["config"]) for r in recorded_runs}
    by_phase: dict[str, list[float]] = {}
    e2_to_e3g: list[float] = []
    for p in phases:
        if int(p["warmup"]) != 0:
            continue
        key = (p["batch_id"], p["trial"], p["config"])
        if key not in rec_keys:
            continue
        name = p["phase"]
        by_phase.setdefault(f"phase_{name}_delta_ns", []).append(float(p["delta_ns"]))
        by_phase.setdefault(f"phase_{name}_since_e2_ns", []).append(
            float(p["ns_since_e2"])
        )
        if name == "E3g":
            e2_to_e3g.append(float(p["ns_since_e2"]))
    metrics.update(by_phase)
    if e2_to_e3g:
        metrics["e2_to_e3g_ns"] = e2_to_e3g
    return metrics


def summarize_group(name: str, metrics: dict[str, list[float]]) -> list[str]:
    lines = [f"## {name}", f"{'metric':<36} {'n':>4} {'median':>14} {'IQR':>14} {'min':>14} {'max':>14}"]
    for metric, vals in sorted(metrics.items()):
        if not vals:
            continue
        n = len(vals)
        med = statistics.median(vals)
        lines.append(
            f"{metric:<36} {n:4d} {med:14.0f} {iqr(vals):14.0f} "
            f"{min(vals):14.0f} {max(vals):14.0f}"
        )
    return lines


def stability_tol_ns(median_ns: float) -> float:
    return max(0.02 * abs(median_ns), 200_000.0)


def compare_stability(
    a: dict[str, list[float]], b: dict[str, list[float]]
) -> list[str]:
    failed = []
    names = sorted(set(a) | set(b))
    for name in names:
        va, vb = a.get(name, []), b.get(name, [])
        if not va or not vb:
            failed.append(f"{name}: missing in one batch")
            continue
        ma, mb = statistics.median(va), statistics.median(vb)
        if max(ma, mb) < 1_000_000:
            continue
        tol = stability_tol_ns(max(ma, mb))
        if abs(ma - mb) > tol:
            failed.append(
                f"{name}: median {ma:.0f} vs {mb:.0f} ns "
                f"(Δ={abs(ma-mb):.0f}, tol={tol:.0f})"
            )
    return failed


def calibrate_client(client_cpu: int, port: int) -> int:
    client = ROOT / "scripts" / "bench-client.py"
    n = int(os.environ.get("BENCH_CALIBRATE_N", "200"))
    cmd = [
        "taskset",
        "-c",
        str(client_cpu),
        sys.executable,
        str(client),
        "--calibrate",
        str(n),
        "--port",
        str(port),
    ]
    out = subprocess.check_output(cmd, text=True)
    data = json.loads(out)
    gran = int(data["granularity_ns"])
    print(
        f"bench: client granularity median={gran} ns "
        f"(target 1000000, min={data['granularity_min_ns']} "
        f"max={data['granularity_max_ns']})",
        flush=True,
    )
    return gran


def cargo_build(
    features: list[str],
    extra: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
) -> Path:
    cmd = ["cargo", "build", "--release", "--manifest-path", str(ROOT / "Cargo.toml")]
    if features:
        cmd += ["--features", ",".join(features)]
    if extra:
        cmd += extra
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    target_dir = Path(env.get("CARGO_TARGET_DIR", ROOT / "target"))
    print("bench: " + " ".join(cmd), flush=True)
    if env_extra:
        print("bench: env " + " ".join(f"{k}={v}" for k, v in env_extra.items()), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)
    kernel = target_dir / "riscv64gc-unknown-none-elf" / "release" / "whimbrel"
    if not kernel.is_file():
        raise BenchFail(f"TEST FAIL: cargo build produced no kernel at {kernel}")
    return kernel


def run_trial(
    *,
    arm: Arm,
    extra: list[str],
    pcap: Path,
    serial_path: Path,
    client_out: Path,
    ready_path: Path,
    qemu_cpu: int,
    client_cpu: int,
    port: int,
    client_timeout_s: float,
    qemu_wait_s: float,
    bios_path: str | None = None,
) -> dict:
    tshark = require_tshark()
    qemu, args = qemu_argv(str(pcap), port, bios=bios_path)
    for p in (pcap, serial_path, client_out, ready_path):
        if p.exists():
            p.unlink()
    pcap.parent.mkdir(parents=True, exist_ok=True)
    serial_path.parent.mkdir(parents=True, exist_ok=True)

    client_cmd = [
        "taskset",
        "-c",
        str(client_cpu),
        sys.executable,
        str(ROOT / "scripts" / "bench-client.py"),
        "--port",
        str(port),
        "--timeout-s",
        str(client_timeout_s),
        "--ready",
        str(ready_path),
        "--out",
        str(client_out),
    ]
    client = subprocess.Popen(client_cmd, cwd=ROOT)
    try:
        t0 = time.monotonic()
        while not ready_path.is_file():
            if time.monotonic() - t0 > 5:
                raise BenchFail("TEST FAIL: measurement client never became ready")
            if client.poll() is not None:
                raise BenchFail(
                    f"TEST FAIL: measurement client exited before ready ({client.returncode})"
                )
            time.sleep(0.0005)

        qemu_cmd = ["taskset", "-c", str(qemu_cpu)]
        if shutil.which("stdbuf"):
            qemu_cmd += ["stdbuf", "-oL"]
        qemu_cmd += [qemu, *args, *extra]
        e0_mono = time.monotonic_ns()
        e0_wall = time.time_ns()
        with open(serial_path, "wb") as ser:
            qemu_p = subprocess.Popen(
                qemu_cmd, cwd=ROOT, stdout=ser, stderr=subprocess.STDOUT
            )
        try:
            qemu_p.wait(timeout=qemu_wait_s)
        except subprocess.TimeoutExpired:
            qemu_p.kill()
            qemu_p.wait(timeout=2)
            raise BenchFail(f"TEST FAIL: QEMU timed out after {qemu_wait_s}s")
        grace = time.monotonic() + 2.0
        while client.poll() is None and time.monotonic() < grace:
            time.sleep(0.01)
        if client.poll() is None:
            client.kill()
            client.wait(timeout=2)
            raise BenchFail("TEST FAIL: measurement client did not finish after QEMU exit")
    finally:
        if client.poll() is None:
            client.kill()
            try:
                client.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    if not client_out.is_file():
        raise BenchFail("TEST FAIL: client result JSON missing")
    client_data = json.loads(client_out.read_text())
    if not client_data.get("body_ok"):
        raise BenchFail("TEST FAIL: client did not receive the 92-byte RESP")
    if client_data.get("first_connect_mono_ns") is None:
        raise BenchFail("TEST FAIL: no first-connect stamp")
    if client_data.get("first_byte_mono_ns") is None:
        raise BenchFail("TEST FAIL: no first-byte stamp (E4)")

    serial_text = serial_path.read_bytes().decode("utf-8", errors="replace")
    if "PANIC" in serial_text or "Kernel panic" in serial_text:
        raise BenchFail("TEST FAIL: guest panic")
    falsifier3_scan(serial_text)
    if arm.system == "linux":
        if "INIT FAIL:" in serial_text:
            raise BenchFail("TEST FAIL: Linux /init INIT FAIL")
        if "READY" not in serial_text:
            raise BenchFail("TEST FAIL: Linux READY missing")
        if "LINUX INIT OK" not in serial_text:
            raise BenchFail("TEST FAIL: LINUX INIT OK missing")
    phases = phases_from_serial(serial_text, arm.system)
    e0_to_connect = int(client_data["first_connect_mono_ns"]) - e0_mono
    e0_to_e4 = int(client_data["first_byte_mono_ns"]) - e0_mono
    extracted = require_pcap_intervals(pcap, tshark, system=arm.system)
    if arm.system == "linux":
        try:
            assert_syn_grid(pcap, tshark)
        except PcapExtractError as e:
            raise BenchFail(str(e)) from e
    return {
        "e0_mono_ns": e0_mono,
        "e0_wall_ns": e0_wall,
        "e0_to_first_connect_ns": e0_to_connect,
        "e0_to_e4_ns": e0_to_e4,
        "w_ns": extracted["w_ns"],
        "d_ack_ns": extracted["d_ack_ns"],
        "d_fin_ns": extracted["d_fin_ns"],
        "synack_to_http_ns": extracted["synack_to_http_ns"],
        "guest_ftx_ns": extracted["guest_ftx_ns"],
        "guest_arp_req_n": extracted["guest_arp_req_n"],
        "attempts": int(client_data["attempts"]),
        "phases": phases,
        "qemu_status": qemu_p.returncode,
    }


# D-0055 registered campaign shape. The D-0080 audit found n/warmup
# were argparse defaults silently overridable via BENCH_N / BENCH_WARMUP,
# and classified two-batch interleaving as "ENFORCED structurally." The
# batch count is the same class as n/warmup: `--batches` defaults from
# BENCH_BATCHES, and cmd_run used to pass stability=batches>=2, so
# batches=1 skipped the two-batch comparison and still printed TEST
# PASS. Campaign kinds now gate n, warmup, and batches; fp-ab is a
# diagnostic kind and is exempt.
REGISTERED_N = 30
REGISTERED_WARMUP = 3
REGISTERED_BATCHES = 2
CAMPAIGN_KINDS = ("whimbrel", "t48", "t47")


def require_registered_counts(
    kind: str, n: int, warmup: int, batches: int
) -> None:
    registered = (REGISTERED_N, REGISTERED_WARMUP, REGISTERED_BATCHES)
    if kind in CAMPAIGN_KINDS and (n, warmup, batches) != registered:
        raise BenchFail(
            f"TEST FAIL: kind={kind} launched with n={n} warmup={warmup} "
            f"batches={batches}; the D-0055 registration is n={REGISTERED_N} "
            f"warmup={REGISTERED_WARMUP} batches={REGISTERED_BATCHES}. A "
            f"stray BENCH_N/BENCH_WARMUP/BENCH_BATCHES env var is the usual "
            f"cause; there is no override."
        )


def falsifier3_scan(serial_text: str) -> None:
    # D-0079 falsifier 3, computed (the D-0080 audit found it was
    # prose-only): the shim's M-mode trap diagnostic emits the literal
    # bytes "M!" then hex CSRs. Any occurrence in any serial is the
    # falsifier itself, not a bug to fix — stop. Empirically zero hits
    # across every retained serial including Linux arms, so the scan is
    # unconditional: a hit anywhere deserves the loud stop.
    if "M!" in serial_text:
        i = serial_text.index("M!")
        raise BenchFail(
            "TEST FAIL: M-mode trap diagnostic 'M!' in serial "
            f"(falsifier 3): {serial_text[i:i + 48]!r}"
        )


def configs_for(kind: str) -> list[Arm]:
    # Release default is no frame pointers (finding 14 stripped). The
    # with-FP arm merges via --config so linker.ld is not dropped.
    fp_yes = (
        "--config",
        'target.riscv64gc-unknown-none-elf.rustflags=["-C","force-frame-pointers=yes"]',
    )
    whimbrel_safe = Arm("release-default", "whimbrel")
    whimbrel_fast = Arm("release-fast-boot", "whimbrel", features=("fast-boot",))
    if kind == "whimbrel":
        return [whimbrel_safe, whimbrel_fast]
    if kind == "fp-ab":
        return [
            Arm(
                "release-fast-boot-fp",
                "whimbrel",
                features=("fast-boot",),
                env_extra=(("CARGO_TARGET_DIR", str(ROOT / "target-fp")),),
                cargo_extra=fp_yes,
            ),
            whimbrel_fast,
        ]
    if kind == "t47":
        # D-0079 / D-0061: the with/without-firmware pair, interleaved in
        # one campaign so the D-0078 serial-regime state and every host
        # control are shared. m-* arms boot the bios-none lane kernel
        # under the shim blob; safe rows are lane-internal only (the
        # console backend differs by construction).
        return [
            whimbrel_fast,
            whimbrel_safe,
            Arm(
                "m-release-fast-boot",
                "whimbrel",
                features=("bios-none", "fast-boot"),
                qemu_bios="mshim",
            ),
            Arm(
                "m-release-default",
                "whimbrel",
                features=("bios-none",),
                qemu_bios="mshim",
            ),
        ]
    if kind == "t48":
        return [
            whimbrel_fast,
            whimbrel_safe,
            Arm(
                "trimmed",
                "linux",
                linux_image="Image-trimmed",
                linux_append="quiet",
            ),
            Arm(
                "stock",
                "linux",
                linux_image="Image-stock",
                linux_append="quiet",
            ),
            Arm(
                "trimmed-instrumented",
                "linux",
                linux_image="Image-trimmed",
                linux_append="instrumented",
            ),
        ]
    raise BenchFail(f"TEST FAIL: unknown bench kind {kind}")


def trimmed_vs_stock_failures(
    metric_by_group: dict[tuple[str, str, str], dict[str, list[float]]],
) -> list[str]:
    """If median E0→E4(trimmed) ≥ stock, trimmed is not published."""
    failed: list[str] = []
    by_batch: dict[str, dict[str, float]] = {}
    for (batch, sys, cfg), mets in metric_by_group.items():
        if sys != "linux":
            continue
        vals = mets.get("e0_to_e4_ns") or []
        if not vals:
            continue
        by_batch.setdefault(batch, {})[cfg] = statistics.median(vals)
    for batch, cfgs in by_batch.items():
        if "trimmed" not in cfgs or "stock" not in cfgs:
            continue
        if cfgs["trimmed"] >= cfgs["stock"]:
            failed.append(
                f"trimmed E0→E4 median {cfgs['trimmed']:.0f} ns >= stock "
                f"{cfgs['stock']:.0f} ns in {batch}; trimmed row is not published"
            )
    return failed


def linux_kernel_hash_failures(runs: list[dict]) -> list[str]:
    rec = [
        r
        for r in runs
        if int(r["warmup"]) == 0 and r.get("system") == "linux"
    ]
    if not rec:
        return []
    man = linux_manifest_path()
    if not man.is_file():
        return [f"linux rows present but MANIFEST missing: {man}"]
    parsed = parse_linux_manifest(man.read_text(encoding="utf-8"))
    failed: list[str] = []
    for r in rec:
        image = "Image-stock" if r["config"] == "stock" else "Image-trimmed"
        want = parsed["artifacts"].get(image)
        if want is None:
            failed.append(f"MANIFEST has no {image} for {r['config']}")
            continue
        if r["kernel_sha256"] != want:
            failed.append(
                f"kernel_sha256={r['kernel_sha256']} want {want} "
                f"({image} {r['config']} trial {r['trial']})"
            )
    return failed


def linux_header_lines(
    *,
    client_timeout_s: float | None,
    linux_meta: dict | None,
) -> list[str]:
    extra: list[str] = []
    if client_timeout_s is not None:
        extra.append(f"client_timeout_s={client_timeout_s:g}")
    if linux_meta:
        extra.append(f"cpio={linux_meta['cpio']}")
        extra.append(f"cpio_sha256={linux_meta['cpio_sha256']}")
        extra.append(f"linux_append_quiet={LINUX_APPEND_QUIET}")
        extra.append(f"linux_append_instrumented={LINUX_APPEND_INSTRUMENTED}")
    return extra


def cmd_run(args: argparse.Namespace) -> int:
    os.chdir(ROOT)
    require_tshark()
    if shutil.which("taskset") is None:
        raise BenchFail("TEST FAIL: taskset not installed")
    git_sha, dirty = git_identity()
    if dirty and not args.allow_dirty:
        raise BenchFail(
            "TEST FAIL: dirty working tree (refusing to produce a batch "
            "the summarizer would reject). Commit or pass --allow-dirty."
        )
    require_origin_sync()
    host = host_meta()
    host.update(require_host_controls())
    qemu_cpu, client_cpu = pin_cpus()
    n = args.n
    warmup = args.warmup
    batches = args.batches
    require_registered_counts(args.kind, n, warmup, batches)
    port = args.port
    require_port_free(port)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gran = calibrate_client(client_cpu, port)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_rows: list[dict] = []
    phase_rows: list[dict] = []
    client_timeout_s = campaign_timeout_s(args.kind)
    print(
        f"bench: client_timeout_s={client_timeout_s:g} "
        f"(uniform recv; not per-system)",
        flush=True,
    )

    arms = configs_for(args.kind)
    linux_arms = [a for a in arms if a.system == "linux"]
    linux_meta: dict | None = None
    if linux_arms:
        names = sorted(
            {a.linux_image for a in linux_arms if a.linux_image}
            | {"rootfs.cpio", "init"}
        )
        parsed = verify_linux_artifacts(list(names))
        cpio = linux_art_dir() / "rootfs.cpio"
        linux_meta = {
            "parsed": parsed,
            "cpio": str(cpio.relative_to(ROOT)),
            "cpio_sha256": parsed["artifacts"]["rootfs.cpio"],
        }

    kernels: dict[str, tuple[Path, str, Arm]] = {}
    kdir = out_dir / "bin"
    kdir.mkdir(parents=True, exist_ok=True)
    for arm in arms:
        if arm.system == "whimbrel":
            env = dict(arm.env_extra) if arm.env_extra else None
            extra = list(arm.cargo_extra) if arm.cargo_extra else None
            kernel_src = cargo_build(
                list(arm.features), extra=extra, env_extra=env
            )
            kernel = kdir / arm.config
            shutil.copy2(kernel_src, kernel)
            kernels[arm.config] = (kernel, sha256_file(kernel), arm)
            continue
        if arm.system == "linux":
            if arm.linux_image is None:
                raise BenchFail(f"TEST FAIL: linux arm {arm.config} has no Image")
            kernel = linux_art_dir() / arm.linux_image
            kernels[arm.config] = (kernel, sha256_file(kernel), arm)
            continue
        raise BenchFail(f"TEST FAIL: unknown system {arm.system}")

    # D-0079: any shim arm needs the blob once per campaign. Built from
    # the donor (mshim feature), extracted by the one blob script, and
    # hashed into every shim row's bios_sha256 column.
    bios_blob: Path | None = None
    bios_blob_sha = ""
    if any(a.qemu_bios == "mshim" for a in arms):
        donor = cargo_build(["mshim"])
        bios_blob = kdir / "mshim.bin"
        subprocess.run(
            ["bash", str(ROOT / "scripts" / "mshim-blob.sh"),
             str(donor), str(bios_blob)],
            cwd=ROOT, check=True,
        )
        bios_blob_sha = sha256_file(bios_blob)
        print(f"bench: mshim blob sha256={bios_blob_sha}", flush=True)

    # D-0078: canary boot. Reuse the campaign's release-default kernel
    # when the arm exists (whimbrel / t48); build it otherwise (fp-ab).
    if SAFE_CONFIG in kernels:
        canary_kernel, _h, canary_arm = kernels[SAFE_CONFIG]
    else:
        canary_kernel = kdir / SAFE_CONFIG
        shutil.copy2(cargo_build([]), canary_kernel)
        canary_arm = Arm(config=SAFE_CONFIG, system="whimbrel")
    canary_dir = out_dir / "trials" / f"{stamp}-canary" / SAFE_CONFIG / "01"
    canary_dir.mkdir(parents=True, exist_ok=True)
    print("bench: canary boot (D-0078, release-default, not a trial)", flush=True)
    try:
        canary_result = run_trial(
            arm=canary_arm,
            extra=guest_qemu_extra(canary_arm, canary_kernel, None, None),
            pcap=canary_dir / "qemu.pcap",
            serial_path=canary_dir / "serial.log",
            client_out=canary_dir / "client.json",
            ready_path=canary_dir / "client.ready",
            qemu_cpu=qemu_cpu,
            client_cpu=client_cpu,
            port=port,
            client_timeout_s=client_timeout_s,
            qemu_wait_s=qemu_timeout_s("whimbrel", client_timeout_s),
        )
    except BenchFail as e:
        record_gate_failure(
            batch_id=f"{stamp}-canary", trial=1, is_warmup=1,
            system="whimbrel", config=SAFE_CONFIG, run_order=0,
            pcap=canary_dir / "qemu.pcap", gate=str(e),
        )
        raise
    canary = canary_values(canary_result["phases"])
    print(
        f"bench: canary stvec={canary['canary_stvec_ns'] / 1e6:.3f} ms "
        f"page_verify={canary['canary_page_verify_ns'] / 1e6:.3f} ms "
        "(D-0078 serial-byte regime)",
        flush=True,
    )

    shuffle_seed = getattr(args, "shuffle_seed", None)
    if shuffle_seed is None:
        env_seed = os.environ.get("BENCH_SHUFFLE_SEED")
        shuffle_seed = (
            int(env_seed) if env_seed else (time.time_ns() % (2**63))
        )
    print(f"bench: shuffle_seed={shuffle_seed}", flush=True)
    run_order = 0
    cfg_names = [a.config for a in arms]

    def one_trial(batch_id: str, config: str, trial: int, is_warmup: int) -> None:
        nonlocal run_order
        run_order += 1
        kernel, k_hash, arm = kernels[config]
        tdir = out_dir / "trials" / batch_id / config / f"{trial:02d}"
        tdir.mkdir(parents=True, exist_ok=True)
        pcap = tdir / "qemu.pcap"
        serial_path = tdir / "serial.log"
        client_out = tdir / "client.json"
        ready_path = tdir / "client.ready"
        append = linux_append_for(arm)
        cpio = linux_art_dir() / "rootfs.cpio" if arm.system == "linux" else None
        extra = guest_qemu_extra(arm, kernel, cpio, append)
        qemu_wait = qemu_timeout_s(arm.system, client_timeout_s)
        print(
            f"bench: batch={batch_id} system={arm.system} config={config} "
            f"trial={trial} warmup={is_warmup} run_order={run_order}",
            flush=True,
        )
        steal0 = read_steal_ticks()
        try:
            result = run_trial(
                arm=arm,
                extra=extra,
                pcap=pcap,
                serial_path=serial_path,
                client_out=client_out,
                ready_path=ready_path,
                qemu_cpu=qemu_cpu,
                client_cpu=client_cpu,
                port=port,
                client_timeout_s=client_timeout_s,
                qemu_wait_s=qemu_wait,
                bios_path=str(bios_blob) if arm.qemu_bios == "mshim" else None,
            )
        except BenchFail as e:
            # D-0077: every gate in run_trial raises before the row is
            # built. Record the trial that failed before letting the
            # abort proceed — one call site, not 18 raise sites.
            record_gate_failure(
                batch_id=batch_id, trial=trial, is_warmup=is_warmup,
                system=arm.system, config=config, run_order=run_order,
                pcap=pcap, gate=str(e),
            )
            raise
        steal_delta = read_steal_ticks() - steal0
        if steal_delta < 0:
            raise BenchFail("TEST FAIL: /proc/stat steal went backwards")
        rel_pcap = os.path.relpath(pcap, ROOT)
        run_rows.append(
            {
                "batch_id": batch_id,
                "trial": trial,
                "warmup": is_warmup,
                "system": arm.system,
                "config": config,
                "git_sha": git_sha,
                "dirty": dirty,
                "kernel_sha256": k_hash,
                "bios_sha256": bios_blob_sha if arm.qemu_bios == "mshim" else "",
                "qemu_version": host["qemu_version"],
                "qemu_hash": host["qemu_hash"],
                "host_kernel": host["host_kernel"],
                "cpu_model": host["cpu_model"],
                "governor": host["governor"],
                "smt_control": host["smt_control"],
                "cpufreq_boost": host["cpufreq_boost"],
                "virt": host["virt"],
                "steal_start_ticks": host["steal_start_ticks"],
                "loadavg_1m": host["loadavg_1m"],
                # D-0079 gap fix: the canary lived only in summary.txt
                # and the console (both uncommitted), so the exhibit's
                # same-campaign canary gate had no committed artifact to
                # read. Constant per row, the host-control pattern.
                "canary_stvec_ns": canary["canary_stvec_ns"],
                "canary_page_verify_ns": canary["canary_page_verify_ns"],
                "qemu_cpu": qemu_cpu,
                "client_cpu": client_cpu,
                "client_granularity_ns": gran,
                "shuffle_seed": shuffle_seed,
                "run_order": run_order,
                "steal_ticks": steal_delta,
                "steal_ns": steal_ticks_to_ns(steal_delta),
                "e0_mono_ns": result["e0_mono_ns"],
                "e0_wall_ns": result["e0_wall_ns"],
                "e0_to_first_connect_ns": result["e0_to_first_connect_ns"],
                "e0_to_e4_ns": result["e0_to_e4_ns"],
                "w_ns": result["w_ns"],
                "d_ack_ns": result["d_ack_ns"],
                "d_fin_ns": result["d_fin_ns"],
                "synack_to_http_ns": result["synack_to_http_ns"],
                "guest_ftx_ns": result["guest_ftx_ns"],
                "guest_arp_req_n": result["guest_arp_req_n"],
                "attempts": result["attempts"],
                "pcap_path": rel_pcap,
            }
        )
        # D-0077: the signature was recorded correctly all along and
        # emitted nothing, so it read as broken. An instrument nobody
        # can see is one nobody checks.
        ftx = result["guest_ftx_ns"]
        print(
            f"bench:   guest_ftx={ftx / 1e6:.1f} ms "
            f"arp_req={result['guest_arp_req_n']} "
            f"e0_to_e4={result['e0_to_e4_ns'] / 1e6:.1f} ms"
            if isinstance(ftx, int)
            else f"bench:   guest_ftx=- arp_req={result['guest_arp_req_n']}",
            flush=True,
        )
        for ph in result["phases"]:
            phase_rows.append(
                {
                    "batch_id": batch_id,
                    "trial": trial,
                    "warmup": is_warmup,
                    "system": arm.system,
                    "config": config,
                    "phase": ph["phase"],
                    "ticks": ph["ticks"],
                    "ns_since_e2": ph["ns_since_e2"],
                    "delta_ticks": ph["delta_ticks"],
                    "delta_ns": ph["delta_ns"],
                    "source": ph["source"],
                }
            )
        write_csv(out_dir / "runs.csv", RUNS_FIELDS, run_rows)
        write_csv(out_dir / "phases.csv", PHASES_FIELDS, phase_rows)

    for batch_i in range(1, batches + 1):
        batch_id = f"{stamp}-{batch_i}"
        # Warmup: round-robin so neither config is always last-to-cache.
        for w in range(1, warmup + 1):
            for config in cfg_names:
                one_trial(batch_id, config, w, 1)
        for config, trial in recorded_schedule(
            cfg_names, n, warmup, int(shuffle_seed), batch_i
        ):
            one_trial(batch_id, config, trial, 0)

    rc = cmd_summarize(
        argparse.Namespace(
            out_dir=str(out_dir),
            # Campaign kinds always request the two-batch comparison.
            # The old batches>=2 ternary skipped it silently when
            # BENCH_BATCHES=1 produced a one-batch CSV and TEST PASS.
            # A one-batch campaign cannot reach here: the launch gate
            # refuses it, and if it did, summarize fails closed on
            # "<cfg>: need ≥2 batches".
            stability=True if args.kind in CAMPAIGN_KINDS else batches >= 2,
            expect_n=n,
            expect_warmup=warmup,
            f3_scanned=len(run_rows),
            allow_dirty=args.allow_dirty,
            runs=run_rows,
            phases=phase_rows,
            client_timeout_s=client_timeout_s,
            linux_meta=linux_meta,
            canary=canary,
        )
    )
    if args.kind == "fp-ab" and rc == 0:
        print_fp_ab_delta(out_dir)
    return rc

def print_fp_ab_delta(out_dir: Path) -> None:
    runs = read_csv(out_dir / "runs.csv")
    phases = read_csv(out_dir / "phases.csv")
    assert_aggregatable(runs, allow_dirty=True)
    by_cfg: dict[str, dict[str, list[float]]] = {}
    for r in runs:
        if int(r["warmup"]) != 0:
            continue
        by_cfg.setdefault(r["config"], {"e0_to_e4_ns": [], "e2_to_e3g_ns": []})
        by_cfg[r["config"]]["e0_to_e4_ns"].append(float(r["e0_to_e4_ns"]))
    rec = {(r["batch_id"], r["trial"], r["config"]) for r in runs if int(r["warmup"]) == 0}
    for p in phases:
        if int(p["warmup"]) != 0 or p["phase"] != "E3g":
            continue
        if (p["batch_id"], p["trial"], p["config"]) not in rec:
            continue
        by_cfg.setdefault(p["config"], {"e0_to_e4_ns": [], "e2_to_e3g_ns": []})
        by_cfg[p["config"]]["e2_to_e3g_ns"].append(float(p["ns_since_e2"]))
    with_fp = by_cfg.get("release-fast-boot-fp", {})
    no_fp = by_cfg.get("release-fast-boot", {})
    print("## finding 14: -C force-frame-pointers=yes A/B (release+fast-boot)")
    for metric in ("e2_to_e3g_ns", "e0_to_e4_ns"):
        a = with_fp.get(metric, [])
        b = no_fp.get(metric, [])
        if not a or not b:
            print(f"{metric}: missing samples with_fp={len(a)} no_fp={len(b)}")
            continue
        ma, mb = statistics.median(a), statistics.median(b)
        delta = ma - mb
        floor = max(0.02 * max(abs(ma), abs(mb)), 200_000.0)
        vs = "inside stability floor" if abs(delta) <= floor else "above stability floor"
        print(
            f"{metric}: with_fp median={ma:.0f} ns  no_fp median={mb:.0f} ns  "
            f"Δ(with-without)={delta:.0f} ns  floor={floor:.0f} ns  ({vs})"
        )
    print(
        "release measured builds omit the flag (D-0055); debug re-adds it "
        "via scripts/cargo-debug.sh."
    )


def _fmt_corr(label: str, rho: float | None) -> str:
    if rho is None:
        return f"{label}: undefined (constant or n<3)"
    return f"{label}: {rho:.3f}"


def steal_diagnosis(runs: list[dict], phases: list[dict]) -> list[str]:
    """Correlate per-trial steal with latency. Not a stability metric."""
    if not runs or "steal_ticks" not in runs[0]:
        return ["## steal (not recorded in this CSV)", ""]
    rec = [r for r in runs if int(r["warmup"]) == 0]
    if not rec:
        return ["## steal (no recorded trials)", ""]
    steal = [float(r["steal_ticks"]) for r in rec]
    e4 = [float(r["e0_to_e4_ns"]) for r in rec]
    conn = [float(r["e0_to_first_connect_ns"]) for r in rec]
    rec_keys = {(r["batch_id"], r["trial"], r["config"]) for r in rec}
    e3g_by: dict[tuple[str, str, str], float] = {}
    for p in phases:
        if (
            int(p["warmup"]) == 0
            and p["phase"] == "E3g"
            and (p["batch_id"], p["trial"], p["config"]) in rec_keys
        ):
            e3g_by[(p["batch_id"], p["trial"], p["config"])] = float(
                p["ns_since_e2"]
            )
    e3g_pairs = [
        (s, e3g_by[(r["batch_id"], r["trial"], r["config"])])
        for s, r in zip(steal, rec)
        if (r["batch_id"], r["trial"], r["config"]) in e3g_by
    ]
    hz = os.sysconf("SC_CLK_TCK")
    tick_ns = steal_ticks_to_ns(1)
    nonzero = sum(1 for s in steal if s > 0)
    lines = [
        "## steal vs latency (recorded trials; not a stability metric)",
        f"SC_CLK_TCK={hz} steal_tick={tick_ns} ns "
        f"n={len(steal)} nonzero={nonzero} "
        f"median_steal_ticks={statistics.median(steal):.0f} "
        f"max_steal_ticks={max(steal):.0f}",
        _fmt_corr("spearman(steal_ticks, e0_to_e4_ns)", spearman(steal, e4)),
        _fmt_corr(
            "spearman(steal_ticks, e0_to_first_connect_ns)",
            spearman(steal, conn),
        ),
    ]
    if e3g_pairs:
        lines.append(
            _fmt_corr(
                "spearman(steal_ticks, e2_to_e3g_ns)",
                spearman(
                    [s for s, _ in e3g_pairs], [g for _, g in e3g_pairs]
                ),
            )
        )
    order = sorted(range(len(e4)), key=lambda i: e4[i])
    q = max(1, len(e4) // 4)
    slow = [steal[i] for i in order[-q:]]
    rest = [steal[i] for i in order[:-q]]
    lines.append(
        f"slow-quartile e0_to_e4 n={len(slow)} mean_steal_ticks="
        f"{(sum(slow) / len(slow)):.3f}; rest n={len(rest)} mean_steal_ticks="
        f"{(sum(rest) / len(rest)):.3f}"
    )
    if nonzero == 0:
        lines.append(
            f"steal was 0 on every recorded trial. USER_HZ={hz} cannot "
            f"resolve host interference below {tick_ns / 1e6:.1f} ms/tick, "
            "so a sub-tick median shift cannot be confirmed or denied by "
            "this column."
        )
    lines.append("")
    return lines


def cmd_summarize(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    runs = getattr(args, "runs", None) or read_csv(out_dir / "runs.csv")
    phases = getattr(args, "phases", None) or read_csv(out_dir / "phases.csv")
    schema = runs_schema(runs[0].keys()) if runs else runs_schema(RUNS_FIELDS)
    assert_aggregatable(runs, allow_dirty=getattr(args, "allow_dirty", False))
    expect_n = getattr(args, "expect_n", None)
    expect_warmup = getattr(args, "expect_warmup", None)
    lines = [
        "# bench summary (D-0055): n / median / IQR / min / max; warmup excluded",
        f"qemu_version={runs[0]['qemu_version']}",
        f"qemu_hash={runs[0]['qemu_hash']}",
    ]
    if schema == "new":
        require_first_connect_control(runs)
        lines.extend(s_header_lines(runs))
    lines.extend(canary_header_lines(getattr(args, "canary", None)))
    lines.extend(
        linux_header_lines(
            client_timeout_s=getattr(args, "client_timeout_s", None),
            linux_meta=getattr(args, "linux_meta", None),
        )
    )
    lines.extend(
        [
            f"git_sha={runs[0]['git_sha']} dirty={runs[0]['dirty']}",
            f"host_kernel={runs[0]['host_kernel']}",
            f"cpu_model={runs[0]['cpu_model']}",
            f"governor={runs[0]['governor']} "
            f"smt_control={runs[0].get('smt_control', 'absent')} "
            f"cpufreq_boost={runs[0].get('cpufreq_boost', 'absent')} "
            f"virt={runs[0].get('virt', 'absent')} "
            f"steal_start_ticks={runs[0].get('steal_start_ticks', 'absent')} "
            f"loadavg_1m={runs[0]['loadavg_1m']}",
            f"client_granularity_ns={runs[0]['client_granularity_ns']}",
            "",
        ]
    )
    if "shuffle_seed" in runs[0]:
        lines.insert(-1, f"shuffle_seed={runs[0]['shuffle_seed']}")
    f3_scanned = getattr(args, "f3_scanned", None)
    if f3_scanned is not None:
        # Zero by construction when this summary exists: a hit raises at
        # parse time (fail-closed) and records a gate-failure row. The
        # line documents that the scan ran and its coverage.
        lines.insert(
            -1,
            f"falsifier3_mtrap: 0 hits in {f3_scanned} trial serials + "
            "canary (computed per boot, fail-closed at first hit)",
        )
    oh_vals = [
        float(p["delta_ns"])
        for p in phases
        if p.get("phase") == "stamp_b" and int(p.get("warmup", "0")) == 0
    ]
    if oh_vals:
        lines.insert(
            -1,
            f"stamp_overhead_ns={statistics.median(oh_vals):.0f} "
            f"(floor max(that, {TICK_NS} ns); not a stability metric)",
        )
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for r in runs:
        key = (r["batch_id"], r["system"], r["config"])
        groups.setdefault(key, []).append(r)
    phase_groups: dict[tuple[str, str, str], list[dict]] = {}
    for p in phases:
        key = (p["batch_id"], p["system"], p["config"])
        phase_groups.setdefault(key, []).append(p)

    metric_by_group: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    for key, rs in sorted(groups.items()):
        rec = [r for r in rs if int(r["warmup"]) == 0]
        if expect_n is not None and len(rec) != expect_n:
            raise BenchFail(
                f"TEST FAIL: {key} has {len(rec)} recorded trials, want {expect_n}"
            )
        if expect_warmup is not None and len(rs) - len(rec) != expect_warmup:
            raise BenchFail(
                f"TEST FAIL: {key} has {len(rs) - len(rec)} warmup rows, "
                f"want {expect_warmup}"
            )
        mets = metric_table(rs, phase_groups.get(key, []), schema)
        metric_by_group[key] = mets
        title = f"{key[1]} {key[2]} batch={key[0]} n_recorded={len(rec)}"
        lines.extend(summarize_group(title, mets))
        lines.append("")

    lines.extend(steal_diagnosis(runs, phases))

    failed: list[str] = []
    failed.extend(trimmed_vs_stock_failures(metric_by_group))
    failed.extend(linux_kernel_hash_failures(runs))
    if getattr(args, "stability", False):
        by_cfg: dict[str, list[tuple[str, dict[str, list[float]]]]] = {}
        for (batch, _sys, cfg), mets in metric_by_group.items():
            by_cfg.setdefault(cfg, []).append((batch, mets))
        lines.append(
            "## stability (two interleaved batches, metrics ≥ 1 ms; "
            "not within-batch arm comparison)"
        )
        n_pass = 0
        for cfg, items in sorted(by_cfg.items()):
            items.sort()
            if len(items) < 2:
                failed.append(f"{cfg}: need ≥2 batches, have {len(items)}")
                lines.append(f"{cfg}: FAIL (need ≥2 batches)")
                continue
            a, b = items[-2], items[-1]
            bad = compare_stability(a[1], b[1])
            if bad:
                # D-0079 reader fix: a failing arm previously got no
                # verdict token in this section (detail went to stderr
                # at the end), so a reader of the section alone saw
                # only PASS lines — a short list that reads complete.
                # The section now carries every arm's verdict and a
                # totals line, so truncation is visible.
                lines.append(
                    f"{cfg}: {a[0]} vs {b[0]} FAIL "
                    f"({len(bad)} metrics; detail in TEST FAIL below)"
                )
                failed.append(f"{cfg} {a[0]} vs {b[0]}:")
                failed.extend("  " + x for x in bad)
            else:
                n_pass += 1
                lines.append(f"{cfg}: {a[0]} vs {b[0]} PASS")
        lines.append(f"stability: {n_pass}/{len(by_cfg)} arms PASS")
        lines.append("")

    text = "\n".join(lines) + "\n"
    (out_dir / "summary.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    if failed:
        print("TEST FAIL: summarize gates not met", file=sys.stderr)
        print("\n".join(failed), file=sys.stderr)
        print(
            "Not widening the criterion (D-0055). Varying metrics listed above.",
            file=sys.stderr,
        )
        return 1
    print("TEST PASS: bench summary")
    return 0


def cmd_check_serial(args: argparse.Namespace) -> int:
    path = Path(args.serial)
    if not path.is_file():
        raise BenchFail(f"TEST FAIL: serial log missing: {path}")
    text = path.read_bytes().decode("utf-8", errors="replace")
    rows = parse_phases(text)
    overhead = stamp_overhead_ns(rows)
    e3g = next(r for r in rows if r["phase"] == "E3g")
    print(
        f"TEST PASS: phase deltas sum to E2→E3g "
        f"({int(e3g['ns_since_e2'])} ns) within stamp overhead {overhead} ns"
    )
    return 0


def cmd_check_linux_artifacts(args: argparse.Namespace) -> int:
    names = list(args.names) if args.names else None
    verify_linux_artifacts(names)
    print("TEST PASS: linux artifacts match MANIFEST")
    return 0


def _write_fixture_runs(path: Path, rows: list[dict]) -> None:
    base = {
        "batch_id": "fix-1",
        "trial": 1,
        "warmup": 0,
        "system": "whimbrel",
        "config": "release-fast-boot",
        "git_sha": "abc",
        "dirty": 0,
        "kernel_sha256": "k",
        "bios_sha256": "",
        "qemu_version": "QEMU emulator version 8.2.2",
        "qemu_hash": "h",
        "host_kernel": "6.12",
        "cpu_model": "test",
        "governor": "performance",
        "smt_control": "off",
        "cpufreq_boost": "0",
        "virt": "none",
        "steal_start_ticks": 0,
        "loadavg_1m": "0.00",
        "canary_stvec_ns": 1_030_000,
        "canary_page_verify_ns": 11_900_000,
        "qemu_cpu": 2,
        "client_cpu": 3,
        "client_granularity_ns": 1000000,
        "shuffle_seed": 1,
        "run_order": 1,
        "steal_ticks": 0,
        "steal_ns": 0,
        "e0_mono_ns": 0,
        "e0_wall_ns": 0,
        "e0_to_first_connect_ns": 10_000_000,
        "e0_to_e4_ns": 50_000_000,
        "w_ns": 25_000_000,
        "d_ack_ns": 40_000,
        "d_fin_ns": 150_000,
        "synack_to_http_ns": 1_000_000,
        "guest_ftx_ns": 237_000_000,
        "guest_arp_req_n": 1,
        "attempts": 12,
        "pcap_path": "x.pcap",
    }
    write_csv(path, RUNS_FIELDS, [{**base, **r} for r in rows])


# D-0074 item 4 / D-0075. A lost solicit leaves the wire looking normal
# — one ARP request, correct order — and shows only as a late first
# guest TX. So the threshold cannot live in the CSV: guest_ftx_ns
# carries the whole guest boot, which differs per arm. Classify per
# arm at analysis time instead, against that arm's own median.
#
# 20 ms: the within-arm clean spread is ~1 ms (D-0074, 525 clean boots
# at 12.30–12.73 ms margin, first TX inside 1 ms), the post-fix heal is
# ~52 ms and the pre-fix heal ~1029 ms. Nothing observed lies between.
ARP_LOSS_MARGIN_NS = 20_000_000


def arp_signature(runs: list[dict]) -> list[dict]:
    """Per-arm outliers in guest_ftx_ns. Counts events; never drops them."""
    if not runs or "guest_ftx_ns" not in runs[0]:
        raise BenchFail(
            "TEST FAIL: runs.csv has no guest_ftx_ns column "
            "(pre-D-0075 batch; the loss signature was not recorded)"
        )
    by_arm: dict[tuple[str, str], list[dict]] = {}
    for r in runs:
        by_arm.setdefault((r["system"], r["config"]), []).append(r)
    out: list[dict] = []
    for (system, config), rows in sorted(by_arm.items()):
        vals = sorted(
            int(r["guest_ftx_ns"]) for r in rows if r["guest_ftx_ns"] != ""
        )
        if not vals:
            continue
        med = vals[len(vals) // 2]
        for r in rows:
            if r["guest_ftx_ns"] == "":
                continue
            ftx = int(r["guest_ftx_ns"])
            if ftx - med > ARP_LOSS_MARGIN_NS:
                out.append(
                    {
                        "system": system,
                        "config": config,
                        "batch_id": r["batch_id"],
                        "trial": r["trial"],
                        "warmup": r["warmup"],
                        "guest_ftx_ns": ftx,
                        "arm_median_ns": med,
                        "excess_ns": ftx - med,
                        "guest_arp_req_n": r.get("guest_arp_req_n", ""),
                    }
                )
    return out


def cmd_arp_signature(args: argparse.Namespace) -> int:
    runs = read_csv(Path(args.runs))
    events = arp_signature(runs)
    print(f"trials {len(runs)}   loss-signature events {len(events)}")
    for e in events:
        print(
            f"  {e['system']}/{e['config']} batch={e['batch_id']} "
            f"trial={e['trial']} warmup={e['warmup']} "
            f"excess={e['excess_ns'] / 1e6:.1f} ms "
            f"(ftx {e['guest_ftx_ns'] / 1e6:.1f} ms vs arm median "
            f"{e['arm_median_ns'] / 1e6:.1f} ms) "
            f"arp_req={e['guest_arp_req_n']}"
        )
    return 0


def cmd_selftest(_args: argparse.Namespace) -> int:
    fired = []

    os.environ["BENCH_TSHARK"] = "/no/such/tshark"
    try:
        require_tshark()
        raise BenchFail("missing tshark did not fire")
    except BenchFail as e:
        if "tshark not installed" not in str(e):
            raise
        fired.append(f"missing tshark: {e}")
    finally:
        os.environ.pop("BENCH_TSHARK", None)

    try:
        parse_phases("PHASE E3g ticks=notanumber ns=0 since_start=0 ns=0 delta=0 ns=0\n")
        raise BenchFail("malformed PHASE did not fire")
    except BenchFail as e:
        if "malformed PHASE line" not in str(e):
            raise
        fired.append(f"malformed PHASE: {e}")

    try:
        parse_phases("PHASE E3g unset\n")
        raise BenchFail("unset PHASE did not fire")
    except BenchFail as e:
        if "unset" not in str(e):
            raise
        fired.append(f"unset PHASE: {e}")

    # D-0055 methodology amendment: gates carry their failure mode.
    try:
        falsifier3_scan("Z P D C T V M\nM! 0000000000000002 ffffffff80200000\n")
        raise BenchFail("M! diagnostic did not fire")
    except BenchFail as e:
        if "falsifier 3" not in str(e):
            raise
        fired.append(f"falsifier 3 M!: {e}")
    falsifier3_scan("PHASE E3g ok\nHTTP OK\n")  # clean serial passes

    try:
        require_registered_counts("t47", 5, 3, 2)
        raise BenchFail("nonconforming n did not fire")
    except BenchFail as e:
        if "BENCH_N" not in str(e):
            raise
        fired.append(f"nonconforming counts: {e}")
    try:
        require_registered_counts("t47", 30, 3, 1)
        raise BenchFail("nonconforming batches did not fire")
    except BenchFail as e:
        if "BENCH_BATCHES" not in str(e):
            raise
        fired.append(f"nonconforming batches: {e}")
    require_registered_counts("t47", 30, 3, 2)  # registered shape passes
    require_registered_counts("fp-ab", 5, 1, 1)  # diagnostic kind exempt

    tmp = ROOT / "results" / "selftest"
    tmp.mkdir(parents=True, exist_ok=True)
    empty = tmp / "zero-runs.csv"
    write_csv(empty, RUNS_FIELDS, [])
    try:
        assert_aggregatable(read_csv(empty))
        raise BenchFail("zero-trial CSV did not fire")
    except BenchFail as e:
        if "zero-trial CSV" not in str(e):
            raise
        fired.append(f"zero-trial CSV: {e}")

    mismatch = tmp / "mismatch-runs.csv"
    _write_fixture_runs(
        mismatch,
        [
            {"trial": 1, "qemu_version": "QEMU emulator version 8.2.2"},
            {"trial": 2, "qemu_version": "QEMU emulator version 9.0.0"},
        ],
    )
    try:
        assert_aggregatable(read_csv(mismatch))
        raise BenchFail("version mismatch did not fire")
    except BenchFail as e:
        if "QEMU version mismatch" not in str(e):
            raise
        fired.append(f"version mismatch: {e}")

    dirty = tmp / "dirty-runs.csv"
    _write_fixture_runs(dirty, [{"trial": 1, "dirty": 1}])
    try:
        assert_aggregatable(read_csv(dirty))
        raise BenchFail("dirty tree did not fire")
    except BenchFail as e:
        if "dirty-tree" not in str(e):
            raise
        fired.append(f"dirty tree: {e}")

    sha_mis = tmp / "sha-runs.csv"
    _write_fixture_runs(
        sha_mis,
        [
            {"trial": 1, "git_sha": "aaa"},
            {"trial": 2, "git_sha": "bbb"},
        ],
    )
    try:
        assert_aggregatable(read_csv(sha_mis))
        raise BenchFail("git SHA mismatch did not fire")
    except BenchFail as e:
        if "git SHA mismatch" not in str(e):
            raise
        fired.append(f"git SHA mismatch: {e}")

    good_serial = (
        "PHASE ticks (10 MHz, 100 ns/tick); ns = ticks * 100\n"
        "PHASE _start ticks=100 ns=10000 since_start=0 ns=0 delta=0 ns=0\n"
        "PHASE stamp_a ticks=110 ns=11000 since_start=10 ns=1000 delta=10 ns=1000\n"
        "PHASE stamp_b ticks=111 ns=11100 since_start=11 ns=1100 delta=1 ns=100\n"
        "PHASE E3g ticks=200 ns=20000 since_start=100 ns=10000 delta=89 ns=8900\n"
    )
    rows = parse_phases(good_serial)
    if [r["phase"] for r in rows] != ["_start", "stamp_a", "stamp_b", "E3g"]:
        raise BenchFail(f"good PHASE parse unexpected: {rows}")
    if stamp_overhead_ns(rows) != 100:
        raise BenchFail("stamp overhead parse unexpected")

    bad_sum = (
        "PHASE ticks (10 MHz, 100 ns/tick); ns = ticks * 100\n"
        "PHASE _start ticks=100 ns=10000 since_start=0 ns=0 delta=0 ns=0\n"
        "PHASE stamp_a ticks=110 ns=11000 since_start=10 ns=1000 delta=10 ns=1000\n"
        "PHASE stamp_b ticks=111 ns=11100 since_start=11 ns=1100 delta=1 ns=100\n"
        "PHASE E3g ticks=200 ns=20000 since_start=100 ns=10000 delta=50 ns=5000\n"
    )
    try:
        parse_phases(bad_sum)
        raise BenchFail("phase-sum mismatch did not fire")
    except BenchFail as e:
        if "phase deltas sum" not in str(e):
            raise
        fired.append(f"phase-sum mismatch: {e}")

    if steal_ticks_from_stat("cpu  1 0 2 3 4 5 6 7 8 9\ncpu0 0 0 0 0 0 0 0 0 0 0\n") != 7:
        raise BenchFail("steal column parse unexpected")
    try:
        steal_ticks_from_stat("cpu  1 2 3\n")
        raise BenchFail("short /proc/stat cpu line did not fire")
    except BenchFail as e:
        if "no steal column" not in str(e):
            raise
        fired.append(f"short steal column: {e}")
    live = read_steal_ticks()
    if live < 0:
        raise BenchFail("live steal ticks negative")
    fired.append(f"live /proc/stat steal ticks={live}")

    good_ctrl = {
        "governor": "performance",
        "smt_control": "off",
        "cpufreq_boost": "0",
        "virt": "none",
        "steal_start_ticks": 0,
    }
    require_host_controls(good_ctrl)
    fired.append("host controls accept a dedicated-host snapshot")
    for key, bad in (
        ("governor", "powersave"),
        ("smt_control", "on"),
        ("cpufreq_boost", "1"),
        ("virt", "kvm"),
        ("steal_start_ticks", 1),
        ("governor", "unavailable"),
    ):
        try:
            require_host_controls({**good_ctrl, key: bad})
            raise BenchFail(f"host control {key}={bad!r} did not fire")
        except BenchFail as e:
            want = f"host control {key}="
            if want not in str(e):
                raise
            fired.append(f"host control {key}={bad!r}: {e}")

    ctrl_mis = tmp / "ctrl-runs.csv"
    _write_fixture_runs(
        ctrl_mis,
        [
            {"trial": 1, "virt": "none"},
            {"trial": 2, "virt": "kvm"},
        ],
    )
    try:
        assert_aggregatable(read_csv(ctrl_mis))
        raise BenchFail("virt mismatch did not fire")
    except BenchFail as e:
        if "virt mismatch" not in str(e):
            raise
        fired.append(f"virt mismatch: {e}")

    require_origin_sync(
        {"branch": "m4-evaluation", "head": "abc", "origin": "abc"}
    )
    fired.append("origin sync accepts HEAD == origin/<branch>")
    try:
        require_origin_sync(
            {
                "branch": "m4-evaluation",
                "head": "aaa111",
                "origin": "bbb222",
            }
        )
        raise BenchFail("origin mismatch did not fire")
    except BenchFail as e:
        msg = str(e)
        if "HEAD aaa111" not in msg or "origin/m4-evaluation bbb222" not in msg:
            raise
        fired.append(f"origin mismatch: {e}")

    sched_a = recorded_schedule(["a", "b"], 5, 3, 42, 1)
    sched_b = recorded_schedule(["a", "b"], 5, 3, 42, 1)
    if sched_a != sched_b:
        raise BenchFail("recorded_schedule is not deterministic")
    expected_pairs = {(c, t) for c in ("a", "b") for t in range(4, 9)}
    if set(sched_a) != expected_pairs:
        raise BenchFail(f"recorded_schedule lost pairs: {sched_a}")
    sequential = [(c, t) for c in ("a", "b") for t in range(4, 9)]
    if sched_a == sequential:
        raise BenchFail("recorded_schedule did not shuffle (seed 42)")
    fired.append(f"recorded_schedule shuffled: {sched_a}")

    def expect_fail(fn, needle: str, label: str) -> None:
        try:
            fn()
            raise BenchFail(f"{label} did not fire")
        except BenchFail as e:
            if needle not in str(e):
                raise
            fired.append(f"{label}: {e}")

    if "e0_to_e3w_ns" in RUNS_FIELDS:
        raise BenchFail("TEST FAIL: RUNS_FIELDS still has e0_to_e3w_ns")
    for col in ("w_ns", "d_ack_ns", "d_fin_ns"):
        if col not in RUNS_FIELDS:
            raise BenchFail(f"TEST FAIL: RUNS_FIELDS missing {col}")

    new_csv = tmp / "new-schema.csv"
    _write_fixture_runs(new_csv, [{"trial": 1}])
    new_rows = read_csv(new_csv)
    if "e0_to_e3w_ns" in new_rows[0]:
        raise BenchFail("TEST FAIL: selftest row still has e0_to_e3w_ns")
    if runs_schema(new_rows[0].keys()) != "new":
        raise BenchFail("TEST FAIL: fixture schema is not new")
    fired.append("new-schema fixture has w_ns/d_ack_ns/d_fin_ns, no e0_to_e3w_ns")

    mixed_fields = list(RUNS_FIELDS) + ["e0_to_e3w_ns"]
    mixed = tmp / "mixed-schema.csv"
    write_csv(mixed, mixed_fields, [{**new_rows[0], "e0_to_e3w_ns": 11_000_000}])
    expect_fail(
        lambda: runs_schema(read_csv(mixed)[0].keys()),
        "mixed runs.csv schema",
        "mixed schema",
    )
    expect_fail(
        lambda: assert_aggregatable(read_csv(mixed)),
        "mixed runs.csv schema",
        "mixed schema via aggregate",
    )

    neither_fields = [
        f for f in RUNS_FIELDS if f not in ("w_ns", "d_ack_ns", "d_fin_ns")
    ]
    neither = tmp / "neither-schema.csv"
    write_csv(neither, neither_fields, [{k: new_rows[0][k] for k in neither_fields}])
    expect_fail(
        lambda: runs_schema(read_csv(neither)[0].keys()),
        "incomplete runs.csv schema",
        "neither schema",
    )

    old_fields = neither_fields + ["e0_to_e3w_ns"]
    old_csv = tmp / "old-schema.csv"
    write_csv(
        old_csv,
        old_fields,
        [{**{k: new_rows[0][k] for k in neither_fields}, "e0_to_e3w_ns": 11_000_000}],
    )
    if runs_schema(read_csv(old_csv)[0].keys()) != "old":
        raise BenchFail(
            "TEST FAIL: historical e0_to_e3w_ns-only header is not old schema"
        )
    fired.append("old-schema header (e0_to_e3w_ns, no W/D_*) still detects as old")

    partial_fields = [
        f for f in RUNS_FIELDS if f not in ("d_ack_ns", "d_fin_ns")
    ] + ["e0_to_e3w_ns"]
    partial = tmp / "partial-schema.csv"
    write_csv(
        partial,
        partial_fields,
        [
            {
                **{k: new_rows[0][k] for k in partial_fields if k != "e0_to_e3w_ns"},
                "e0_to_e3w_ns": 11_000_000,
            }
        ],
    )
    expect_fail(
        lambda: runs_schema(read_csv(partial)[0].keys()),
        "mixed runs.csv schema",
        "partial new columns with e0_to_e3w_ns",
    )

    tshark = require_tshark()
    frames = _D0070._selftest_frames()

    def write_frames(path: Path, chosen: list[tuple[bytes, int]]) -> None:
        _D0070._write_pcap(path, chosen)

    def retimed(times: list[int]) -> list[tuple[bytes, int]]:
        return [(pkt, t) for (pkt, _), t in zip(frames, times)]

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        ok = td_path / "ok.pcap"
        write_frames(ok, frames)
        got = require_pcap_intervals(ok, tshark, system="whimbrel")
        want = {
            "w_ns": 30_000_000,
            "d_ack_ns": 36_000,
            "d_fin_ns": 212_000,
            "synack_to_http_ns": 1_000_000,
            "http_len": 92,
            "guest_ftx_ns": 20_000_000,
            "guest_arp_req_n": 1,
        }
        if got != want:
            raise BenchFail(f"TEST FAIL: imported extract {got} want {want}")
        fired.append("imported D-0070 extract on synthetic pcap")

        empty = td_path / "empty.pcap"
        empty.write_bytes(b"")
        expect_fail(
            lambda: require_pcap_intervals(empty, tshark, system="whimbrel"),
            "pcap missing or empty",
            "empty pcap",
        )
        missing = td_path / "no-such.pcap"
        expect_fail(
            lambda: require_pcap_intervals(missing, tshark, system="whimbrel"),
            "pcap missing or empty",
            "missing pcap",
        )

        # frames = [slirp ARP, guest ARP request, SYN/ACK, HTTP, ACK, FIN]
        dropped = [
            ("no slirp ARP", frames[1:]),
            ("no guest SYN/ACK", frames[:2] + frames[3:]),
            ("no HTTP 200", frames[:3] + frames[4:]),
            ("no pure ACK", frames[:4] + frames[5:]),
            ("no client FIN", frames[:5]),
        ]
        for i, (needle, chosen) in enumerate(dropped):
            p = td_path / f"drop-{i}.pcap"
            write_frames(p, chosen)
            expect_fail(
                lambda p=p, needle=needle: require_pcap_intervals(
                    p, tshark, system="whimbrel"
                ),
                needle,
                f"missing {needle}",
            )

        arp_f, garp_f, syn_f, http_f, ack_f, fin_f = frames

        # The signature is passive: a pcap with no guest ARP request
        # still extracts, and records zero. It can never fail a trial.
        no_garp = td_path / "no-guest-arp.pcap"
        write_frames(no_garp, frames[:1] + frames[2:])
        got_no_garp = require_pcap_intervals(no_garp, tshark, system="whimbrel")
        if got_no_garp["guest_arp_req_n"] != 0:
            raise BenchFail(
                "TEST FAIL: guest_arp_req_n "
                f"{got_no_garp['guest_arp_req_n']} want 0 without a guest ARP"
            )
        if got_no_garp["guest_ftx_ns"] != 30_000_000:
            raise BenchFail(
                "TEST FAIL: guest_ftx_ns "
                f"{got_no_garp['guest_ftx_ns']} want 30000000 (the SYN/ACK)"
            )
        fired.append("guest ARP-loss signature is passive (absent → 0, no fail)")

        # D-0077: the anchor must ignore a guest frame that is neither
        # ARP nor IPv4 — stock's IPv6 DAD solicit preceded the ARP on 1
        # boot in 92 and was read as 6.45 ms of delivery delay.
        guest_mac = bytes.fromhex("525400123456")
        v6 = (bytes.fromhex("3333ff123456") + guest_mac
              + bytes.fromhex("86dd") + bytes(46))
        # An inbound SYN toward :80; the shared fixture has only the
        # guest's SYN/ACK. eth(14) + ip(20) + 13 = the TCP flags byte.
        syn_in = bytearray(frames[4][0])
        syn_in[47] = 0x02
        v6_pcap = td_path / "ipv6-first.pcap"
        write_frames(v6_pcap, [
            (frames[0][0], 0),          # slirp ARP
            (v6, 12_000),               # guest ICMPv6 (the D-0077 shape)
            (frames[1][0], 20_000),     # guest ARP request
            (bytes(syn_in), 20_500),    # SYN flushed 500 µs later
            (frames[2][0], 31_000),     # SYN/ACK
        ])
        # New anchor: the ARP, so the gate passes at 500 µs.
        dt = assert_syn_grid(v6_pcap, tshark)
        if abs(dt - 0.0005) > 1e-6:
            raise BenchFail(
                f"TEST FAIL: SYN-grid anchored on the IPv6 frame "
                f"(dt={dt:.6f}s, want 0.000500 from the guest ARP)"
            )
        # Old anchor: the IPv6 frame, so the same trial would have
        # failed at 8.5 ms. The fixture must reproduce that, or it is
        # not testing D-0077.
        old_rows = tshark_table(v6_pcap, tshark, f"eth.src != {SLIRP_MAC}")
        old_dt = 0.0205 - _time_s(old_rows[0])
        if not (old_rows and abs(old_dt - 0.0085) < 1e-6):
            raise BenchFail(
                "TEST FAIL: fixture does not reproduce the D-0077 shape "
                f"(old-anchor dt={old_dt:.6f}s, want 0.008500)"
            )
        fired.append(
            "SYN-grid anchor ignores a non-IPv4 guest frame: 0.5 ms new "
            "vs 8.5 ms old on the same pcap (D-0077)"
        )

        # A gate that raises must still leave a record.
        gf = td_path / "gate-failures.csv"
        record_gate_failure(
            batch_id="b", trial=29, is_warmup=0, system="linux",
            config="stock", run_order=125, pcap=v6_pcap,
            gate="TEST FAIL: SYN-grid: t(SYN)-t(guest first TX)=0.006454s",
            out_path=gf,
        )
        gf_rows = read_csv(gf)
        if len(gf_rows) != 1 or gf_rows[0]["trial"] != "29":
            raise BenchFail(f"TEST FAIL: gate failure not recorded: {gf_rows}")
        if gf_rows[0]["guest_arp_req_n"] != "1":
            raise BenchFail(
                "TEST FAIL: gate-failure row lost the passive signature: "
                f"{gf_rows[0]}"
            )
        record_gate_failure(
            batch_id="b", trial=30, is_warmup=0, system="linux",
            config="stock", run_order=126, pcap=td_path / "no-such.pcap",
            gate="TEST FAIL: pcap missing", out_path=gf,
        )
        if len(read_csv(gf)) != 2:
            raise BenchFail(
                "TEST FAIL: recorder dropped a row when the pcap was absent; "
                "diagnostics must not need a gate to pass"
            )
        fired.append(
            "gate-failing trial is recorded before the raise, pcap or not"
        )
        order_cases = [
            (
                "SYN/ACK before slirp ARP",
                [
                    (syn_f[0], 0),
                    (arp_f[0], 30_000),
                    (http_f[0], 31_000),
                    (ack_f[0], 31_036),
                    (fin_f[0], 31_212),
                ],
            ),
            (
                "HTTP frame before SYN/ACK",
                [
                    (arp_f[0], 0),
                    (http_f[0], 30_000),
                    (syn_f[0], 31_000),
                    (ack_f[0], 31_036),
                    (fin_f[0], 31_212),
                ],
            ),
            (
                "ACK-of-response before HTTP",
                [
                    (arp_f[0], 0),
                    (syn_f[0], 30_000),
                    (http_f[0], 31_036),
                    (ack_f[0], 31_000),
                    (fin_f[0], 31_212),
                ],
            ),
            (
                "client FIN before HTTP",
                [
                    (arp_f[0], 0),
                    (syn_f[0], 30_000),
                    (http_f[0], 31_000),
                    (ack_f[0], 31_036),
                    (fin_f[0], 30_900),
                ],
            ),
        ]
        for i, (needle, chosen) in enumerate(order_cases):
            p = td_path / f"order-{i}.pcap"
            write_frames(p, chosen)
            expect_fail(
                lambda p=p, needle=needle: require_pcap_intervals(
                    p, tshark, system="whimbrel"
                ),
                needle,
                needle,
            )

        long_fin = td_path / "long-fin.pcap"
        write_frames(long_fin, retimed([0, 20_000, 30_000, 31_000, 31_036, 41_000]))
        linux_long = require_pcap_intervals(long_fin, tshark, system="linux")
        if linux_long["d_fin_ns"] != 10_000_000:
            raise BenchFail(
                f"TEST FAIL: long-fin d_fin_ns={linux_long['d_fin_ns']} want 10000000"
            )
        fired.append("linux d_fin_ns ≥ 10 ms is recorded, not a tripwire")
        expect_fail(
            lambda: require_pcap_intervals(long_fin, tshark, system="whimbrel"),
            "d_fin_ns=",
            "whimbrel d_fin_ns ≥ 10 ms",
        )

    orig_extract = _D0070.extract_pcap

    def fake_len(_pcap, _tshark, **_kw):
        return {
            "w_ns": 1,
            "d_ack_ns": 1,
            "d_fin_ns": 1,
            "synack_to_http_ns": 1,
            "http_len": 91,
        }

    _D0070.extract_pcap = fake_len
    try:
        expect_fail(
            lambda: require_pcap_intervals(
                Path("/dev/null"), "tshark", system="whimbrel"
            ),
            "HTTP tcp.len=91 want 92",
            "HTTP tcp.len ≠ 92",
        )
    finally:
        _D0070.extract_pcap = orig_extract

    def fake_neg_w(_pcap, _tshark, **_kw):
        return {
            "w_ns": -1,
            "d_ack_ns": 1,
            "d_fin_ns": 1,
            "synack_to_http_ns": 1,
            "http_len": 92,
        }

    _D0070.extract_pcap = fake_neg_w
    try:
        expect_fail(
            lambda: require_pcap_intervals(
                Path("/dev/null"), "tshark", system="whimbrel"
            ),
            "w_ns is negative",
            "negative w_ns",
        )
    finally:
        _D0070.extract_pcap = orig_extract

    def fake_neg_ack(_pcap, _tshark, **_kw):
        return {
            "w_ns": 1,
            "d_ack_ns": -2,
            "d_fin_ns": 1,
            "synack_to_http_ns": 1,
            "http_len": 92,
        }

    _D0070.extract_pcap = fake_neg_ack
    try:
        expect_fail(
            lambda: require_pcap_intervals(
                Path("/dev/null"), "tshark", system="whimbrel"
            ),
            "d_ack_ns is negative",
            "negative d_ack_ns",
        )
    finally:
        _D0070.extract_pcap = orig_extract

    def fake_neg_fin(_pcap, _tshark, **_kw):
        return {
            "w_ns": 1,
            "d_ack_ns": 1,
            "d_fin_ns": -3,
            "synack_to_http_ns": 1,
            "http_len": 92,
        }

    _D0070.extract_pcap = fake_neg_fin
    try:
        expect_fail(
            lambda: require_pcap_intervals(
                Path("/dev/null"), "tshark", system="whimbrel"
            ),
            "d_fin_ns is negative",
            "negative d_fin_ns",
        )
    finally:
        _D0070.extract_pcap = orig_extract

    require_first_connect_control(
        [
            {
                "warmup": 0,
                "system": "whimbrel",
                "config": SAFE_CONFIG,
                "e0_to_first_connect_ns": 18_500_000,
            },
            {
                "warmup": 0,
                "system": "whimbrel",
                "config": FAST_CONFIG,
                "e0_to_first_connect_ns": 18_600_000,
            },
        ]
    )
    fired.append("first-connect control accepts |safe − fast| ≤ 1 ms")
    expect_fail(
        lambda: require_first_connect_control(
            [
                {
                    "warmup": 0,
                    "system": "whimbrel",
                    "config": SAFE_CONFIG,
                    "e0_to_first_connect_ns": 18_500_000,
                },
                {
                    "warmup": 0,
                    "system": "whimbrel",
                    "config": FAST_CONFIG,
                    "e0_to_first_connect_ns": 20_000_000,
                },
            ]
        ),
        "first-connect control |safe − fast|",
        "first-connect |safe − fast| > 1 ms",
    )
    expect_fail(
        lambda: require_first_connect_control(
            [
                {
                    "warmup": 0,
                    "system": "whimbrel",
                    "config": FAST_CONFIG,
                    "e0_to_first_connect_ns": 18_500_000,
                },
                {
                    "warmup": 0,
                    "system": "linux",
                    "config": "linux-trimmed",
                    "e0_to_first_connect_ns": 20_000_000,
                },
            ]
        ),
        "first-connect control |linux − whimbrel|",
        "first-connect cross-system > 1 ms",
    )

    def s_pair(safe_e4: int, fast_e4: int) -> list[dict]:
        common = {
            "warmup": 0,
            "system": "whimbrel",
            "w_ns": 25_000_000,
            "d_fin_ns": 150_000,
            "synack_to_http_ns": 1_000_000,
            "e0_to_first_connect_ns": 18_500_000,
        }
        return [
            {**common, "config": SAFE_CONFIG, "e0_to_e4_ns": safe_e4, "trial": 1},
            {**common, "config": FAST_CONFIG, "e0_to_e4_ns": fast_e4, "trial": 2},
        ]

    s_ok = s_header_lines(s_pair(51_450_000, 51_450_000))
    if not s_ok[0].startswith("s_ns=6800000 ") or "n=2" not in s_ok[0]:
        raise BenchFail(f"TEST FAIL: unexpected s_ns header {s_ok}")
    if s_ok[1] != "s_ns_fast=6800000 s_ns_safe=6800000":
        raise BenchFail(f"TEST FAIL: unexpected s_ns_fast/safe header {s_ok[1]}")
    fired.append("s_ns header pools both configs; |fast − safe| ≤ 1 ms")
    expect_fail(
        lambda: s_header_lines(s_pair(51_450_000, 52_650_000)),
        "|s_ns_fast − s_ns_safe|",
        "|s_ns_fast − s_ns_safe| > 1 ms",
    )

    empty_linux = phases_from_serial("READY\nLINUX INIT OK\n", "linux")
    if empty_linux != []:
        raise BenchFail(f"linux PHASE skip unexpected: {empty_linux}")
    fired.append("linux serial skips PHASE-presence")
    try:
        phases_from_serial("READY\n", "whimbrel")
        raise BenchFail("whimbrel empty PHASE did not fire")
    except BenchFail as e:
        if "no PHASE rows" not in str(e):
            raise
        fired.append(f"whimbrel PHASE-presence: {e}")

    w_arm = Arm("release-fast-boot", "whimbrel")
    w_extra = guest_qemu_extra(w_arm, Path("/k"), None, None)
    if w_extra != ["-kernel", "/k"]:
        raise BenchFail(f"Whimbrel argv unexpected: {w_extra}")
    if "-initrd" in w_extra or "-append" in w_extra:
        raise BenchFail("Whimbrel argv took -initrd or -append")
    l_arm = Arm(
        "trimmed", "linux", linux_image="Image-trimmed", linux_append="quiet"
    )
    l_extra = guest_qemu_extra(
        l_arm, Path("/Image"), Path("/rootfs.cpio"), LINUX_APPEND_QUIET
    )
    if l_extra != [
        "-kernel",
        "/Image",
        "-initrd",
        "/rootfs.cpio",
        "-append",
        LINUX_APPEND_QUIET,
    ]:
        raise BenchFail(f"Linux argv unexpected: {l_extra}")
    fired.append("per-system argv: Whimbrel -kernel only; Linux +initrd +append")

    os.environ.pop("BENCH_TIMEOUT_S", None)
    if campaign_timeout_s("whimbrel") != 12.0:
        raise BenchFail("whimbrel default timeout is not 12")
    if campaign_timeout_s("t48") != 60.0:
        raise BenchFail("t48 default timeout is not 60")
    os.environ["BENCH_TIMEOUT_S"] = "45"
    if campaign_timeout_s("whimbrel") != 45.0 or campaign_timeout_s("t48") != 45.0:
        raise BenchFail("BENCH_TIMEOUT_S is not uniform across kinds")
    os.environ.pop("BENCH_TIMEOUT_S", None)
    fired.append("uniform campaign timeout (not a per-system recv knob)")

    if qemu_timeout_s("whimbrel", 12.0) != 14.0:
        raise BenchFail("Whimbrel qemu wait is not floor 12 + 2")
    if qemu_timeout_s("linux", 12.0) != 62.0:
        raise BenchFail("Linux qemu wait is not floor 60 + 2")
    if qemu_timeout_s("whimbrel", 60.0) != 62.0:
        raise BenchFail("uniform 60s recv must lengthen Whimbrel qemu wait too")
    fired.append("per-system QEMU hang watchdog; recv stays uniform")

    t48_names = [a.config for a in configs_for("t48")]
    if t48_names != [
        "release-fast-boot",
        "release-default",
        "trimmed",
        "stock",
        "trimmed-instrumented",
    ]:
        raise BenchFail(f"t48 arms unexpected: {t48_names}")
    fired.append("t48 five-arm configs_for")

    t47 = configs_for("t47")
    if [a.config for a in t47] != [
        "release-fast-boot", "release-default",
        "m-release-fast-boot", "m-release-default",
    ]:
        raise BenchFail(f"TEST FAIL: t47 arms {[a.config for a in t47]}")
    if [a.qemu_bios for a in t47] != [None, None, "mshim", "mshim"]:
        raise BenchFail("TEST FAIL: t47 bios markers wrong")
    if any(a.system != "whimbrel" for a in t47):
        raise BenchFail("TEST FAIL: t47 must be whimbrel-only")
    fired.append("t47 four-arm configs_for (D-0079 pair, whimbrel-only)")

    _qemu, qargs = qemu_argv("/tmp/whimbrel.pcap", 8080)
    qjoined = " ".join(qargs)
    if "csum=off" not in qjoined or "guest_tso4=off" not in qjoined:
        raise BenchFail(f"shared qemu args missing offload-off: {qjoined}")
    if "host_uso=off" not in qjoined:
        raise BenchFail(f"shared qemu args missing host_uso=off: {qjoined}")
    fired.append("shared virtio-net-device has csum=off / TSO-family off")

    trim_bad = trimmed_vs_stock_failures(
        {
            ("b1", "linux", "trimmed"): {"e0_to_e4_ns": [5_000_000_000.0]},
            ("b1", "linux", "stock"): {"e0_to_e4_ns": [1_000_000_000.0]},
        }
    )
    if not any("not published" in x for x in trim_bad):
        raise BenchFail(f"trimmed-vs-stock tripwire did not fire: {trim_bad}")
    fired.append(f"trimmed-vs-stock: {trim_bad[0]}")
    trim_ok = trimmed_vs_stock_failures(
        {
            ("b1", "linux", "trimmed"): {"e0_to_e4_ns": [1_000_000_000.0]},
            ("b1", "linux", "stock"): {"e0_to_e4_ns": [5_000_000_000.0]},
        }
    )
    if trim_ok:
        raise BenchFail(f"trimmed-vs-stock fired on a healthy pair: {trim_ok}")

    hdr = linux_header_lines(
        client_timeout_s=60.0,
        linux_meta={
            "cpio": "bench/linux/artifacts/rootfs.cpio",
            "cpio_sha256": "c" * 64,
        },
    )
    if hdr != [
        "client_timeout_s=60",
        "cpio=bench/linux/artifacts/rootfs.cpio",
        f"cpio_sha256={'c' * 64}",
        f"linux_append_quiet={LINUX_APPEND_QUIET}",
        f"linux_append_instrumented={LINUX_APPEND_INSTRUMENTED}",
    ]:
        raise BenchFail(f"batch header unexpected: {hdr}")
    fired.append("batch header: cpio hash, -append pins, uniform client_timeout_s")

    man = parse_linux_manifest(
        "artifact Image-stock "
        + ("a" * 64)
        + "\nartifact Image-trimmed "
        + ("b" * 64)
        + "\nartifact rootfs.cpio "
        + ("c" * 64)
        + "\nartifact init "
        + ("d" * 64)
        + f"\nappend quiet {LINUX_APPEND_QUIET}\n"
        + f"append instrumented {LINUX_APPEND_INSTRUMENTED}\n"
    )
    if man["artifacts"]["rootfs.cpio"] != "c" * 64:
        raise BenchFail("MANIFEST parse unexpected")
    try:
        parse_linux_manifest(
            "artifact Image-stock "
            + ("a" * 64)
            + "\nartifact Image-trimmed "
            + ("b" * 64)
            + "\nartifact rootfs.cpio "
            + ("c" * 64)
            + "\nartifact init "
            + ("d" * 64)
            + "\nappend quiet console=ttyS0\n"
            + f"append instrumented {LINUX_APPEND_INSTRUMENTED}\n"
        )
        raise BenchFail("MANIFEST append mismatch did not fire")
    except BenchFail as e:
        if "append quiet" not in str(e):
            raise
        fired.append(f"MANIFEST append pin: {e}")

    boot = subprocess.run(
        ["bash", str(ROOT / "scripts" / "linux-boot-test.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    boot_out = (boot.stdout or "") + (boot.stderr or "")
    if (ROOT / "bench" / "linux" / "MANIFEST").exists():
        # Bench host with artifacts: the boot test really runs and must
        # pass. The original single-branch check assumed artifacts
        # absent ("runs anywhere") and failed here from the day
        # linux-build landed them — the same environment-dependent
        # gate-assumption class as D-0080's cadence finding.
        if boot.returncode != 0:
            raise BenchFail(
                f"linux-boot-test failed with artifacts present: {boot_out}"
            )
        fired.append("linux-boot-test green with artifacts present")
    else:
        if boot.returncode == 0:
            raise BenchFail("linux-boot-test passed without artifacts")
        if "linux artifact missing" not in boot_out:
            raise BenchFail(
                f"linux-boot-test missing-artifact shape unexpected: {boot_out}"
            )
        fired.append("linux-boot-test fail-closed without artifacts")

    # D-0075: the loss classifier must find a planted event, ignore
    # ordinary jitter, and refuse a pre-D-0075 CSV rather than report
    # a clean run off a column that was never recorded.
    sig_rows = [
        {"system": "linux", "config": "trimmed", "batch_id": "b", "trial": i,
         "warmup": 0, "guest_ftx_ns": 237_000_000 + i * 200_000,
         "guest_arp_req_n": 1}
        for i in range(9)
    ]
    if len(arp_signature(sig_rows)) != 0:
        raise BenchFail("TEST FAIL: arp_signature flagged ordinary jitter")
    planted = sig_rows + [
        {"system": "linux", "config": "trimmed", "batch_id": "b", "trial": 9,
         "warmup": 0, "guest_ftx_ns": 237_000_000 + 52_000_000,
         "guest_arp_req_n": 1}
    ]
    hits = arp_signature(planted)
    if len(hits) != 1 or int(hits[0]["trial"]) != 9:
        raise BenchFail(f"TEST FAIL: arp_signature missed the planted event: {hits}")
    try:
        arp_signature([{k: v for k, v in sig_rows[0].items()
                        if k != "guest_ftx_ns"}])
        raise BenchFail("pre-D-0075 runs.csv did not fire")
    except BenchFail as e:
        if "no guest_ftx_ns column" not in str(e):
            raise
        fired.append(f"arp signature schema: {e}")
    fired.append(
        "arp signature: planted +52 ms event found, jitter ignored"
    )

    # D-0078: the canary must fail closed without its PHASE deltas and
    # must render the header line from a complete measurement.
    good = [{"phase": "stvec", "delta_ns": 1_030_000},
            {"phase": "page_verify", "delta_ns": 11_900_000}]
    c = canary_values(good)
    if c != {"canary_stvec_ns": 1_030_000, "canary_page_verify_ns": 11_900_000}:
        raise BenchFail(f"TEST FAIL: canary_values wrong: {c}")
    hdr = canary_header_lines(c)
    if len(hdr) != 1 or "canary_stvec_ns=1030000" not in hdr[0]             or "canary_page_verify_ns=11900000" not in hdr[0]:
        raise BenchFail(f"TEST FAIL: canary header line wrong: {hdr}")
    if canary_header_lines(None) != []:
        raise BenchFail("TEST FAIL: absent canary must add no header line")
    try:
        canary_values([{"phase": "stvec", "delta_ns": 1}])
        raise BenchFail("canary without page_verify did not fire")
    except BenchFail as e:
        if "canary boot produced no PHASE dump" not in str(e):
            raise
        fired.append(f"canary fail-closed: {e}")
    fired.append("canary header line renders from a complete measurement")

    print("TEST PASS: bench fail-closed selftest")
    for line in fired:
        print(f"  fired: {line}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("whimbrel", help="release-default + release+fast-boot")
    run_p.add_argument("--n", type=int, default=int(os.environ.get("BENCH_N", "30")))
    run_p.add_argument(
        "--warmup", type=int, default=int(os.environ.get("BENCH_WARMUP", "3"))
    )
    run_p.add_argument(
        "--batches", type=int, default=int(os.environ.get("BENCH_BATCHES", "2"))
    )
    run_p.add_argument("--out-dir", default=os.environ.get("BENCH_OUT", "results"))
    run_p.add_argument("--port", type=int, default=int(os.environ.get("BENCH_PORT", "8080")))
    run_p.add_argument("--allow-dirty", action="store_true")
    run_p.add_argument(
        "--shuffle-seed",
        type=int,
        default=None,
        help="recorded RNG seed for trial shuffle (or BENCH_SHUFFLE_SEED)",
    )
    run_p.set_defaults(kind="whimbrel", func=cmd_run)

    t48 = sub.add_parser("t48", help="T4.8 five-arm cross-system campaign")
    t48.add_argument("--n", type=int, default=int(os.environ.get("BENCH_N", "30")))
    t48.add_argument(
        "--warmup", type=int, default=int(os.environ.get("BENCH_WARMUP", "3"))
    )
    t48.add_argument(
        "--batches", type=int, default=int(os.environ.get("BENCH_BATCHES", "2"))
    )
    t48.add_argument("--out-dir", default=os.environ.get("BENCH_OUT", "results"))
    t48.add_argument("--port", type=int, default=int(os.environ.get("BENCH_PORT", "8080")))
    t48.add_argument("--allow-dirty", action="store_true")
    t48.add_argument(
        "--shuffle-seed",
        type=int,
        default=None,
        help="recorded RNG seed for trial shuffle (or BENCH_SHUFFLE_SEED)",
    )
    t48.set_defaults(kind="t48", func=cmd_run)

    t47 = sub.add_parser(
        "t47", help="D-0079 with/without-firmware pair, one campaign"
    )
    t47.add_argument("--n", type=int, default=int(os.environ.get("BENCH_N", "30")))
    t47.add_argument(
        "--warmup", type=int, default=int(os.environ.get("BENCH_WARMUP", "3"))
    )
    t47.add_argument(
        "--batches", type=int, default=int(os.environ.get("BENCH_BATCHES", "2"))
    )
    t47.add_argument("--out-dir", default=os.environ.get("BENCH_OUT", "results"))
    t47.add_argument(
        "--port", type=int, default=int(os.environ.get("BENCH_PORT", "8080"))
    )
    t47.add_argument("--allow-dirty", action="store_true")
    t47.add_argument("--shuffle-seed", type=int, default=None)
    t47.set_defaults(kind="t47", func=cmd_run)

    fp = sub.add_parser("fp-ab", help="finding 14: frame-pointer A/B")
    fp.add_argument("--n", type=int, default=int(os.environ.get("BENCH_N", "30")))
    fp.add_argument(
        "--warmup", type=int, default=int(os.environ.get("BENCH_WARMUP", "3"))
    )
    fp.add_argument("--batches", type=int, default=1)
    fp.add_argument("--out-dir", default="results/fp-ab")
    fp.add_argument("--port", type=int, default=int(os.environ.get("BENCH_PORT", "8080")))
    fp.add_argument("--allow-dirty", action="store_true")
    fp.set_defaults(kind="fp-ab", func=cmd_run)

    sm = sub.add_parser("summarize")
    sm.add_argument("--out-dir", default="results")
    sm.add_argument("--stability", action="store_true")
    sm.add_argument("--allow-dirty", action="store_true")
    sm.set_defaults(func=cmd_summarize)

    st = sub.add_parser("selftest")
    st.set_defaults(func=cmd_selftest)

    sig = sub.add_parser(
        "arp-signature",
        help="count guest ARP-loss events in a runs.csv (D-0074 item 4)",
    )
    sig.add_argument("runs")
    sig.set_defaults(func=cmd_arp_signature)

    chk = sub.add_parser("check-serial", help="assert PHASE deltas sum to E2→E3g")
    chk.add_argument("serial")
    chk.set_defaults(func=cmd_check_serial)

    lin = sub.add_parser(
        "check-linux-artifacts",
        help="hash Linux artifacts against bench/linux/MANIFEST",
    )
    lin.add_argument("names", nargs="*")
    lin.set_defaults(func=cmd_check_linux_artifacts)

    args = p.parse_args()
    try:
        return args.func(args)
    except BenchFail as e:
        print(str(e), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"TEST FAIL: command failed: {e.cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
