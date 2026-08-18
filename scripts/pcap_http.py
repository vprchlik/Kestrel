#!/usr/bin/env python3
"""Shared pcap extract and Linux confound gates (D-0070 / D-0071 / T4.8).

`extract_pcap` is the one copy of the W / D_ack / D_fin filters.
`scripts/d0070-pcap-pass.py` and `scripts/bench.py` both import it.
A third copy of the filters is a fail.

Also: RST (confound B) and SYN-grid (confound A). SYN-grid is
Linux-only at the harness; the assert is per-pcap and fail-closed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ARP_FILTER = (
    "arp.opcode==1 && arp.src.proto_ipv4==10.0.2.2 && "
    "arp.dst.proto_ipv4==10.0.2.15"
)
SYNACK_FILTER = "tcp.srcport==80 && tcp.flags==0x012"
HTTP_FILTER = (
    "tcp && ip.src == 10.0.2.15 && tcp.srcport == 80 && tcp.len > 0 && "
    'tcp.flags.syn == 0 && frame contains "HTTP/1.0 200 OK"'
)
RST_FILTER = "tcp.flags.reset == 1"
SYN_IN_FILTER = (
    "tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && "
    "tcp.flags.syn == 1 && tcp.flags.ack == 0"
)
# slirp's default MAC (52:55 + 10.0.2.2). Guest first TX is the first
# frame not sourced from this address — ARP or the UDP announce.
SLIRP_MAC = "52:55:0a:00:02:02"
GUEST_TX_FILTER = f"eth.src != {SLIRP_MAC}"
SYN_GRID_LIMIT_S = 0.001
HTTP_LEN = 92


class PcapExtractError(Exception):
    pass


def tshark_bin() -> str:
    return os.environ.get("BENCH_TSHARK", "tshark")


def require_tshark() -> str:
    name = tshark_bin()
    path = name if os.path.sep in name else shutil.which(name)
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise PcapExtractError(
            f"TEST FAIL: tshark not installed ({name}); see docs/SETUP.md"
        )
    return path


def tshark_table(
    pcap: Path, tshark: str, display_filter: str, extra: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    fields = ("frame.time_relative", "frame.number") + extra
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
    ]
    for field in fields:
        cmd.extend(["-e", field])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = proc.stderr.strip() or f"status={proc.returncode}"
        if "permission" in err.lower():
            raise PcapExtractError(
                f"TEST FAIL: tshark could not read {pcap}: {err} "
                "(AppArmor override: docs/SETUP.md §7)"
            )
        raise PcapExtractError(
            f"TEST FAIL: tshark could not read {pcap}: {err}"
        )
    rows: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        while len(parts) < len(fields):
            parts.append("")
        rows.append(dict(zip(fields, parts[: len(fields)])))
    return rows


def _time_ns(row: dict[str, str]) -> int:
    return int(round(float(row["frame.time_relative"]) * 1_000_000_000))


def _time_s(row: dict[str, str]) -> float:
    return float(row["frame.time_relative"])


def _frame_no(row: dict[str, str]) -> int:
    return int(row["frame.number"])


def extract_pcap(pcap: Path, tshark: str) -> dict[str, int]:
    """W, D_ack, D_fin, and pcap(SYN/ACK→HTTP) on one pcap clock."""
    if not pcap.is_file() or pcap.stat().st_size == 0:
        raise PcapExtractError(f"TEST FAIL: pcap missing or empty: {pcap}")

    arp_rows = tshark_table(pcap, tshark, ARP_FILTER)
    if not arp_rows:
        raise PcapExtractError(
            f"TEST FAIL: no slirp ARP request for 10.0.2.15 in {pcap}"
        )
    syn_rows = tshark_table(pcap, tshark, SYNACK_FILTER)
    if not syn_rows:
        raise PcapExtractError(
            f"TEST FAIL: no guest SYN/ACK (tcp.flags==0x012) in {pcap}"
        )
    http_rows = tshark_table(
        pcap, tshark, HTTP_FILTER, extra=("tcp.nxtseq", "tcp.len", "tcp.flags.fin")
    )
    if not http_rows:
        raise PcapExtractError(f"TEST FAIL: no HTTP 200 data frame in {pcap}")

    arp = arp_rows[0]
    syn = syn_rows[0]
    http = http_rows[0]
    t_arp = _time_ns(arp)
    t_syn = _time_ns(syn)
    t_http = _time_ns(http)
    fn_http = _frame_no(http)
    if t_syn < t_arp:
        raise PcapExtractError(
            f"TEST FAIL: SYN/ACK before slirp ARP in {pcap} "
            f"(arp_ns={t_arp} synack_ns={t_syn})"
        )
    if t_http < t_syn:
        raise PcapExtractError(
            f"TEST FAIL: HTTP frame before SYN/ACK in {pcap} "
            f"(synack_ns={t_syn} http_ns={t_http})"
        )
    nxt = http["tcp.nxtseq"].strip()
    if not nxt.isdigit():
        raise PcapExtractError(
            f"TEST FAIL: HTTP tcp.nxtseq not an integer in {pcap}: {nxt!r}"
        )
    ack_filter = (
        "tcp && ip.src == 10.0.2.2 && ip.dst == 10.0.2.15 && "
        "tcp.dstport == 80 && tcp.flags.syn == 0 && tcp.flags.fin == 0 && "
        "tcp.flags.reset == 0 && tcp.flags.ack == 1 && tcp.len == 0 && "
        f"tcp.ack == {int(nxt)} && frame.number > {fn_http}"
    )
    ack_rows = tshark_table(pcap, tshark, ack_filter, extra=("tcp.ack",))
    if not ack_rows:
        raise PcapExtractError(
            f"TEST FAIL: no pure ACK of HTTP payload+FIN "
            f"(tcp.ack={nxt}) after frame {fn_http} in {pcap}"
        )
    fin_filter = (
        "tcp && ip.dst == 10.0.2.15 && tcp.dstport == 80 && "
        f"tcp.flags.fin == 1 && frame.number > {fn_http}"
    )
    fin_rows = tshark_table(pcap, tshark, fin_filter)
    if not fin_rows:
        raise PcapExtractError(
            f"TEST FAIL: no client FIN toward :80 after HTTP frame "
            f"{fn_http} in {pcap}"
        )
    t_ack = _time_ns(ack_rows[0])
    t_fin = _time_ns(fin_rows[0])
    if t_ack < t_http:
        raise PcapExtractError(
            f"TEST FAIL: ACK-of-response before HTTP in {pcap}"
        )
    if t_fin < t_http:
        raise PcapExtractError(f"TEST FAIL: client FIN before HTTP in {pcap}")
    return {
        "w_ns": t_syn - t_arp,
        "d_ack_ns": t_ack - t_http,
        "d_fin_ns": t_fin - t_http,
        "synack_to_http_ns": t_http - t_syn,
        "http_len": int(http["tcp.len"]),
    }


def assert_no_rst(pcap: Path, tshark: str) -> None:
    """Confound B: any RST fails the run. Same shape as assert-pcap-http.sh."""
    if not pcap.is_file() or pcap.stat().st_size == 0:
        raise PcapExtractError(f"TEST FAIL: pcap missing or empty: {pcap}")
    rows = tshark_table(pcap, tshark, RST_FILTER)
    if rows:
        frames = ",".join(r["frame.number"] for r in rows)
        raise PcapExtractError(
            f"TEST FAIL: RST present in {pcap} (wanted none on the happy "
            f"path); frames {frames}"
        )


def assert_syn_grid(
    pcap: Path, tshark: str, *, limit_s: float = SYN_GRID_LIMIT_S
) -> float:
    """Confound A: t(SYN into guest) − t(guest first TX) < 1 ms.

    Guest first TX is the first frame not sourced from slirp, not ARP
    specifically. SYN into guest is the first SYN to :80 at or after
    that TX (the flush, not an earlier slirp probe). One miss fails
    the batch — this function raises; the harness does not continue.
    Returns dt in seconds.
    """
    if not pcap.is_file() or pcap.stat().st_size == 0:
        raise PcapExtractError(f"TEST FAIL: pcap missing or empty: {pcap}")
    tx_rows = tshark_table(pcap, tshark, GUEST_TX_FILTER)
    if not tx_rows:
        raise PcapExtractError(
            f"TEST FAIL: SYN-grid: no guest first TX "
            f"(eth.src != {SLIRP_MAC}) in {pcap}"
        )
    syn_rows = tshark_table(pcap, tshark, SYN_IN_FILTER)
    if not syn_rows:
        raise PcapExtractError(
            f"TEST FAIL: SYN-grid: no TCP SYN to 10.0.2.15:80 in {pcap}"
        )
    t_tx = _time_s(tx_rows[0])
    flushed = [r for r in syn_rows if _time_s(r) + 1e-12 >= t_tx]
    if not flushed:
        raise PcapExtractError(
            f"TEST FAIL: SYN-grid: no SYN into guest after first TX "
            f"(t_tx={t_tx:.6f}s) in {pcap}"
        )
    t_syn = _time_s(flushed[0])
    dt = t_syn - t_tx
    if not (0.0 <= dt < limit_s):
        raise PcapExtractError(
            f"TEST FAIL: SYN-grid: t(SYN)-t(guest first TX)={dt:.6f}s "
            f"(want 0 ≤ dt < {limit_s}s) in {pcap} — trial is measuring "
            f"slirp's RTO, not guest listen"
        )
    return dt


def cmd_syn_grid(pcap: Path) -> int:
    tshark = require_tshark()
    try:
        dt = assert_syn_grid(pcap, tshark)
    except PcapExtractError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(
        f"TEST PASS: SYN-grid dt={dt * 1e6:.1f} µs (< 1 ms) in {pcap}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("syn-grid", help="confound A: SYN-grid per pcap")
    g.add_argument("pcap")
    args = p.parse_args()
    if args.cmd == "syn-grid":
        return cmd_syn_grid(Path(args.pcap))
    raise SystemExit(f"unknown cmd {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
