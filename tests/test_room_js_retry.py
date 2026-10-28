"""The window keeps working when its first request for room.js is cut off (some
antivirus resets bursts of local requests), and the room loads on the second ask.

    .\\.venv\\Scripts\\python.exe tests\\test_room_js_retry.py
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
work = tempfile.mkdtemp(prefix="hearth-roomjs-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = work
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

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
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", args=["--disable-gpu"])
    pg = b.new_page()
    errs, cut = [], []

    def once(route):
        if not cut:
            cut.append(route.request.url)
            route.abort("connectionreset")
        else:
            route.continue_()
    pg.route("**/room.js*", once)
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    for _ in range(3):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=40000)
            break
        except Exception:
            time.sleep(2)
    time.sleep(6)
    got = pg.evaluate("() => ({ marks: typeof brainMarkHTML, room: typeof roomStart, host: typeof ROOM_HOST.look })")
    check(f"the first room.js was cut off ({len(cut)})", len(cut) == 1)
    check(f"the rest of the window still ran, without errors ({errs[:2]})",
          got["marks"] == "function" and not errs)
    check(f"and the room came on the second ask ({got})", got["room"] == "function" and got["host"] == "function")
    b.close()
srv.shutdown()

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall room.js retry checks passed")
