#!/usr/bin/env bash
# Fire a TCP connect at the hostfwd port.
#
# After DRIVER_OK and *before* our GARP: slirp ARPs 10.0.2.15 (T3.5).
# That first connect uses a short timeout — it is the ARP trigger, not
# the handshake. After `TX ARP reply`: slirp already has our MAC and
# sends IPv4 (T3.6 / D-0046). The second connect waits long enough for
# the guest to SYN/ACK during `wait_ping_reply`, then close()s with FIN.
# T3.11 kernel-closes that unused TCB so LISTEN is restored before curl
# (D-0053). Timeout is seconds, default 0.3.
set -u
TIMEOUT="${1:-0.3}"
python3 - "$TIMEOUT" <<'PY'
import socket, sys
timeout = float(sys.argv[1])
s = socket.socket()
s.settimeout(timeout)
try:
    s.connect(("127.0.0.1", 8080))
except OSError:
    pass
s.close()
PY
