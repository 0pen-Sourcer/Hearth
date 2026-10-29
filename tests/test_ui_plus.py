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


def wait_for(cond, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


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

    # ------------------------------------------------ calls from the agent
    shots = os.environ.get("CALL_SHOTS") or home
    pg.wait_for_selector("#callme:not([hidden])", timeout=10000)
    check("with Plus, Calls from the agent is there", "Let JARVIS call me" in pg.inner_text("#callme"))
    master = pg.locator('#callme .switch[data-key="call_me"]')
    check("off until it is turned on", "on" not in (master.get_attribute("class") or "")
          and web._load_settings()["call_me"] is False)
    check("with its reasons faded while off", pg.evaluate("getComputedStyle(document.getElementById('callme-more')).opacity") == "0.45")
    check("the reasons start switched on, for when calls are",
          all("on" in (pg.get_attribute(f'#callme .switch[data-key="{k}"]', "class") or "")
              for k in ("call_me_reminders", "call_me_jobs", "call_me_questions", "call_me_quiet")))
    check("quiet hours 22:00 to 08:00, a question waits 3 min",
          pg.input_value("#callme-from") == "22:00" and pg.input_value("#callme-to") == "08:00" and pg.input_value("#callme-wait") == "3")
    check("and says once what a web app's call can't do", "can't play a ringtone" in pg.inner_text("#callme"))
    pg.locator("#callme").scroll_into_view_if_needed()
    pg.screenshot(path=os.path.join(shots, "w1_settings_off_dark.png"))
    master.click()
    check("turned on, it is kept", wait_for(lambda: web._load_settings()["call_me"] is True))
    check("and the reasons come up", pg.evaluate("getComputedStyle(document.getElementById('callme-more')).opacity") == "1")
    pg.select_option("#callme-wait", "5")
    check("the wait is kept", wait_for(lambda: web._load_settings()["call_me_wait_min"] == 5))
    pg.fill("#callme-from", "23:30")
    pg.dispatch_event("#callme-from", "change")
    check("and the quiet hours", wait_for(lambda: web._load_settings()["call_me_quiet_from"] == "23:30"))
    pg.click('#callme .switch[data-key="call_me_jobs"]')
    check("a reason switched off is kept", wait_for(lambda: web._load_settings()["call_me_jobs"] is False))
    from hearth import call_me as CM  # noqa: E402
    check("and the rules read what the window saved", CM.rules()["call_me"] and CM.rules()["call_me_wait_min"] == 5
          and not CM.may_call("job")[0])
    pg.locator("#callme").scroll_into_view_if_needed()
    pg.screenshot(path=os.path.join(shots, "w2_settings_on_dark.png"))
    pg.evaluate("document.documentElement.dataset.theme = 'light'")
    time.sleep(0.4)
    pg.screenshot(path=os.path.join(shots, "w3_settings_on_light.png"))
    pg.evaluate("document.documentElement.dataset.theme = 'dark'")

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
