"""The window starts without a script error.

Parsing is not enough. The page runs one long script top to bottom, and a line
near the top that touches something declared further down throws, which stops
everything after it: no chats, no model list, no working buttons, and nothing
on screen says why. This loads the real page in a real browser and fails on
any error it raises while starting, in each look.

Needs Playwright and Microsoft Edge; skipped where either is missing.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_boot.py
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except Exception:
    print("skip  playwright is not installed")
    sys.exit(0)

from hearth import web                                              # noqa: E402

s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skip  no Edge to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(0)
    # (look saved in settings, look the window remembers, look expected)
    # The window's own storage does not survive every restart, so a look saved
    # in settings has to win over a stale or empty one. Nothing saved anywhere
    # means Classic.
    real_load = web._load_settings
    cases = [("classic", "", "classic"), ("dark", "", "dark"), ("light", "", "light"),
             ("classic", "dark", "classic"), ("", "", "classic"), ("", "light", "light")]
    for saved, local, theme in cases:
        web._load_settings = (lambda sv=saved: dict(real_load(), ui_theme=sv or None))
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_init_script(
            "try{%slocalStorage.setItem('hearth_onboarded','1')}catch(e){}"
            % (f"localStorage.setItem('hearth_theme','{local}');" if local else ""))
        page = ctx.new_page()
        # Never start a real download from a test.
        page.route("**/api/llmserver/download*", lambda r: r.abort())
        errors: list = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # The first request on a fresh context occasionally stalls on Windows
        # loopback; a second try goes straight through.
        for attempt in range(3):
            try:
                page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=20000)
                break
            except Exception:
                if attempt == 2:
                    raise
        time.sleep(3)
        tag = f"saved {saved or 'nothing'}, window had {local or 'nothing'}"
        check(f"{tag}: no script error while starting"
              + (f" ({errors[0][:160]})" if errors else ""), not errors)
        # The composer's buttons are wired late in the script, so a throw
        # anywhere above leaves them dead even though they are drawn.
        check(f"{tag}: the approval menu opens",
              page.evaluate("document.getElementById('btn-mode').click(),"
                            " document.getElementById('mode-menu').classList.contains('open')"))
        shown = page.evaluate("document.documentElement.dataset.theme")
        check(f"{tag}: shows {theme} (got {shown})", shown == theme)
        ctx.close()
    web._load_settings = real_load
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all window start checks passed")
