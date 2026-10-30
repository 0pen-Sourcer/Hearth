"""web_fetch and browse don't reach this PC or the local network unless the person named the address.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_local_fetch.py
"""
from __future__ import annotations

import http.server
import os
import socket
import sys
import tempfile
import threading

os.environ["HEARTH_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-localfetch-")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import auto_review, outbound, tools        # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class _H(http.server.BaseHTTPRequestHandler):
    target = ""

    def do_GET(self):
        if self.path.startswith("/go") and _H.target:
            self.send_response(302)
            self.send_header("Location", _H.target)
            self.end_headers()
            return
        body = b"<html><body>router admin secret</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]


a, b = serve(), serve()


def turn(prompt):
    auto_review.begin_turn("t", prompt, [], None)


turn("what's the weather like")
r = tools._web_fetch({"url": f"http://127.0.0.1:{a}/"})
check("a port on this PC nobody named is refused", "secret" not in r and "this PC" in r)
r = tools._web_fetch({"url": f"http://localhost:{a}/"})
check("localhost spelled as a name is refused too", "secret" not in r)

turn(f"check what my dev server at localhost:{a} says")
r = tools._web_fetch({"url": f"http://127.0.0.1:{a}/"})
check("the port the person named opens (as 127.0.0.1 too)", "secret" in r)
r = tools._web_fetch({"url": f"http://127.0.0.1:{b}/"})
check("another port on this PC still doesn't", "secret" not in r)
_H.target = f"http://127.0.0.1:{b}/"
r = tools._web_fetch({"url": f"http://localhost:{a}/go"})
check("a redirect from a named port to another one is refused", "redirected" in r and "secret" not in r)

turn("look at the news")
for u in ("http://192.168.1.1/", "http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data",
          "http://[fe80::1]/", "http://[fd12:3456::1]/", "http://172.16.0.1/", "http://0.0.0.0:80/",
          "http://[::ffff:127.0.0.1]/", "http://2130706433/"):
    check(f"{u} is refused", bool(outbound.local_refusal(u)))
check("a public address is not", not outbound.local_refusal("https://93.184.215.14/"))

real = socket.getaddrinfo


def fake(host, *a, **k):
    if host == "rebind.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 0))]
    return real(host, *a, **k)


socket.getaddrinfo = fake
try:
    check("a name that resolves to the local network is refused", bool(outbound.local_refusal("http://rebind.example/")))
    r = tools._browse({"url": "http://rebind.example/"})
    check("browse refuses it before opening anything", "local network" in r)
    turn("open my router at 192.168.0.10 please")
    check("an address the person typed is allowed", not outbound.local_refusal("http://192.168.0.10/"))
    seen = outbound.from_history([{"role": "tool", "name": "web_fetch", "content": "visit http://192.168.0.10/"}], "hi")
    check("an address that only came from a page is not the person's", bool(outbound.local_refusal("http://192.168.0.10/", seen)))
finally:
    socket.getaddrinfo = real
auto_review.end_turn()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all local fetch checks passed")
