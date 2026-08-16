#!/usr/bin/env python3
# Datagram client for T3.8 (the nc -u shape). SOCK_DGRAM to 127.0.0.1:7777
# after the guest has printed UDP ECHO READY. Recv timeout 2s: a silent
# guest is TEST FAIL, not a hang (D-0050). Payload must echo verbatim.
import socket
import sys

PAYLOAD = b"whimbrel-udp-echo"
OUT = sys.argv[1] if len(sys.argv) > 1 else "udp-echo.got"

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(2.0)
try:
    s.sendto(PAYLOAD, ("127.0.0.1", 7777))
    data, _ = s.recvfrom(256)
except socket.timeout:
    sys.stderr.write("TEST FAIL: no UDP echo (recv timeout 2s)\n")
    sys.exit(1)
except OSError as e:
    sys.stderr.write(f"TEST FAIL: UDP socket: {e}\n")
    sys.exit(1)
finally:
    s.close()

open(OUT, "wb").write(data)
if data != PAYLOAD:
    sys.stderr.write(f"TEST FAIL: UDP echo mismatch got={data!r} want={PAYLOAD!r}\n")
    sys.exit(1)
print("TEST PASS: UDP echo verbatim")
