"""Early systems' screens say a label and a short line, and nothing about how they work.

From the C29 review: Skills opened with a paragraph on the file format, an empty
"For you" explained how ideas are learned, About pointed at a Support tab that
doesn't exist and called Inglen Windows-only, start at login was a button that
read "Off - turn on" and toasted what it had just shown, and the shortcuts list
stopped at Ctrl+8 with nine tabs. Chromium, a temp home, no model.

    python tests/test_early_systems_words.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-c29-home-")
WS = os.path.join(HOME, "Inglen")
os.makedirs(WS)
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "tour_done": True}, f)
sys.path.insert(0, ROOT)

from hearth import web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


srv = web.serve("127.0.0.1", 0)
port = srv.server_address[1]

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", args=["--disable-gpu"]) if sys.platform == "win32" else pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    autostart = {"enabled": False, "asked": []}
    saved = []

    def on_autostart(route):
        body = json.loads(route.request.post_data or "{}")
        if "enabled" in body:
            autostart["asked"].append(body["enabled"])
            autostart["enabled"] = bool(body["enabled"])
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "enabled": autostart["enabled"]}))

    def on_settings(route):
        if route.request.method == "POST":
            saved.append(route.request.post_data or "")
        route.continue_()

    pg.route("**/api/autostart", on_autostart)
    pg.route("**/api/settings", on_settings)
    pg.route("**/api/reminders/ideas", lambda r: r.fulfill(status=200, content_type="application/json",
                                                           body='{"ideas": []}'))
    pg.goto(f"http://127.0.0.1:{port}/")
    pg.wait_for_function("typeof showView === 'function'", timeout=60000)
    time.sleep(1.5)

    pg.click('.nav-btn[data-view="skills"]')
    time.sleep(0.5)
    check("Skills has no paragraph about the format", pg.locator("#view-skills .sk-note").count() == 0)

    pg.click('.nav-btn[data-view="reminders"]')
    pg.wait_for_function("RM_IDEAS.list !== null", timeout=20000)
    time.sleep(0.3)
    ideas = pg.inner_text("#rm-ideas").strip()
    check(f"with no ideas, For you stays empty ({ideas!r})", ideas == "")

    pg.click('.nav-btn[data-view="settings"]')
    pg.click('.settings-side-btn[data-st="about"]')
    time.sleep(0.8)
    about = pg.inner_text('.settings-pane[data-st="about"]')
    check("About names no Support tab", "Support tab" not in about)
    check("About doesn't say Inglen is for Windows", "for Windows" not in about)
    check("setup is run again with a plain button", pg.inner_text("#btn-replay-onboard").strip() == "Run setup")
    last = pg.inner_text(".kbd-row >> nth=0 >> kbd >> nth=-1").strip()
    navs = pg.locator(".nav-btn[data-view]").count()
    check(f"the shortcut list covers every tab (Ctrl+1 to {last}, {navs} tabs)", last == str(navs))

    sw = pg.locator("#btn-autostart-toggle")
    check("start at login is a switch", "switch" in (sw.get_attribute("class") or "")
          and sw.get_attribute("role") == "switch" and "turn on" not in sw.inner_text())
    # Settings wires the switch after its earlier awaits, which take longer on Windows.
    pg.wait_for_function("!!document.getElementById('btn-autostart-toggle').onclick", timeout=60000)
    toasts_before = pg.locator(".toast").count()
    sw.click()
    pg.wait_for_function("document.getElementById('btn-autostart-toggle').classList.contains('on')", timeout=5000)
    check("a click asks for it on and shows it on", autostart["asked"] == [True]
          and sw.get_attribute("aria-checked") == "true")
    time.sleep(0.4)
    check("and no toast repeats it", pg.locator(".toast").count() == toasts_before)
    check(f"the switch saves no setting of its own {saved}", not any("undefined" in s or '"null"' in s for s in saved))
    pg.evaluate("refreshSettings(); 0")
    time.sleep(0.8)
    check("reloading settings leaves it on", "on" in (sw.get_attribute("class") or ""))
    sw.click()
    pg.wait_for_function("!document.getElementById('btn-autostart-toggle').classList.contains('on')", timeout=5000)
    check("a second click turns it off", autostart["asked"] == [True, False])

    pg.click('.settings-side-btn[data-st="voice"]')
    time.sleep(0.5)
    long_hints = [h for h in pg.locator('.settings-pane[data-st="voice"] .field .hint').all_inner_texts()
                  if any(w in h for w in ("Bluetooth", "tray", "terminal", "commas", "processor")) and len(h) > 70]
    check(f"the wake word and speaker hints are one short line {long_hints}", not long_hints)
    pg.click('.settings-side-btn[data-st="connect"]')
    time.sleep(0.5)
    intro = pg.inner_text('.settings-pane[data-st="connect"] .settings-sub')
    check(f"Connectors opens with one line ({intro!r})", len(intro) <= 60)
    b.close()

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all early systems word checks passed")
