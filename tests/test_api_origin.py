"""Another website must not be able to use Hearth's local API.

The API listens on 127.0.0.1. A page on any site that fetches that address
sends Host 127.0.0.1, because Host names the target, so the loopback host
check passed it, and a wildcard CORS header let the page read the response. In
practice that let any open tab read stored API keys back and call tools.

The probe path does not exist, so an allowed request gets 404 and a refused
one gets 403. That keeps the check about the gate rather than any endpoint.

    .\\.venv\\Scripts\\python.exe tests\\test_api_origin.py
"""
from __future__ import annotations

import http.client
import os
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ.pop("JARVIS_ALLOW_LAN", None)

from hearth import web                                              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


srv = ThreadingHTTPServer(("127.0.0.1", 0), web.HearthHandler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
PROBE = "/api/__origin_probe__"


def status(method, headers):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    body = b"{}" if method == "POST" else None
    h = {"Host": f"127.0.0.1:{PORT}"}
    h.update(headers)
    c.request(method, PROBE, body=body, headers=h)
    code = c.getresponse().status
    c.close()
    return code


own = f"http://127.0.0.1:{PORT}"
for m in ("GET", "POST", "DELETE"):
    check(f"{m} from another site is refused",
          status(m, {"Origin": "https://evil.example"}) == 403)
    check(f"{m} from Hearth's own page is let through",
          status(m, {"Origin": own}) != 403)
    check(f"{m} from a non-browser client, with no Origin, is let through",
          status(m, {}) != 403)

check("a sandboxed frame or file page (Origin null) is refused",
      status("GET", {"Origin": "null"}) == 403)
check("DNS rebinding, where Host and Origin both name the attacker, is refused",
      status("GET", {"Host": f"evil.example:{PORT}",
                     "Origin": f"http://evil.example:{PORT}"}) == 403)
check("a preflight is never granted", status("OPTIONS", {"Origin": "https://evil.example"}) == 403)

src = open(os.path.join(ROOT, "hearth", "web.py"), encoding="utf-8").read()
check("no wildcard CORS header is sent anywhere",
      'Access-Control-Allow-Origin", "*"' not in src)

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all API origin checks passed")
