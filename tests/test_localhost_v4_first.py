"""A request to localhost doesn't wait out an IPv6 attempt: local model servers
listen on 127.0.0.1, and every request waited about two seconds for ::1 to be
refused (the window's start, the phone's brain answer).

    .\\.venv\\Scripts\\python.exe tests\\test_localhost_v4_first.py
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import hearth  # noqa: E402,F401

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


fams = [r[0] for r in socket.getaddrinfo("localhost", 80, proto=socket.IPPROTO_TCP)]
check(f"localhost answers IPv4 first ({fams})", fams and fams[0] == socket.AF_INET)
check("other names are left as they are", socket.getaddrinfo("127.0.0.1", 80)[0][0] == socket.AF_INET)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)          # an IPv4-only server, as LM Studio is
threading.Thread(target=srv.serve_forever, daemon=True).start()
t = time.time()
for _ in range(3):
    urllib.request.urlopen(f"http://localhost:{srv.server_address[1]}/", timeout=10).read()
took = time.time() - t
check(f"three requests to an IPv4-only server on localhost are quick ({took:.2f} s)", took < 1.0)
srv.shutdown()

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall localhost checks passed")
