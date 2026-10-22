"""Settings > Plus in the real page, on the real routes.

Without Plus it asks for the licence key and turns Plus on with it (a stand-in
licence server says the key is good). With Plus, Pair a phone shows a QR code
holding the pairing link; when a phone has scanned it the six digits show by
themselves, and Yes, confirm pairs the phone under the name typed. The phone can
be forgotten, and Plus moved off the PC.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_plus.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class Dodo(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        good = body.get("license_key") == "GOOD-KEY"
        if self.path.endswith("/activate"):
            code, out = (201, {"id": "lki_test"}) if good else (404, {})
        else:
            code, out = 200, {"valid": good}
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


dodo = ThreadingHTTPServer(("127.0.0.1", 0), Dodo)
threading.Thread(target=dodo.serve_forever, daemon=True).start()

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skipped: no browser ({type(e).__name__})")
        sys.exit(0)
    # Hearth's own files from here on live in a throwaway home (the browser has its profile already).
    home = tempfile.mkdtemp(prefix="hearth-plus-ui-")
    os.environ["HOME"] = os.environ["USERPROFILE"] = home
    os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(home, "Jarvis")
    os.environ["HEARTH_DODO_BASE"] = f"http://127.0.0.1:{dodo.server_address[1]}"
    os.environ["HEARTH_RELAY_URL"] = "https://relay.example"
    from hearth import web, relay_bridge as RB  # noqa: E402
    web._save_settings(dict(web._load_settings(), onboarded=True))
    spawned: list = []
    web._spawn_bridge = lambda ch: spawned.append(ch) or (True, None)     # no real relay to reach here
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    pg = browser.new_page(viewport={"width": 1280, "height": 860})
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    time.sleep(2)
    pg.evaluate("showView('settings')")
    pg.click(".settings-side-btn[data-st=plus]")
    pg.wait_for_selector("#plus-key", timeout=10000)
    check("without Plus it asks for the licence key", "Get Plus" in pg.inner_text("#plus-body"))

    pg.fill("#plus-key", "WRONG")
    pg.click("#plus-on")
    pg.wait_for_selector("#plus-key", timeout=10000)
    time.sleep(0.5)
    check("a wrong key leaves Plus off", "Plus is on" not in pg.inner_text("#plus-body"))

    pg.fill("#plus-key", "GOOD-KEY")
    pg.click("#plus-on")
    pg.wait_for_selector("#plus-pair", timeout=10000)
    check("the right key turns Plus on", "Plus is on" in pg.inner_text("#plus-body"))

    pg.click("#plus-pair")
    pg.wait_for_selector("#plus-body svg", timeout=10000)
    pending = json.load(open(RB.STATE_FILE))["pending"]
    check("Pair a phone shows a QR code and starts the relay bridge", spawned == ["relay"] and pending.get("room"))
    pg.screenshot(path=os.path.join(home, "plus_qr.png"))

    # A phone scans it: the PC learns its key and the six digits (as the relay bridge records them).
    d = json.load(open(RB.STATE_FILE))
    d["pending"].update(phone="cGhvbmUta2V5LWJ5dGVzLWZvci10aGUtdGVzdA", digits="482913")
    with open(RB.STATE_FILE, "w") as f:
        json.dump(d, f)
    pg.wait_for_selector("#plus-confirm", timeout=10000)
    check("once a phone has scanned it the digits show by themselves", "482 913" in pg.inner_text("#plus-body"))
    pg.screenshot(path=os.path.join(home, "plus_digits.png"))
    pg.fill("#plus-phone-name", "Pixel")
    pg.click("#plus-confirm")
    pg.wait_for_selector("[data-plus-forget]", timeout=10000)
    check("Yes, confirm pairs the phone under its name", [p["name"] for p in RB.phones()] == ["Pixel"]
          and "Pixel" in pg.inner_text("#plus-body"))

    pg.click("[data-plus-forget]")
    pg.wait_for_function("!document.querySelector('[data-plus-forget]')", timeout=10000)
    check("a phone can be forgotten", RB.phones() == [])

    pg.click("#plus-off")
    pg.click(".modal button:has-text('Move it')")
    pg.wait_for_selector("#plus-key", timeout=10000)
    check("Plus can be moved off this PC", not os.path.exists(os.path.join(home, ".hearth", "plus.json")))
    check(f"no script errors {errors[:2]}", not errors)
    print("screenshots in", home)
    browser.close()
    srv.shutdown()

dodo.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all Plus settings checks passed")
