"""What's new shows once after an update from v0.7.5, never on a fresh install,
and opens again from Settings, About.

An install set up on v0.7.5 has been through setup and has never seen the
screen, so it is an update from Hearth and opens on "Hearth is now Inglen".
A new install is marked seen on its first start, and setup marks it too, so
someone who set up in the terminal doesn't get it in the window later.

    .\\.venv\\Scripts\\python.exe tests\\test_whats_new.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import memory, onboarding, web, whats_new  # noqa: E402
from hearth.updater import HEARTH_VERSION  # noqa: E402

cur = whats_new.release(HEARTH_VERSION)

# ---------------------------------------------------------------- the rule
st, save = whats_new.status({})
check("a fresh install is not shown it, and is marked seen", not st["show"] and save == {"whats_new_seen": cur})
st, save = whats_new.status({"onboarded": True})
check("an install set up on v0.7.5 is shown it, as an update from Hearth",
      st["show"] and st["from_hearth"] and save.get("whats_new_from_hearth") is True)
st, _ = whats_new.status({"onboarded": True, "whats_new_seen": cur, "whats_new_from_hearth": True})
check("once seen it stays shut, and still knows it came from Hearth", not st["show"] and st["from_hearth"])
st, _ = whats_new.status({"onboarded": True, "whats_new_seen": "0.7.6"}, version="0.7.7-preview")
check("the next release shows it again, not as a rename", st["show"] and not st["from_hearth"])

work = os.environ["JARVIS_WORKSPACE"]
memory.PROFILE_PATH = os.path.join(work, "profile.md")
memory.RULES_PATH = os.path.join(work, "rules.md")
saved: dict = {}
real_save = web._save_settings
web._save_settings = lambda d: saved.update(d) or saved
onboarding.apply({"name": "Asha"})
web._save_settings = real_save
check("setup marks it seen, so a terminal setup doesn't get it in the window later",
      saved.get("onboarded") and saved.get("whats_new_seen") == cur
      and not whats_new.status(saved)[0]["show"])

# ---------------------------------------------------------------- the clips travel with the app
import build_patch  # noqa: E402
packed = {arc for _src, arc in build_patch.code_members()}
clips = ("computer", "phone", "memory", "brains", "team", "voice")
check("each clip and its still go out with an update",
      all(f"assets/whats_new/{c}.{ext}" in packed for c in clips for ext in ("webm", "jpg")))
check("and each clip is small",
      all(os.path.getsize(os.path.join(ROOT, "assets", "whats_new", c + ".webm")) < 1_500_000 for c in clips))

# ---------------------------------------------------------------- the window's API
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def get(path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


def post(path):
    req = urllib.request.Request(base + path, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def reset(**kw):
    s = {k: v for k, v in web._load_settings().items()
         if k not in ("onboarded", "whats_new_seen", "whats_new_from_hearth")}
    s.update(kw)
    from hearth import keystore
    keystore.write_settings(s, web.SETTINGS_PATH)


reset()
check("fresh install: the window is told not to show it", get("/api/whats_new")["show"] is False)
check("and asking again still says no", get("/api/whats_new")["show"] is False)

reset(onboarded=True)
r = get("/api/whats_new")
check(f"update from v0.7.5: shown, as a rename {r}", r["show"] and r["from_hearth"] and r["version"] == cur)
check("not dismissed yet: shown again on the next start", get("/api/whats_new")["show"])
post("/api/whats_new/seen")
check("dismissed: not shown again", get("/api/whats_new")["show"] is False)
with urllib.request.urlopen(base + "/assets/whats_new/team.webm", timeout=30) as r:
    ctype, body = r.headers.get("Content-Type"), r.read()
check("the clips are served as video", ctype == "video/webm" and len(body) > 10000)

# ---------------------------------------------------------------- the screen
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped the window: playwright is not installed")
    srv.shutdown()
    sys.exit(1 if FAILS else 0)

shots = os.environ.get("HEARTH_SHOTS") or tempfile.mkdtemp(prefix="inglen-whatsnew-shots-")
reset(onboarded=True)
with sync_playwright() as pw:
    browser = None
    # Software drawing: a busy graphics card stalls headless screenshots.
    for kw in ({"channel": "msedge", "args": ["--disable-gpu"]}, {"args": ["--disable-gpu"]}):
        try:
            browser = pw.chromium.launch(**kw)
            break
        except Exception:
            pass
    if browser is None:
        print("skipped the window: no browser")
        srv.shutdown()
        sys.exit(1 if FAILS else 0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    def load():
        # Some antivirus stalls a large local response now and then; try again.
        for _ in range(4):
            try:
                pg.goto(base + "/", wait_until="domcontentloaded", timeout=25000)
                pg.wait_for_function("typeof openWhatsNew === 'function'", timeout=25000)
                return
            except Exception as e:
                last = e
        raise last

    load()
    pg.wait_for_selector("#whatsnew.open", timeout=15000)
    pg.wait_for_timeout(2600)
    pg.screenshot(path=os.path.join(shots, "1_rename.png"))
    check("after the update it opens by itself, on the rename",
          pg.is_visible("#wn-intro") and "Hearth is now Inglen" in pg.inner_text("#wn-intro"))
    pg.click("#wn-go")
    pg.wait_for_timeout(900)
    check("then the changes, six of them, each with its clip",
          pg.is_visible("#wn-feats") and pg.locator("#wn-list .wn-item").count() == 6
          and pg.evaluate("() => $('wn-video').src.endsWith('/assets/whats_new/computer.webm')"))
    pg.click("#wn-list .wn-item:nth-of-type(2)")
    pg.wait_for_timeout(900)
    check("picking one swaps the clip and the words",
          pg.evaluate("() => $('wn-video').src.endsWith('/assets/whats_new/phone.webm')")
          and "phone" in pg.inner_text("#wn-p"))
    pg.click("#wn-done")
    pg.wait_for_timeout(600)
    check("Done closes it", not pg.evaluate("() => $('whatsnew').classList.contains('open')"))

    load()
    pg.wait_for_timeout(3000)
    check("next start: it stays shut", not pg.evaluate("() => $('whatsnew').classList.contains('open')"))
    pg.evaluate("() => $('btn-whats-new').click()")
    pg.wait_for_selector("#whatsnew.open", timeout=10000)
    check("Settings, About opens it again", pg.evaluate("() => $('whatsnew').classList.contains('open')"))
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(500)
    check("Escape closes it", not pg.evaluate("() => $('whatsnew').classList.contains('open')"))
    check(f"no page errors {errors[:2]}", not errors)
    browser.close()
srv.shutdown()

print(f"\nshots in {shots}")
print("\nOK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
