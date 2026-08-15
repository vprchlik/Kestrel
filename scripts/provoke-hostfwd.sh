#!/usr/bin/env bash
# Fire a TCP connect at the hostfwd port.
#
# After DRIVER_OK and *before* our GARP: slirp ARPs 10.0.2.15 (T3.5).
# After `TX ARP reply`: slirp already has our MAC and sends IPv4 (T3.6 /
# D-0046). Do not wait for a handshake: the guest has no TCP yet.
set -u
python3 - <<'PY'
import socket
s = socket.socket()
s.settimeout(0.3)
try:
    s.connect(("127.0.0.1", 8080))
except OSError:
    pass
s.close()
PY
