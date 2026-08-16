#!/usr/bin/env python3
"""M4 benchmark harness (D-0055 / T4.1).

Long/tidy CSV: one run row per trial, one phase row per trial × PHASE
line. T4.2 adding stamps is more rows, not more columns. Phase names are
parsed from serial — this file is not a fourth copy of the justfile list
(finding 26).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
    "qemu_version",
    "qemu_hash",
    "host_kernel",
    "cpu_model",
    "governor",
    "loadavg_1m",
    "qemu_cpu",
    "client_cpu",
    "client_granularity_ns",
    "e0_mono_ns",
    "e0_wall_ns",
    "e0_to_first_connect_ns",
    "e0_to_e3w_ns",
    "e0_to_e4_ns",
    "attempts",
    "pcap_path",
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


def qemu_argv(pcap: str, port: int = 8080) -> tuple[str, list[str]]:
    script = ROOT / "scripts" / "qemu-args.sh"
    line = subprocess.check_output(
        ["bash", str(script), pcap, str(port)], text=True
    ).strip()
    args = line.split()
    qemu = os.environ.get("QEMU", "qemu-system-riscv64")
    return qemu, args


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
    gov = "unavailable"
    gov_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if gov_path.is_file():
        gov = gov_path.read_text().strip() or "unavailable"
    loadavg = Path("/proc/loadavg").read_text().split()[0]
    qemu = os.environ.get("QEMU", "qemu-system-riscv64")
    qpath = shutil.which(qemu)
    if not qpath:
        raise BenchFail(f"TEST FAIL: {qemu} not on PATH")
    ver = subprocess.check_output([qpath, "--version"], text=True).splitlines()[0]
    return {
        "host_kernel": kernel,
        "cpu_model": cpu,
        "governor": gov,
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
    return rows


def pcap_time_ns(line: str) -> int:
    parts = line.split()
    if not parts:
        raise BenchFail(f"TEST FAIL: empty tshark line: {line!r}")
    return int(round(float(parts[0]) * 1_000_000_000))


def tshark_fields(pcap: Path, tshark: str, display_filter: str) -> str:
    cmd = [
        tshark,
        "-r",
        str(pcap),
        "-o",
        "tcp.relative_sequence_numbers:FALSE",
        "-Y",
        display_filter,
        "-T",
        "fields",
        "-e",
        "frame.time_relative",
        "-e",
        "frame.number",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BenchFail(
            f"TEST FAIL: tshark could not read {pcap}: {proc.stderr.strip()}"
        )
    return proc.stdout


def e0_to_e3w_ns(pcap: Path, tshark: str, e0_to_first_connect_ns: int) -> int:
    """E3w on the E0 timeline: monotonic first-connect (≈ SYN/ACK) plus
    the pcap-relative SYN/ACK→HTTP interval. filter-dump's wall clock and
    Python CLOCK_REALTIME disagree on this QEMU, so this is not
    `pcap_epoch - e0_wall`.
    """
    if not pcap.is_file() or pcap.stat().st_size == 0:
        raise BenchFail(f"TEST FAIL: pcap missing or empty: {pcap}")
    syn_out = tshark_fields(
        pcap,
        tshark,
        "tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && "
        "tcp.flags.syn == 1 && tcp.flags.ack == 1",
    )
    http_out = tshark_fields(
        pcap,
        tshark,
        "tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.len > 0 && "
        'tcp.flags.syn == 0 && frame contains "HTTP/1.0 200 OK"',
    )
    syn_line = next((ln for ln in syn_out.splitlines() if ln.strip()), "")
    http_line = next((ln for ln in http_out.splitlines() if ln.strip()), "")
    if not syn_line:
        raise BenchFail(f"TEST FAIL: no TCP SYN/ACK from 10.0.2.15:80 in {pcap}")
    if not http_line:
        raise BenchFail(f"TEST FAIL: no HTTP 200 data frame in {pcap}")
    syn_rel = pcap_time_ns(syn_line)
    http_rel = pcap_time_ns(http_line)
    if http_rel < syn_rel:
        raise BenchFail(
            f"TEST FAIL: HTTP frame before SYN/ACK in {pcap} "
            f"(synack_rel_ns={syn_rel} http_rel_ns={http_rel})"
        )
    return e0_to_first_connect_ns + (http_rel - syn_rel)


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


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise BenchFail(f"TEST FAIL: {path} missing")
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def assert_aggregatable(runs: list[dict], *, allow_dirty: bool = False) -> None:
    if not runs:
        raise BenchFail("TEST FAIL: zero-trial CSV (nothing to aggregate)")
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


def metric_table(runs: list[dict], phases: list[dict]) -> dict[str, list[float]]:
    recorded_runs = [r for r in runs if int(r["warmup"]) == 0]
    metrics: dict[str, list[float]] = {
        "e0_to_first_connect_ns": [],
        "e0_to_e3w_ns": [],
        "e0_to_e4_ns": [],
    }
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
    kernel: Path,
    pcap: Path,
    serial_path: Path,
    client_out: Path,
    ready_path: Path,
    qemu_cpu: int,
    client_cpu: int,
    port: int,
    timeout_s: float,
) -> dict:
    tshark = require_tshark()
    qemu, args = qemu_argv(str(pcap), port)
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
        str(timeout_s),
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
        qemu_cmd += [qemu, *args, "-kernel", str(kernel)]
        e0_mono = time.monotonic_ns()
        e0_wall = time.time_ns()
        with open(serial_path, "wb") as ser:
            qemu_p = subprocess.Popen(
                qemu_cmd, cwd=ROOT, stdout=ser, stderr=subprocess.STDOUT
            )
        try:
            qemu_p.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            qemu_p.kill()
            qemu_p.wait(timeout=2)
            raise BenchFail(f"TEST FAIL: QEMU timed out after {timeout_s}s")
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
        raise BenchFail("TEST FAIL: client did not receive body whimbrel\\n")
    if client_data.get("first_connect_mono_ns") is None:
        raise BenchFail("TEST FAIL: no first-connect stamp")
    if client_data.get("first_byte_mono_ns") is None:
        raise BenchFail("TEST FAIL: no first-byte stamp (E4)")

    serial_text = serial_path.read_bytes().decode("utf-8", errors="replace")
    if "PANIC" in serial_text:
        raise BenchFail("TEST FAIL: guest panic")
    phases = parse_phases(serial_text)
    e0_to_connect = int(client_data["first_connect_mono_ns"]) - e0_mono
    e0_to_e4 = int(client_data["first_byte_mono_ns"]) - e0_mono
    e0_to_e3w = e0_to_e3w_ns(pcap, tshark, e0_to_connect)
    if e0_to_e3w < 0:
        raise BenchFail(f"TEST FAIL: e0_to_e3w_ns is negative ({e0_to_e3w})")
    if e0_to_e4 < e0_to_e3w:
        raise BenchFail(
            f"TEST FAIL: E4 before E3w (e0_to_e4={e0_to_e4} e0_to_e3w={e0_to_e3w})"
        )
    return {
        "e0_mono_ns": e0_mono,
        "e0_wall_ns": e0_wall,
        "e0_to_first_connect_ns": e0_to_connect,
        "e0_to_e4_ns": e0_to_e4,
        "e0_to_e3w_ns": e0_to_e3w,
        "attempts": int(client_data["attempts"]),
        "phases": phases,
        "qemu_status": qemu_p.returncode,
    }


def configs_for(kind: str) -> list[tuple[str, list[str], dict[str, str]]]:
    if kind == "whimbrel":
        return [
            ("release-default", [], {}),
            ("release-fast-boot", ["fast-boot"], {}),
        ]
    if kind == "fp-ab":
        nofp = {
            "CARGO_TARGET_DIR": str(ROOT / "target-nofp"),
            "RUSTFLAGS": (
                f"-C link-arg=-T{ROOT / 'linker.ld'} "
                "-C force-frame-pointers=no"
            ),
        }
        return [
            ("release-fast-boot", ["fast-boot"], {}),
            ("release-fast-boot-nofp", ["fast-boot"], nofp),
        ]
    raise BenchFail(f"TEST FAIL: unknown bench kind {kind}")


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
    host = host_meta()
    qemu_cpu, client_cpu = pin_cpus()
    n = args.n
    warmup = args.warmup
    batches = args.batches
    port = args.port
    require_port_free(port)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gran = calibrate_client(client_cpu, port)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_rows: list[dict] = []
    phase_rows: list[dict] = []
    timeout_s = float(os.environ.get("BENCH_TIMEOUT_S", "12"))

    kernels: dict[str, tuple[Path, str]] = {}
    for config, features, env_extra in configs_for(args.kind):
        kernel_src = cargo_build(features, env_extra=env_extra or None)
        kdir = out_dir / "bin"
        kdir.mkdir(parents=True, exist_ok=True)
        kernel = kdir / config
        shutil.copy2(kernel_src, kernel)
        kernels[config] = (kernel, sha256_file(kernel))

    for batch_i in range(1, batches + 1):
        batch_id = f"{stamp}-{batch_i}"
        for config, _features, _env in configs_for(args.kind):
            kernel, k_hash = kernels[config]
            total = warmup + n
            for trial in range(1, total + 1):
                is_warmup = 1 if trial <= warmup else 0
                tdir = out_dir / "trials" / batch_id / config / f"{trial:02d}"
                tdir.mkdir(parents=True, exist_ok=True)
                pcap = tdir / "qemu.pcap"
                serial_path = tdir / "serial.log"
                client_out = tdir / "client.json"
                ready_path = tdir / "client.ready"
                print(
                    f"bench: batch={batch_id} config={config} "
                    f"trial={trial}/{total} warmup={is_warmup}",
                    flush=True,
                )
                result = run_trial(
                    kernel=kernel,
                    pcap=pcap,
                    serial_path=serial_path,
                    client_out=client_out,
                    ready_path=ready_path,
                    qemu_cpu=qemu_cpu,
                    client_cpu=client_cpu,
                    port=port,
                    timeout_s=timeout_s,
                )
                rel_pcap = os.path.relpath(pcap, ROOT)
                run_rows.append(
                    {
                        "batch_id": batch_id,
                        "trial": trial,
                        "warmup": is_warmup,
                        "system": "whimbrel",
                        "config": config,
                        "git_sha": git_sha,
                        "dirty": dirty,
                        "kernel_sha256": k_hash,
                        "qemu_version": host["qemu_version"],
                        "qemu_hash": host["qemu_hash"],
                        "host_kernel": host["host_kernel"],
                        "cpu_model": host["cpu_model"],
                        "governor": host["governor"],
                        "loadavg_1m": host["loadavg_1m"],
                        "qemu_cpu": qemu_cpu,
                        "client_cpu": client_cpu,
                        "client_granularity_ns": gran,
                        "e0_mono_ns": result["e0_mono_ns"],
                        "e0_wall_ns": result["e0_wall_ns"],
                        "e0_to_first_connect_ns": result["e0_to_first_connect_ns"],
                        "e0_to_e3w_ns": result["e0_to_e3w_ns"],
                        "e0_to_e4_ns": result["e0_to_e4_ns"],
                        "attempts": result["attempts"],
                        "pcap_path": rel_pcap,
                    }
                )
                for ph in result["phases"]:
                    phase_rows.append(
                        {
                            "batch_id": batch_id,
                            "trial": trial,
                            "warmup": is_warmup,
                            "system": "whimbrel",
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

    rc = cmd_summarize(
        argparse.Namespace(
            out_dir=str(out_dir),
            stability=batches >= 2,
            expect_n=n,
            allow_dirty=args.allow_dirty,
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
    with_fp = by_cfg.get("release-fast-boot", {})
    no_fp = by_cfg.get("release-fast-boot-nofp", {})
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
        "strip-vs-record is the operator's call; this harness does not "
        "change .cargo/config.toml."
    )


def cmd_summarize(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    runs = read_csv(out_dir / "runs.csv")
    phases = read_csv(out_dir / "phases.csv")
    assert_aggregatable(runs, allow_dirty=getattr(args, "allow_dirty", False))
    expect_n = getattr(args, "expect_n", None)
    lines = [
        "# bench summary (D-0055): n / median / IQR / min / max; warmup excluded",
        f"qemu_version={runs[0]['qemu_version']}",
        f"qemu_hash={runs[0]['qemu_hash']}",
        f"git_sha={runs[0]['git_sha']} dirty={runs[0]['dirty']}",
        f"host_kernel={runs[0]['host_kernel']}",
        f"cpu_model={runs[0]['cpu_model']}",
        f"governor={runs[0]['governor']} loadavg_1m={runs[0]['loadavg_1m']}",
        f"client_granularity_ns={runs[0]['client_granularity_ns']}",
        "",
    ]
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
        mets = metric_table(rs, phase_groups.get(key, []))
        metric_by_group[key] = mets
        title = f"{key[1]} {key[2]} batch={key[0]} n_recorded={len(rec)}"
        lines.extend(summarize_group(title, mets))
        lines.append("")

    failed: list[str] = []
    if getattr(args, "stability", False):
        by_cfg: dict[str, list[tuple[str, dict[str, list[float]]]]] = {}
        for (batch, _sys, cfg), mets in metric_by_group.items():
            by_cfg.setdefault(cfg, []).append((batch, mets))
        lines.append("## stability (consecutive batches, metrics ≥ 1 ms)")
        for cfg, items in sorted(by_cfg.items()):
            items.sort()
            if len(items) < 2:
                failed.append(f"{cfg}: need ≥2 batches, have {len(items)}")
                continue
            a, b = items[-2], items[-1]
            bad = compare_stability(a[1], b[1])
            if bad:
                failed.append(f"{cfg} {a[0]} vs {b[0]}:")
                failed.extend("  " + x for x in bad)
            else:
                lines.append(f"{cfg}: {a[0]} vs {b[0]} PASS")
        lines.append("")

    text = "\n".join(lines) + "\n"
    (out_dir / "summary.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    if failed:
        print("TEST FAIL: stability criterion not met", file=sys.stderr)
        print("\n".join(failed), file=sys.stderr)
        print(
            "Not widening the criterion (D-0055). Varying metrics listed above.",
            file=sys.stderr,
        )
        return 1
    print("TEST PASS: bench summary")
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
        "qemu_version": "QEMU emulator version 8.2.2",
        "qemu_hash": "h",
        "host_kernel": "6.12",
        "cpu_model": "test",
        "governor": "unavailable",
        "loadavg_1m": "0.00",
        "qemu_cpu": 2,
        "client_cpu": 3,
        "client_granularity_ns": 1000000,
        "e0_mono_ns": 0,
        "e0_wall_ns": 0,
        "e0_to_first_connect_ns": 10_000_000,
        "e0_to_e3w_ns": 11_000_000,
        "e0_to_e4_ns": 12_000_000,
        "attempts": 12,
        "pcap_path": "x.pcap",
    }
    write_csv(path, RUNS_FIELDS, [{**base, **r} for r in rows])


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
        "PHASE E3g ticks=200 ns=20000 since_start=100 ns=10000 delta=100 ns=10000\n"
    )
    rows = parse_phases(good_serial)
    if [r["phase"] for r in rows] != ["_start", "E3g"]:
        raise BenchFail(f"good PHASE parse unexpected: {rows}")

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
    run_p.set_defaults(kind="whimbrel", func=cmd_run)

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
