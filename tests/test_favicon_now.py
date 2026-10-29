"""A site's icon is answered at once: from disk when it is there, and when it isn't,
"none for now" while it is fetched in the background, so the next ask has it.

C20, measured on the owner's PC: at start the window asked /api/favicon for a few sites
and each took up to 15 s (four URLs tried with 4-5 s timeouts), holding the browser's
six connections, so the Team room's art waited behind them.

    .\\.venv\\Scripts\\python.exe tests\\test_favicon_now.py
"""
from __future__ import annotations

import http.client
import io
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="hearth-favicon-")
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=os.path.join(HOME, "Inglen"),
                  JARVIS_WORKSPACE=os.path.join(HOME, "Inglen"), HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
os.environ.pop("JARVIS_ALLOW_LAN", None)
sys.path.insert(0, ROOT)

from hearth import web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 200
asked: list = []


class Slow(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return "https://slow.example/"


def slow_urlopen(req, timeout=None):
    url = getattr(req, "full_url", req)
    asked.append(url)
    time.sleep(2.0)                                     # a site that answers slowly
    if url.endswith("/favicon.ico") and "slow.example" in url:
        return Slow(PNG)
    raise OSError("no")


web.urllib.request.urlopen = slow_urlopen


class Quiet(web.HearthHandler):
    def log_message(self, *_a, **_kw):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(path):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    t = time.time()
    c.request("GET", path, headers={"Host": f"127.0.0.1:{PORT}"})
    r = c.getresponse()
    body = r.read()
    return r.status, dict(r.getheaders()), body, time.time() - t


st, hd, body, took = get("/api/favicon?d=slow.example")
check(f"a site not looked up yet is answered at once ({st} in {took:.2f}s)", took < 0.5 and st == 404)
check(f"and the browser doesn't keep that answer ({hd.get('Cache-Control')})", hd.get("Cache-Control") == "no-store")
check(f"and it says the icon is on its way, so the page keeps waiting ({hd.get('X-Icon')})", hd.get("X-Icon") == "later")
os.makedirs(web._FAVICON_DIR, exist_ok=True)
open(os.path.join(web._FAVICON_DIR, "gone.example.miss"), "w").close()
st3, hd3, _, _ = get("/api/favicon?d=gone.example")
check(f"a site known to have none says so, so the page stops waiting ({st3}, {hd3.get('X-Icon')})",
      st3 == 404 and hd3.get("X-Icon") == "none")
st2, _, _, took2 = get("/api/favicon?d=slow.example")
check(f"asked again while it's being fetched: still at once, one fetch ({took2:.2f}s, {len(asked)} asked)",
      took2 < 0.5 and len(asked) == 1)
time.sleep(2.6)
st, hd, body, took = get("/api/favicon?d=slow.example")
check(f"once fetched, it comes from disk at once ({st}, {hd.get('Content-Type')}, {took:.2f}s)",
      st == 200 and body == PNG and hd.get("Content-Type") == "image/png" and took < 0.5)
st, hd, _, took = get("/api/favicon?d=nothing.example")
time.sleep(0.1)
check(f"many sites at once don't wait on each other ({took:.2f}s)", took < 0.5)
st, _, _, took = get("/api/brand-icon?k=claude")
check(f"a brand's mark is answered at once too ({st} in {took:.2f}s)", took < 0.5)
check("the phone's batched lookup still waits for the icon (web._favicon)",
      web._favicon("slow.example") == (PNG, "image/png"))

srv.shutdown()
print("\n" + ("all favicon checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
