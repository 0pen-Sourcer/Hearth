"""Settings look the same in Simple and Full: every page is there, and expert
rows sit in a closed Advanced section at the foot of their page. The mode
only changes how much a chat shows. MCP server rows carry their service's
icon, and nothing visible names JARVIS or a ~/Jarvis path.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_settings_mode.py
"""
from __future__ import annotations

import base64
import os
import re
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("no settings page or row is hidden by the mode", "data-adv" not in html)
check("the wake phrases aren't written with a fixed name",
      not re.search(r'id="setting-wake-phrases"[^>]*placeholder="[^"]*jarvis', html, re.I))
check("the key hint doesn't name a settings path", "~/Jarvis/settings.json" not in html)
check("no visible ~/Jarvis path", not re.search(r"['\">]~/Jarvis", html))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(1 if FAILS else 0)

work = tempfile.mkdtemp(prefix="hearth-setmode-")
os.environ["JARVIS_WORKSPACE"] = os.environ["HEARTH_WORKSPACE"] = work
os.makedirs(os.path.join(work, ".hearth"))
from hearth import web  # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True, agent_name="Friday", ui_mode="simple"))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
BRIDGES = {"bridges": [
    {"name": "github", "raw_name": "github", "state": "connected", "site": "github.com", "kind": "",
     "tools": [{"name": "a"}, {"name": "b"}]},
    {"name": "files", "raw_name": "files", "state": "connected", "site": "", "kind": "folder", "tools": [{"name": "r"}]},
    {"name": "thing", "raw_name": "thing", "state": "error", "site": "", "kind": "", "tools": [],
     "error": "Couldn't start", "detail": "x"},
]}

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skipped: no browser to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(1 if FAILS else 0)
    os.environ["HOME"] = os.environ["USERPROFILE"] = work
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.route("**/api/mcp/status", lambda r: r.fulfill(json=BRIDGES))
    pg.route("**/api/favicon*", lambda r: r.fulfill(body=PNG, content_type="image/png"))
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    time.sleep(3)
    pg.evaluate("hearthSetInterface('simple')")
    pg.evaluate("showView('settings')")
    pg.wait_for_timeout(600)

    for pane in ("server", "mcp"):
        check(f"Simple shows the {pane} page in the side list",
              pg.is_visible(f".settings-side-btn[data-st={pane}]"))

    pg.click(".settings-side-btn[data-st=behavior]")
    pg.wait_for_timeout(500)
    adv = pg.evaluate("""() => {
      const pane = document.querySelector('.settings-pane[data-st=behavior]');
      const d = pane.querySelector(':scope > details.set-adv');
      const sel = document.getElementById('setting-think-level');
      return { has: !!d, last: d && d === [...pane.children].filter(e => e.tagName !== 'SCRIPT').pop(),
               open: d && d.open, inside: !!(d && d.contains(sel)), perms: !!(d && d.contains(document.getElementById('perm-list'))),
               count: pane.querySelectorAll('details.set-adv').length };
    }""")
    check(f"Behavior keeps its expert rows in one closed Advanced section at its foot {adv}",
          adv["has"] and adv["last"] and adv["open"] is False and adv["inside"] and adv["perms"] and adv["count"] == 1)
    check("a row in a closed Advanced section is out of sight", not pg.is_visible("#setting-think-level"))
    pg.click(".settings-pane[data-st=behavior] details.set-adv > summary")
    pg.wait_for_timeout(300)
    check("opening Advanced shows it", pg.is_visible("#setting-think-level"))
    check("rows in Advanced sit in cards like the rest",
          pg.evaluate("document.getElementById('setting-think-level').closest('.set-card') !== null"))

    pg.click(".settings-side-btn[data-st=server]")
    pg.wait_for_timeout(500)
    check("Built-in server opens in Simple and its port sits in a closed Advanced section",
          pg.is_visible("#setting-server-ctx") and not pg.is_visible("#setting-server-port")
          and pg.evaluate("!!document.getElementById('setting-server-port').closest('details.set-adv:not([open])')"))

    pg.click(".settings-side-btn[data-st=mcp]")
    pg.wait_for_timeout(1200)
    marks = pg.evaluate("""() => [...document.querySelectorAll('#mcp-status-box .model-row')].map(r => {
      const m = r.querySelector('.mcp-mark');
      const img = m && m.querySelector('img');
      return { img: img ? img.getAttribute('src') : '', svg: !!(m && m.querySelector('svg')), dot: !!(m && m.querySelector('.mcp-dot')) };
    })""")
    check(f"each MCP row shows a mark with its status dot {marks}", len(marks) == 3 and all(m["dot"] for m in marks))
    check("a service's row shows its site's icon", marks and "/api/favicon?d=github.com" in marks[0]["img"])
    check("a file server shows a folder, an unknown one the line icon",
          len(marks) == 3 and marks[1]["svg"] and not marks[1]["img"] and marks[2]["svg"] and not marks[2]["img"])

    pg.click(".settings-side-btn[data-st=voice]")
    pg.wait_for_timeout(400)
    ph = pg.evaluate("document.getElementById('setting-wake-phrases').placeholder")
    check(f"the wake placeholder follows the agent's name ({ph!r})", "friday" in ph and "jarvis" not in ph.lower())
    pg.click(".settings-side-btn[data-st=appearance]")
    pg.wait_for_timeout(300)
    ap = pg.evaluate("document.querySelector('.settings-pane[data-st=appearance]').innerText")
    check("the mode's control says what it governs", "Detail in chats" in ap and "Full" in ap)

    # The mode still decides how a chat shows tool steps.
    def tools_shown(mode):
        return pg.evaluate("""(mode) => {
          hearthSetInterface(mode);
          const msg = document.createElement('div');
          msg.innerHTML = '<div class="body"><div class="actions"></div></div>';
          document.body.appendChild(msg);
          appendPart(makeToolCard('read_file', { path: 'a.txt' }).div, msg);
          appendPart(makeToolCard('list_dir', { path: '.' }).div, msg);
          const out = { runs: msg.querySelectorAll('.tool-run').length,
                        loose: msg.querySelectorAll('.body > .tool').length };
          msg.remove();
          return out;
        }""", mode)
    simple, full = tools_shown("simple"), tools_shown("advanced")
    check(f"Simple folds tool steps into one line {simple}", simple["runs"] == 1 and simple["loose"] == 0)
    check(f"Full shows each tool as its own card {full}", full["runs"] == 0 and full["loose"] == 2)
    pg.evaluate("hearthSetInterface('simple')")
    check(f"no page errors {errors[:3]}", not errors)
    browser.close()
srv.shutdown()

print()
print("FAIL" if FAILS else "PASS", f"({len(FAILS)} failed)" if FAILS else "")
sys.exit(1 if FAILS else 0)
