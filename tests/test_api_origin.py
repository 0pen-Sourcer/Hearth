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


class _Quiet(web.HearthHandler):
    """The handler logs every request to stderr. Run from a terminal that is
    nothing but a pipe, which is how this runs under CI and under a loop that
    redirects, that write blocks whenever the reader is behind, and it blocks
    inside the request, so the client on the other end sees a reset partway
    through a response that the server has already logged as answered. Silence
    it: this test is about which requests are allowed, not about the log."""

    def log_message(self, *_a, **_kw):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
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

# An unknown path answers JSON, not an HTML error page. Every caller here is
# the window, which reads JSON, so the page reached it as a parse failure with
# no status in it and the real problem was invisible.
import json as _json                                                # noqa: E402

_c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
_c.request("GET", "/api/definitely-not-a-real-endpoint",
           headers={"Host": f"127.0.0.1:{PORT}", "Origin": own})
_r = _c.getresponse()
_ct = _r.getheader("Content-Type") or ""
_body = _r.read().decode("utf-8", "replace")
_c.close()
check("an unknown endpoint answers 404", _r.status == 404)
check("and answers it as JSON", "application/json" in _ct)
try:
    check("with the path in it",
          "definitely-not-a-real-endpoint" in (_json.loads(_body).get("error") or ""))
except Exception as e:
    check(f"with the path in it ({type(e).__name__})", False)

srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all API origin checks passed")
