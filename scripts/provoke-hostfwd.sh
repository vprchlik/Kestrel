#!/usr/bin/env bash
# Fire a TCP connect at the hostfwd port so slirp ARPs 10.0.2.15.
#
# Call only after DRIVER_OK (RX buffers are posted). Do not wait for a
# handshake: the guest has no TCP yet. slirp emits ARP as soon as it
# tries to deliver the SYN; that ARP is the T3.5 payload.
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
