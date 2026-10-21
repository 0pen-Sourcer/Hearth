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

    # Open with: only an app Windows lists for the file is ever started.
    from hearth import openers  # noqa: E402
    launched = []
    openers.subprocess.Popen = lambda argv, **kw: launched.append(argv)
    openers.openers = lambda p: [{"exe": r"C:\Apps\Editor.exe", "name": "Editor", "default": True}]
    st, out = post({"path": "deck.pptx", "exe": r"C:\Apps\Editor.exe"})
    check(f"a listed app opens the file ({st}, {launched})", st == 200 and launched
          and launched[0][0] == r"C:\Apps\Editor.exe" and launched[0][1].endswith("deck.pptx"))
    launched.clear()
    st, out = post({"path": "deck.pptx", "exe": r"C:\Windows\System32\cmd.exe"})
    check(f"any other program is refused ({st}, {out})", st != 200 and not launched)

    # Show in folder finds a Files-tab path from the workspace down.
    import subprocess as _sub  # noqa: E402
    shown = []
    real_popen = _sub.Popen
    _sub.Popen = lambda argv, **kw: shown.append(argv)
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("POST", "/api/file/reveal", json.dumps({"path": "deck.pptx"}),
              {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    r = c.getresponse(); r.read(); c.close()
    _sub.Popen = real_popen
    check(f"show in folder finds a workspace path ({r.status}, {shown})",
          r.status == 200 and shown
          and os.path.join(os.path.realpath(WS), "deck.pptx").lower() in " ".join(map(str, shown[0])).lower())
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all file open checks passed")
