"""An opened memory says who saved it (noticed on its own, or saved on purpose)
and the chat it came from, which opens that chat.

    .\\.venv\\Scripts\\python.exe tests\\test_memory_fact_view.py [screenshot-dir]
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
work = tempfile.mkdtemp(prefix="hearth-memview-")
os.environ.update(HEARTH_WORKSPACE=work, JARVIS_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skip: playwright not installed")
    sys.exit(0)

from hearth import web  # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True))
os.makedirs(os.path.join(work, "conversations"), exist_ok=True)
with open(os.path.join(work, "conversations", "c_trip.json"), "w", encoding="utf-8") as f:
    json.dump({"id": "c_trip", "title": "Plan the Goa trip", "created": 1, "updated": 2,
               "messages": [{"role": "user", "content": "plan it"}, {"role": "assistant", "content": "ok"}]}, f)
NOTICED = ("---\nname: budget\ndescription: Trip budget is 30k\ntype: project\ntags: [auto-extracted, cat:ongoing]\n"
           "updated: 2026-10-28T10:00:00\nsource_chat: c_trip\nrecall_count: 2\n---\nTrip budget is 30k\n")
ON_PURPOSE = ("---\nname: editor\ndescription: Uses VS Code\ntype: user\ntags: [cat:tools]\n"
              "updated: 2026-10-20T10:00:00\nsource_chat: c_gone\n---\nUses VS Code\n")

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", args=["--disable-gpu"])
    pg = b.new_page(viewport={"width": 1200, "height": 760})
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=40000); break
        except Exception:
            time.sleep(2)
    pg.wait_for_function("STATE.conversations && STATE.conversations.some(c => c.id === 'c_trip')", timeout=30000)
    a = pg.evaluate("raw => { const d = document.createElement('div'); d.innerHTML = memFactHTML(raw); return d.innerText; }", NOTICED)
    check(f"one the memory pass noticed says so ({a!r})", "Noticed on its own" in a)
    check("and names the chat it came from", "From Plan the Goa trip" in a)
    c = pg.evaluate("raw => { const d = document.createElement('div'); d.innerHTML = memFactHTML(raw); return d.innerText; }", ON_PURPOSE)
    check(f"one saved on purpose says so ({c!r})", "Saved on purpose" in c)
    check("a chat that's gone isn't linked", "From" not in c)
    pg.evaluate("raw => showModal('budget', memFactHTML(raw), [{ label: 'Close' }])", NOTICED)
    time.sleep(0.6)
    if len(sys.argv) > 1:
        pg.screenshot(path=os.path.join(sys.argv[1], "memory_fact.png"))
    pg.click(".mf-src")
    time.sleep(1.2)
    opened = pg.evaluate("({ id: STATE.activeId, modal: $('modal').classList.contains('open') })")
    check(f"the link opens that chat and closes the card ({opened})", opened["id"] == "c_trip" and not opened["modal"])
    b.close()
srv.shutdown()

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall memory fact view checks passed")
