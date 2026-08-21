#!/usr/bin/env python3
"""Persistent HTTP measurement client (D-0055 / audit finding 32).

One process, `time.monotonic_ns()`, connect-retry at ~1 ms cadence.
Records first-connect, first-byte (E4), and attempt count. Replaces the
fork-per-attempt curl loop whose exec overhead quantized E4 to 5–15 ms.

Recv timeout after connect is `--timeout-s` (the campaign trial
timeout), identical for every system. A hardcoded 2 s recv is the
per-system-looking knob that would hide a slow Linux arm (D-0062).
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import select
import socket
import sys
import time

GET = (
    b"GET / HTTP/1.0\r\n"
    b"Host: 127.0.0.1\r\n"
    b"\r\n"
)
# Byte-identical 92-byte RESP (app/src/lib.rs / bench/linux/server.c).
RESP = (
    b"HTTP/1.0 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Connection: close\r\n"
    b"Content-Length: 9\r\n"
    b"\r\n"
    b"whimbrel\n"
)
assert len(RESP) == 92
CADENCE_NS = 1_000_000
CONNECT_WAIT_NS = 800_000


def wait_until(deadline_ns: int) -> None:
    while True:
        now = time.monotonic_ns()
        if now >= deadline_ns:
            return
        remain = deadline_ns - now
        if remain > 200_000:
            time.sleep((remain - 100_000) / 1e9)


def attempt_connect(
    host: str, port: int, wait_ns: int, recv_timeout_s: float
) -> socket.socket | None:
    s = socket.socket()
    s.setblocking(False)
    try:
        rc = s.connect_ex((host, port))
        if rc == 0:
            s.setblocking(True)
            s.settimeout(recv_timeout_s)
            return s
        if rc not in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EAGAIN):
            s.close()
            return None
        deadline = time.monotonic_ns() + wait_ns
        while True:
            remain = (deadline - time.monotonic_ns()) / 1e9
            if remain <= 0:
                s.close()
                return None
            _, w, _ = select.select([], [s], [s], remain)
            if not w:
                s.close()
                return None
            err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err == 0:
                s.setblocking(True)
                s.settimeout(recv_timeout_s)
                return s
            s.close()
            return None
    except OSError:
        try:
            s.close()
        except OSError:
            pass
        return None


def recv_response(s: socket.socket, recv_timeout_s: float) -> tuple[int | None, bytes]:
    buf = b""
    first_byte = None
    s.settimeout(recv_timeout_s)
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            if first_byte is None:
                first_byte = time.monotonic_ns()
            buf += chunk
            if RESP in buf:
                break
    except OSError:
        pass
    return first_byte, buf


def calibrate(host: str, port: int, n: int) -> dict:
    times: list[int] = []
    next_t = time.monotonic_ns()
    for _ in range(n):
        wait_until(next_t)
        t = time.monotonic_ns()
        times.append(t)
        sock = attempt_connect(host, port, CONNECT_WAIT_NS, 2.0)
        if sock is not None:
            sock.close()
        next_t = t + CADENCE_NS
    deltas = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    deltas.sort()
    mid = deltas[len(deltas) // 2]
    return {
        "n": n,
        "cadence_target_ns": CADENCE_NS,
        "granularity_ns": mid,
        "granularity_min_ns": deltas[0],
        "granularity_max_ns": deltas[-1],
        "granularity_p25_ns": deltas[len(deltas) // 4],
        "granularity_p75_ns": deltas[(3 * len(deltas)) // 4],
    }


def run_loop(host: str, port: int, timeout_s: float, ready_path: str) -> dict:
    deadline = time.monotonic_ns() + int(timeout_s * 1e9)
    attempts = 0
    attempt_ns: list[int] = []
    first_connect = None
    first_byte = None
    body_ok = False
    buf = b""

    with open(ready_path, "w", encoding="utf-8") as f:
        f.write("ready\n")
        f.flush()
        os.fsync(f.fileno())

    next_t = time.monotonic_ns()
    while time.monotonic_ns() < deadline:
        wait_until(next_t)
        t = time.monotonic_ns()
        if t >= deadline:
            break
        attempts += 1
        attempt_ns.append(t)
        sock = attempt_connect(host, port, CONNECT_WAIT_NS, timeout_s)
        if sock is None:
            next_t = t + CADENCE_NS
            continue
        first_connect = time.monotonic_ns()
        try:
            sock.sendall(GET)
            first_byte, buf = recv_response(sock, timeout_s)
        finally:
            try:
                sock.close()
            except OSError:
                pass
        body_ok = RESP in buf
        break

    deltas = [attempt_ns[i + 1] - attempt_ns[i] for i in range(max(0, len(attempt_ns) - 1))]
    deltas.sort()
    gran = deltas[len(deltas) // 2] if deltas else None
    return {
        "attempts": attempts,
        "first_connect_mono_ns": first_connect,
        "first_byte_mono_ns": first_byte,
        "body_ok": body_ok,
        "attempt_period_median_ns": gran,
        "timeout": first_byte is None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--timeout-s", type=float, default=12.0)
    p.add_argument("--ready", required=False)
    p.add_argument("--out", required=False)
    p.add_argument("--calibrate", type=int, default=0)
    args = p.parse_args()

    if args.calibrate:
        result = calibrate(args.host, args.port, args.calibrate)
    else:
        if not args.ready or not args.out:
            print("TEST FAIL: --ready and --out required unless --calibrate", file=sys.stderr)
            return 1
        result = run_loop(args.host, args.port, args.timeout_s, args.ready)

    text = json.dumps(result, sort_keys=True)
    if args.out:
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
