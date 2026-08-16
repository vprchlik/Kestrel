#!/usr/bin/env bash
# Fire a TCP connect at the hostfwd port.
#
# After D-0054 the guest ARPs 10.0.2.2 itself; slirp need not ask us.
# The net-init watcher fires this once after `gateway 10.0.2.2 MAC
# learned` so the SYN is not dropped as noarp (D-0046). Timeout is
# long enough for SYN/ACK during `wait_tcp_handshake`, then close()s
# with FIN. The kernel closes that unused TCB so LISTEN is restored
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
