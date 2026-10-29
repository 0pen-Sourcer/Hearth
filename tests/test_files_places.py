"""The Files tab opens on the person's files, not the app's folder.

It opened on the workspace (conversations, logs, settings.json). Now it has
places: Recent (files Inglen wrote or changed, newest first), Desktop,
Documents, Downloads, and Inglen's own folder, and it opens on Recent when
there is any. Preview and the Open menu work for files in every place, and a
listing never leaves its place. Chromium, temp HOME and workspace.

    python tests/test_files_places.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-files-home-")
WS = os.path.join(HOME, "Inglen")
os.makedirs(WS)
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  XDG_CONFIG_HOME=os.path.join(HOME, ".config"), HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "tour_done": True}, f)
for d in ("Desktop", "Documents", "Downloads"):
    os.makedirs(os.path.join(HOME, d))
with open(os.path.join(HOME, "Documents", "taxes.txt"), "w") as f:
    f.write("my taxes")
with open(os.path.join(HOME, "Downloads", "photo.png"), "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n")
with open(os.path.join(HOME, "secret.txt"), "w") as f:
    f.write("not in any place")
sys.path.insert(0, ROOT)

from hearth import undo, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def wrote(path, text):
    tok = undo.before("write_file", {"path": path, "content": text})
    with open(path, "w") as f:
        f.write(text)
    undo.after(tok, True)
    time.sleep(0.05)


report = os.path.join(HOME, "Documents", "report.md")
wrote(os.path.join(WS, "notes.md"), "older")
wrote(report, "# Report")

srv = web.serve("127.0.0.1", 0)
port = srv.server_address[1]
BASE = f"http://127.0.0.1:{port}"


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


st, raw = get("/api/files?place=documents&path=" + urllib.parse.quote("../"))
check("a listing never leaves its place", b"escapes" in raw)
st, _ = get("/file?path=" + urllib.parse.quote(os.path.join(HOME, "secret.txt")))
check("a file outside every place isn't served", st == 403)

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", args=["--disable-gpu"]) if sys.platform == "win32" else pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(BASE + "/")
    pg.wait_for_function("typeof showView === 'function'", timeout=60000)
    time.sleep(1.5)
    pg.click('.nav-btn[data-view="files"]')
    pg.wait_for_selector("#files-places .lg-chip", timeout=10000)
    time.sleep(0.8)
    places = pg.locator("#files-places .lg-chip").all_inner_texts()
    check(f"the places are plain names {places}", places == ["Recent", "Desktop", "Documents", "Downloads", "Inglen"])
    on = pg.locator("#files-places .lg-chip.on").inner_text()
    names = pg.locator("#file-list .item .name").all_inner_texts()
    check(f"it opens on Recent, newest first {names}", on == "Recent" and names == ["report.md", "notes.md"])
    check("each says which folder it's in", "Documents" in pg.locator("#file-list .item").first.inner_text())
    pg.screenshot(path=os.path.join(HOME, "files_recent.png"))
    pg.locator("#file-list .item").first.click()
    pg.wait_for_selector("#modal.open", timeout=5000)
    check("a file from Recent previews", "# Report" in pg.inner_text("#modal-body"))
    pg.keyboard.press("Escape")
    time.sleep(0.3)

    pg.click('#files-places [data-place="documents"]')
    time.sleep(0.8)
    names = pg.locator("#file-list .item .name").all_inner_texts()
    check(f"Documents shows the person's documents {names}", sorted(names) == ["report.md", "taxes.txt"])
    check("and its name heads the path", pg.inner_text("#file-crumb").startswith("Documents"))
    pg.locator("#file-list .item", has_text="taxes.txt").click()
    pg.wait_for_selector("#modal.open", timeout=5000)
    check("a document previews", "my taxes" in pg.inner_text("#modal-body"))
    pg.keyboard.press("Escape")
    time.sleep(0.3)
    apps = pg.evaluate("api('/api/file/openers?path=' + encodeURIComponent(%s)).then(r => !r.error)"
                       % json.dumps(os.path.join(HOME, "Documents", "taxes.txt")))
    check("the Open menu works for it", apps)

    pg.click('#files-places [data-place="downloads"]')
    time.sleep(0.8)
    img = pg.evaluate("fetch('/file?path=' + encodeURIComponent(%s)).then(r => r.status)"
                      % json.dumps(os.path.join(HOME, "Downloads", "photo.png")))
    check(f"a picture in Downloads opens in the viewer ({img})", img == 200)

    pg.click('#files-places [data-place="inglen"]')
    time.sleep(0.8)
    names = pg.locator("#file-list .item .name").all_inner_texts()
    check("Inglen's own folder is still one click away", "notes.md" in names)
    b.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all files places checks passed")
