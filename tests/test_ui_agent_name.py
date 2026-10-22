"""A renamed agent is called by its name everywhere in the window.

The agent is JARVIS by default and can be renamed. Page headers, hints, toasts
and empty states said "JARVIS" whatever the name was. With the agent called
Friday, no text in the page (hidden panes included) says JARVIS, and the
placeholders say Friday.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_agent_name.py
"""
from __future__ import annotations

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

work = tempfile.mkdtemp(prefix="hearth-name-")
os.environ["JARVIS_WORKSPACE"] = os.environ["HEARTH_WORKSPACE"] = work
from hearth import web  # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True, agent_name="Friday"))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

LEFT = """() => {
  const out = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement && n.parentElement.closest('script, style, template, .ob, #onboarding')
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  while (walk.nextNode()) if (/\\bJARVIS\\b/.test(walk.currentNode.textContent)) out.push(walk.currentNode.parentElement.outerHTML.slice(0, 160));
  return out;
}"""

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge", args=["--disable-gpu"])
    except Exception as e:
        print(f"skipped: no browser ({type(e).__name__})")
        sys.exit(0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    pg.wait_for_function("typeof STATE !== 'undefined' && STATE.settings && STATE.settings.agent_name === 'Friday'", timeout=20000)
    time.sleep(1.5)
    for view in ("team", "reminders", "skills", "logs", "settings"):
        try:
            pg.evaluate(f"showView('{view}')")
        except Exception:
            pass
        time.sleep(0.4)
    left = pg.evaluate(LEFT)
    check(f"no text in the window says JARVIS when the agent is Friday {left[:4]}", not left)
    check("the Team room's box says Friday", "Friday" in (pg.get_attribute("#tm-input", "placeholder") or ""))
    check("the message box says Friday", "Friday" in (pg.get_attribute("#input", "placeholder") or ""))
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent name checks passed")
