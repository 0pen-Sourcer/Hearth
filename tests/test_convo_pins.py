"""Pinning a chat.

A pin is stored in the chat file, so it survives a reinstall and reaches every
window. A chat that has not been opened yet can be pinned without loading it,
and a window that loaded the chat before it was pinned must not unpin it by
saving its copy.

    .\\.venv\\Scripts\\python.exe tests\\test_convo_pins.py
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading

WS = tempfile.mkdtemp(prefix="hearth-pins-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import web                                               # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


web.CONVOS_DIR = os.path.join(WS, "conversations")
os.makedirs(web.CONVOS_DIR, exist_ok=True)
with open(os.path.join(web.CONVOS_DIR, "c1.json"), "w", encoding="utf-8") as f:
    json.dump({"id": "c1", "title": "Trip plans", "updated": 1, "created": 1,
               "messages": [{"role": "user", "content": "hi"}]}, f)

srv = web._QuietHTTPServer(("127.0.0.1", 0), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]


def post(path, body):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("POST", path, json.dumps(body),
              {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    r = c.getresponse()
    out = json.loads(r.read() or b"{}")
    c.close()
    return r.status, out


def listed():
    return {c["id"]: c for c in web._list_convos()}


check("a chat starts unpinned", listed()["c1"]["pinned"] is False)
status, out = post("/api/conversations/pin", {"id": "c1", "pinned": True})
check("pinning an unopened chat works", status == 200 and out.get("pinned") is True)
check("the listing reports it pinned", listed()["c1"]["pinned"] is True)

on_disk = json.load(open(os.path.join(web.CONVOS_DIR, "c1.json"), encoding="utf-8"))
check("the rest of the chat is untouched", on_disk["messages"] == [{"role": "user", "content": "hi"}])

# A window that loaded the chat before the pin saves its copy without the field.
stale = {"id": "c1", "title": "Trip plans", "updated": 2, "created": 1,
         "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]}
post("/api/conversations", stale)
check("saving an older copy does not unpin it", listed()["c1"]["pinned"] is True)

status, out = post("/api/conversations/pin", {"id": "c1", "pinned": False})
check("unpinning works", status == 200 and out.get("pinned") is False and listed()["c1"]["pinned"] is False)
status, _ = post("/api/conversations/pin", {"id": "nope", "pinned": True})
check("pinning a chat that doesn't exist is refused", status == 404)

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all pin checks passed")
