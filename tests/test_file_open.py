"""Files tab: "Open in its app" opens a workspace file and nothing outside it.

    .\\.venv\\Scripts\\python.exe tests\\test_file_open.py
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-fileopen-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
sys.path.insert(0, ROOT)
with open(os.path.join(WS, "deck.pptx"), "wb") as f:
    f.write(b"x")
outside = os.path.join(tempfile.mkdtemp(prefix="hearth-elsewhere-"), "secret.txt")
with open(outside, "w") as f:
    f.write("x")

from hearth import web  # noqa: E402

opened: list = []
web.os.startfile = lambda p: opened.append(p)   # nothing is actually launched

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


srv = web._QuietHTTPServer(("127.0.0.1", 0), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]


def post(body):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("POST", "/api/file/open", json.dumps(body),
              {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    r = c.getresponse()
    out = json.loads(r.read() or b"{}")
    c.close()
    return r.status, out


if sys.platform == "win32":
    st, out = post({"path": "deck.pptx"})
    check(f"a workspace file opens in its app ({st}, {out})", st == 200 and out.get("ok") and opened
          and os.path.basename(opened[0]) == "deck.pptx")
    opened.clear()
    st, out = post({"path": outside})
    check(f"a file outside the workspace is refused ({st}, {out})", st == 400 and not opened)
    st, out = post({"path": "..\\..\\Windows\\win.ini"})
    check("and so is a way out of it", st == 400 and not opened)
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all file open checks passed")
