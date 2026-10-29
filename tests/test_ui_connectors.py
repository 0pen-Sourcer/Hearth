"""Settings > Connectors, in the real page: who asked to talk (with their code),
letting them in, who is let in, and the reminders-to-phone switch in place of
ntfy. A Discord id typed in stays exact (it doesn't fit a JavaScript number).

    .\\.venv\\Scripts\\python.exe tests\\test_ui_connectors.py
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

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="hearth-conn-")
os.environ["JARVIS_WORKSPACE"] = os.environ["HEARTH_WORKSPACE"] = work
# A home of its own (set once the browser is up; Edge needs the real one): the
# page must not show, or a Save overwrite, this PC's real bots.
os.makedirs(os.path.join(work, ".hearth"))
with open(os.path.join(work, ".hearth", "discord_bridge.json"), "w", encoding="utf-8") as f:
    f.write('{"bot_token": "tok", "allowed_user_ids": [1234567890123456789]}')   # saved as a number
from hearth import web  # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

ACCESS = {"pending": [{"channel": "telegram", "code": "K7QWX2MP", "sender": "5550001", "name": "Sam", "at": 1}],
          "approved": [{"channel": "discord", "sender": "1234567890123456789", "name": "Ishant"}]}
posts, configs = [], []


def access(route):
    if route.request.method == "POST":
        posts.append(json.loads(route.request.post_data or "{}"))
        return route.fulfill(json={"ok": True})
    route.fulfill(json=ACCESS)


def bridge_config(route):
    configs.append(json.loads(route.request.post_data or "{}"))
    route.fulfill(json={"ok": True})


with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skipped: no browser to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(0)
    os.environ["HOME"] = os.environ["USERPROFILE"] = work
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.route("**/api/connectors/access", access)
    pg.route("**/api/bridges/config", bridge_config)
    pg.route("**/api/bridges/start", lambda r: r.fulfill(json={"ok": True, "running": True}))
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    time.sleep(3)
    pg.evaluate("showView('settings')")
    pg.wait_for_timeout(800)
    pg.click(".settings-side-btn[data-st=connect]")
    pg.wait_for_timeout(1200)
    text = pg.evaluate("document.querySelector('.settings-pane[data-st=connect]').innerText")
    check("someone asking to talk is shown with their code", "Sam" in text and "K7QWX2MP" in text)
    check("and who is let in", "Ishant" in text and "Let in" in text)
    check("ntfy is gone, the reminders switch is there",
          "ntfy" not in text.lower() and "Send reminders to my phone too" in text)
    check("Discord's setup no longer asks for a privileged intent", "MESSAGE CONTENT" not in text)
    pg.screenshot(path=os.path.join(work, "connectors.png"))
    shown = pg.evaluate("document.getElementById('bridge-dc-ids').value")
    check(f"a saved Discord id is shown exactly, not rounded ({shown})", shown == "1234567890123456789")
    pg.click("[data-conn-approve]")
    pg.wait_for_timeout(600)
    check(f"Let in approves that code {posts}", posts and posts[0] == {"action": "approve", "channel": "telegram", "code": "K7QWX2MP"})
    pg.fill("#bridge-dc-token", "tok")
    pg.fill("#bridge-dc-ids", "1234567890123456789")
    pg.click("#bridge-dc-save")
    pg.wait_for_timeout(800)
    saved = (configs[-1].get("config") or {}).get("allowed_user_ids") if configs else None
    check(f"a Discord id is saved exactly ({saved})", saved == ["1234567890123456789"])
    # MCP servers: two plain rows and the live list; a failure is one sentence,
    # the raw error one click away.
    raw = "ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)\n'node' is not recognized"
    pg.route("**/api/mcp/status", lambda r: r.fulfill(json={"bridges": [
        {"name": "github", "raw_name": "github", "state": "connected", "tools": [{"name": "a"}, {"name": "b"}], "error": ""},
        {"name": "files", "raw_name": "files", "state": "error", "tools": [],
         "error": "Couldn't start: node can't be found", "detail": raw}]}))
    pg.evaluate("() => document.querySelector('.settings-side-btn[data-st=mcp]').click()")
    pg.wait_for_timeout(1200)
    mcp = pg.evaluate("document.querySelector('.settings-pane[data-st=mcp]').innerText")
    check("MCP servers reads as two plain rows",
          "Servers Inglen uses" in mcp and "Use Inglen from other apps" in mcp
          and "Edit mcp.json" in mcp and "Show config" in mcp)
    check("no protocol essay or CLIENT/SERVER headings",
          not any(w in mcp for w in ("Model Context Protocol", "as a CLIENT", "as a SERVER", "Active MCP bridges", "Outbound", "Inbound")))
    check("a connected server shows its tool count", "github" in mcp and "2 tools" in mcp)
    check("a failed one says why in one plain sentence, without the raw error",
          "Couldn't start: node can't be found" in mcp and "ExceptionGroup" not in mcp)
    pg.click(".settings-pane[data-st=mcp] [data-mcp-row]")
    pg.wait_for_timeout(300)
    mcp_open = pg.evaluate("document.querySelector('.settings-pane[data-st=mcp]').innerText")
    check("clicking it shows the raw detail", "ExceptionGroup" in mcp_open)
    pg.screenshot(path=os.path.join(work, "mcp.png"))
    check(f"no script errors {errors[:2]}", not errors)
    print("screenshot:", os.path.join(work, "connectors.png"))
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all connectors page checks passed")
