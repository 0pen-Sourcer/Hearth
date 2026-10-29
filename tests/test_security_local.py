"""The window's local server and page, as someone trying to get in would use them.

- A chat title (or any text the page didn't write) holding a tag is shown as
  text in a dialog, never run: showModal ran anything that looked like HTML, so
  a title like <img onerror=...> ran script on the app's own origin, where every
  route (the installer launch among them) is open.
- Another site's page in the browser can't set off a GET on the API: an <img>
  or <script> carries no Origin, so only Sec-Fetch-Site tells it apart.
- A yes to writing one file at the top of a drive or the home folder doesn't
  open the whole drive or home folder to writes.

Chromium, temp HOME and workspace.

    python tests/test_security_local.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-sec-home-")
WS = os.path.join(HOME, "Inglen")
os.makedirs(WS)
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
os.environ.pop("HEARTH_ALLOW_LAN", None)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "tour_done": True}, f)
sys.path.insert(0, ROOT)

from hearth import tools, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


srv = web.serve("127.0.0.1", 0)
port = srv.server_address[1]
BASE = f"http://127.0.0.1:{port}"


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


cross = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}
check("another site's <img> can't call the API", get("/api/settings", cross) == 403)
check("nor fetch a file through it", get("/file?path=" + urllib.request.quote(os.path.join(WS, "settings.json")), cross) == 403)
check("nor a same-site page on another port",
      get("/api/llmserver/rescan", {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "no-cors"}) == 403)
check("a link from another site still opens the window",
      get("/", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}) == 200)
check("but a link straight to an API route doesn't",
      get("/api/settings", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}) == 403)
check("the window's own page is let through", get("/api/settings", {"Sec-Fetch-Site": "same-origin"}) == 200)
check("and so is a program with no browser headers (the terminal, a bridge)", get("/api/settings") == 200)

# The chat whose title is a tag.
bad = '<img src=x onerror="window.__pwned=1">'
req = urllib.request.Request(BASE + "/api/conversations", method="POST", headers={"Content-Type": "application/json"},
                             data=json.dumps({"id": "c_evil", "title": bad, "created": int(time.time() * 1000),
                                              "updated": int(time.time() * 1000),
                                              "messages": [{"role": "user", "content": "hi"}]}).encode())
urllib.request.urlopen(req, timeout=20).read()

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", args=["--disable-gpu"]) if sys.platform == "win32" else pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(BASE + "/")
    pg.wait_for_function("typeof showModal === 'function'", timeout=60000)
    time.sleep(2)
    row = pg.locator("#convo-list .convo").filter(has_text="img src")
    if not row.count():
        row = pg.locator("text=onerror").first
    row.first.hover()
    row.first.locator(".del").click(force=True)
    pg.wait_for_selector("#modal.open", timeout=5000)
    time.sleep(0.4)
    body = pg.inner_text("#modal-body")
    check("deleting a chat titled with a tag shows the tag as text", "<img src=x" in body)
    check("and runs nothing", pg.evaluate("window.__pwned === undefined") and pg.locator("#modal-body img").count() == 0)
    pg.keyboard.press("Escape")
    pg.evaluate("showModal('x', '<img src=x onerror=\"window.__pwned2=1\">'); 0")
    time.sleep(0.3)
    check("any plain string in a dialog is text", pg.evaluate("window.__pwned2 === undefined"))
    pg.evaluate("showModal('x', trustedHTML('<b id=made>made by the page</b>')); 0")
    check("markup the page wrote itself still renders", pg.locator("#modal-body #made").count() == 1)
    b.close()
srv.shutdown()

# Writes: a yes for one file at the top of the home folder or of a drive.
asked = []
tools.set_extend_workspace_callback(lambda p: asked.append(p) or True) if hasattr(tools, "set_extend_workspace_callback") \
    else setattr(tools, "_extend_workspace_callback", lambda p: asked.append(p) or True)
before = list(tools.EXTRA_WORKSPACES)
tools._resolve_write(os.path.join(HOME, "notes.txt"))
check("a yes for a file in the home folder opens that file, not the home folder",
      os.path.realpath(HOME) not in [os.path.realpath(x) for x in tools.EXTRA_WORKSPACES]
      and os.path.join(HOME, "notes.txt") in tools.EXTRA_WORKSPACES)
root_file = os.path.join(os.path.abspath(os.sep), "inglen-root-test.txt")
tools._resolve_write(root_file)
check("a yes for a file at the top of a drive opens that file, not the drive",
      os.path.abspath(os.sep) not in tools.EXTRA_WORKSPACES and root_file in tools.EXTRA_WORKSPACES)
deeper = os.path.join(HOME, "proj", "a.txt")
os.makedirs(os.path.dirname(deeper))
tools._resolve_write(deeper)
check("a yes deeper down still opens its folder, as before", os.path.join(HOME, "proj") in tools.EXTRA_WORKSPACES)
tools.EXTRA_WORKSPACES[:] = before

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all local security checks passed")
