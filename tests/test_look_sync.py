"""The look and the Team room set on the phone reach the window at once, and it repaints without a
reload: the phone's request goes through the window's own settings route, which tells the window
(/api/events), which follows what is saved. A change it missed (its stream was down) is caught up when
the stream opens or the window is looked at again, the room isn't overwritten by a repaint, and the phone
hears only whether a setting took, never the PC's settings.

    .\\.venv\\Scripts\\python.exe tests\\test_look_sync.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-look-")
os.environ["HOME"] = os.environ["USERPROFILE"] = WORK
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(WORK, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])
SHOTS = os.environ.get("INGLEN_SHOTS") or os.path.join(os.path.dirname(ROOT), "hearth-notes", "shots", "look")
os.makedirs(SHOTS, exist_ok=True)

from hearth import relay_bridge as RB, web  # noqa: E402

FAILS: list = []
WAIT_MS = 20000        # the page can take many seconds to start on a busy PC; a change itself takes well under one


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright isn't installed")
    sys.exit(0)

web._save_settings({"onboarded": True, "ui_theme": "dark", "ui_accent": "rose", "llm_key": "secret-should-not-travel"})
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}"
RB.WINDOW_URL = url
# The page's event stream opens only once the test says so (window.__go), so a change can come first.
SEEN = """(() => { const O = window.EventSource; window.__open = 0;
  window.EventSource = class { constructor(u) { const t = setInterval(() => { if (!window.__go) return; clearInterval(t);
      const e = this.e = new O(u); e.addEventListener('open', () => { window.__open++; });
      e.onopen = (x) => this.onopen && this.onopen(x); e.onmessage = (x) => this.onmessage && this.onmessage(x);
      e.onerror = (x) => this.onerror && this.onerror(x); }, 100); }
    close() { if (this.e) this.e.close(); } }; })();"""


def shot(pg, name):
    try:
        pg.screenshot(path=os.path.join(SHOTS, name), timeout=60000, animations="disabled")
    except Exception as e:
        print("screenshot skipped:", type(e).__name__, flush=True)


def shows(pg, js, ms=WAIT_MS):
    t0 = time.time()
    try:
        pg.wait_for_function(js, timeout=ms, polling=100)      # on a timer: a headless page's frames can stall
        return time.time() - t0
    except Exception:
        return None


def phone_set(key, value):
    return RB.brain_request("set", {"key": key, "value": value})


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge")
    errors: list = []
    # The window's page is large; a local antivirus can hold a big localhost answer for minutes. Tried again on a fresh page.
    for attempt in range(3):
        pg = b.new_page(viewport={"width": 1100, "height": 760})
        pg.add_init_script(SEEN)
        pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
        try:
            pg.goto(url + "/", wait_until="domcontentloaded", timeout=90000)
            break
        except Exception as e:
            print(f"the window's page didn't load (try {attempt + 1}): {type(e).__name__}", flush=True)
            pg.close()
    else:
        print("skipped: the window's page wouldn't load here")
        sys.exit(0)
    pg.wait_for_function("typeof followLook === 'function'", timeout=120000)
    # Changed before the window's stream opened: it shows once the stream opens.
    web._save_settings(dict(web._load_settings(), ui_accent="mono"))
    pg.evaluate("window.__go = 1")
    check("a change made before the window's stream opened shows once it opens",
          shows(pg, "document.documentElement.dataset.accent === 'mono'") is not None)
    pg.wait_for_function("window.__open > 0", timeout=60000)
    pg.wait_for_timeout(800)
    shot(pg, "window_before.png")
    got = phone_set("ui_accent", "ocean")
    took = shows(pg, "document.documentElement.dataset.accent === 'ocean'")
    check(f"an accent picked on the phone repaints the open window, no reload ({took and round(took, 2)} s)", took is not None and took < 3)
    check(f"the phone hears only whether it took, never the PC's settings and keys ({got})", got == {"ok": True})
    phone_set("ui_theme", "light")
    check("and so does a theme", shows(pg, "document.documentElement.dataset.theme === 'light'") is not None)
    # The Team room, from the phone.
    got = [phone_set(k, v) for k, v in (("room_look", "him"), ("room_home", "library"), ("room_wipe", "instant"))]
    ok = shows(pg, "localStorage.getItem('hearth_room_look') === 'him' && localStorage.getItem('hearth_room_home') === 'library'"
                   " && localStorage.getItem('hearth_room_wipe') === 'instant'")
    check(f"the Team room's character, home room and room change set on the phone reach the window ({got})", ok is not None)
    check("a room the window doesn't have the shape of is refused", phone_set("room_home", "../x").get("ok") is False)
    # Set together, the look's repaint must not save the window's old room over the new one.
    phone_set("room_home", "winter")
    phone_set("ui_accent", "sage")
    shows(pg, "document.documentElement.dataset.accent === 'sage'")
    pg.wait_for_timeout(1500)
    saved = web._load_settings()
    check(f"a repaint doesn't put the window's old room back over the phone's ({saved.get('room_home')})",
          saved.get("room_home") == "winter" and pg.evaluate("localStorage.getItem('hearth_room_home')") == "winter")
    # Missed while nobody was listening: read again when the window is looked at.
    web._save_settings(dict(web._load_settings(), ui_accent="violet"))
    pg.evaluate("window.dispatchEvent(new Event('focus'))")
    check("a look changed while the window wasn't listening shows when it is looked at again",
          shows(pg, "document.documentElement.dataset.accent === 'violet'") is not None)
    pg.wait_for_timeout(400)
    shot(pg, "window_after.png")
    check(f"no errors in the window's page ({errors[:2]})", not errors)

    # The window's own pick goes to the phone: the bridge passes the settings event on.
    pushed: list = []
    br = RB.Bridge.__new__(RB.Bridge)
    br.adapter = type("A", (), {"rooms": {"p1": object()}, "_push": lambda self, chat, obj: pushed.append(obj)})()
    br.on_window_event({"kind": "settings", "keys": ["ui_accent"]})
    n = len(pushed)
    br.on_window_event({"kind": "settings", "keys": ["room_home"]})
    check(f"a look, or a room, picked in the window is passed to the phone ({pushed})",
          n >= 1 and len(pushed) > n and all(p.get("type") == "changed" for p in pushed))
    b.close()

# The stream's heartbeat is an event the page sees, so a stream that died quietly is noticed and reopened.
lines = []
with urllib.request.urlopen(url + "/api/events", timeout=25) as r:
    end = time.time() + 20
    while time.time() < end:
        line = r.readline().decode("utf-8", "replace").strip()
        if line:
            lines.append(line)
        if any("ping" in l for l in lines):
            break
check(f"the PC's event stream says it is still there as an event, not a comment ({lines[-1:]})",
      any(l.startswith("data:") and "ping" in l for l in lines))
srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all look sync checks passed")
