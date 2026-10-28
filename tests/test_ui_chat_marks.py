"""Each chat in the window's list is marked by where it started: a phone for
the phone app, the chat app's own icon for Telegram, Discord and WhatsApp, a
clock for a reminder, a prompt for the terminal, a bubble for the window.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_chat_marks.py
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
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="hearth-marks-")
os.environ["JARVIS_WORKSPACE"] = os.environ["HEARTH_WORKSPACE"] = work
from hearth import chat_sync, web  # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True))
os.makedirs(chat_sync.CONVOS_DIR, exist_ok=True)
now = time.time() * 1000
for n, (cid, source, title) in enumerate([("c_phone", "relay", "Hello from the phone"),
                                          ("c_tg", "telegram", "From Telegram"),
                                          ("rem_x", "reminder", "Stand up and stretch"),
                                          ("c_term", "terminal", "In the terminal"),
                                          ("c_win", "", "In the window")]):
    with open(os.path.join(chat_sync.CONVOS_DIR, cid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": cid, "title": title, "source": source, "updated": now - n * 1000,
                   "messages": [{"role": "user", "content": "hi", "at": now}]}, f)

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge", args=["--disable-gpu"])
    except Exception as e:
        print(f"skipped: no browser ({type(e).__name__})")
        sys.exit(0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_selector('.convo[data-cid="c_phone"]', timeout=20000)
    marks = pg.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.convo[data-cid]')].map(d =>
      [d.dataset.cid, { title: d.title, html: (d.firstElementChild || {}).outerHTML || '' }]))""")
    phone = marks.get("c_phone", {})
    check(f"a chat started on the phone carries a phone ({phone.get('title')})",
          phone.get("title") == "Started on your phone" and "<rect" in phone.get("html", ""))
    tg = marks.get("c_tg", {})
    check("a Telegram chat carries Telegram's own icon", "brand-icon?k=telegram" in tg.get("html", "")
          and tg.get("title") == "From Telegram")
    check("a reminder keeps its clock", "circle" in marks.get("rem_x", {}).get("html", ""))
    check("the terminal keeps its prompt", "M4 17l6-5-6-5" in marks.get("c_term", {}).get("html", ""))
    check("a window chat keeps its bubble", "M21 11.5" in marks.get("c_win", {}).get("html", ""))
    browser.close()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat mark checks passed")
