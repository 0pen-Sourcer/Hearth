"""Auto mode, deterministic first: what the app made itself it may delete or
move without asking (a folder it created outside the workspace included), what
the person made still asks, and every file change can be undone: an edit, an
overwrite, a delete, a move, a new file.

    .\\.venv\\Scripts\\python.exe tests\\test_undo_auto.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-undo-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
WS = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.makedirs(WS)

from hearth import tools as T, undo as U  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


T.approval_mode = lambda: "auto"
OUT = os.path.join(HOME, "Projects")              # a folder outside the workspace the person let it use
os.makedirs(OUT)
T.EXTRA_WORKSPACES.append(OUT)
mine = os.path.join(OUT, "thesis")               # the person's own folder
os.makedirs(mine)
with open(os.path.join(mine, "chapter1.md"), "w") as f:
    f.write("my words\n")

# What the app makes, it may remove without asking.
T.execute_tool("create_directory", {"path": os.path.join(OUT, "scratch-build")})
T.execute_tool("write_file", {"path": os.path.join(OUT, "scratch-build", "out.txt"), "content": "x"})
made = os.path.join(OUT, "scratch-build")
check("a folder it made outside the workspace may be deleted in Auto without asking (the 'C:' case)",
      T.mode_allows("delete_path", {"path": made}) and T.mode_allows("run_command", {"command": f'rm -rf "{made}"'})
      and T.mode_allows("run_command", {"command": f'cmd /c rd /s /q "{made}"'}))
check("and moved", T.mode_allows("move_path", {"source": made, "destination": os.path.join(OUT, "old-build")}))
check("the person's own folder still asks", not T.mode_allows("delete_path", {"path": mine})
      and not T.mode_allows("run_command", {"command": f'rm -rf "{mine}"'}))
check("a delete chained to another command still asks",
      not T.mode_allows("run_command", {"command": f'rm -rf "{made}"; rm -rf "{mine}"'})
      and not T.mode_allows("run_command", {"command": f'rm -rf "{made}" "{mine}"'}))
check("a wildcard still asks", not T.mode_allows("run_command", {"command": f"rm -rf {OUT}/*"}))
check("the workspace itself is never 'made by the app'", not U.made_by_app(WS))

# Undo: each kind of change goes back.
note = os.path.join(WS, "notes.txt")
with open(note, "w") as f:
    f.write("first\n")
T.execute_tool("read_file", {"path": note})
T.execute_tool("edit_file", {"path": note, "edits": [{"old_text": "first", "new_text": "second"}]})
T.execute_tool("write_file", {"path": os.path.join(WS, "new.txt"), "content": "made"})
T.execute_tool("delete_path", {"path": os.path.join(mine, "chapter1.md")})
T.execute_tool("move_path", {"source": made, "destination": os.path.join(OUT, "old-build")})
rows = U.recent(10)
check(f"recent changes are listed in words ({[r['what'] for r in rows][:4]})",
      [r["what"] for r in rows][:4] == ["Moved scratch-build to " + OUT, "Deleted chapter1.md", "Made new.txt", "Changed notes.txt"])
for r in rows[:4]:
    U.undo(r["id"])
check("undo puts back a moved folder", os.path.isdir(made) and not os.path.exists(os.path.join(OUT, "old-build")))
check("a deleted file", open(os.path.join(mine, "chapter1.md")).read() == "my words\n")
check("removes a file it made", not os.path.exists(os.path.join(WS, "new.txt")))
check("and an edit", open(note).read() == "first\n")
check("an undone change can't be undone twice", U.undo(rows[0]["id"])["ok"] is False)

# A plain delete through the shell, of what it made, is copied aside first too.
# As a turn runs it once Auto said yes (headless and the MCP server mark it approved).
r = T.execute_tool("run_command", {"command": f'rm -rf "{made}"', "_approved": T.APPROVAL_TOKEN})
check(f"rm -rf of its own folder ran ({r[:60]!r})", not os.path.exists(made))
U.undo(U.recent(1)[0]["id"])
check("and comes back with Undo", os.path.isfile(os.path.join(made, "out.txt")))
check("a failed call leaves nothing to undo", T.execute_tool("delete_path", {"path": os.path.join(WS, "nope")}).startswith("Error")
      and U.recent(1)[0]["what"] != "Deleted nope")

# The window: /undo lists the changes, and Undo puts one back.
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is not None:
    import socket
    import threading
    from hearth import web
    web._save_settings(dict(web._load_settings(), onboarded=True))
    T.execute_tool("write_file", {"path": os.path.join(WS, "plan.md"), "content": "# Plan\n"})
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as pw:
        browser = None
        for kw in ({"channel": "msedge"}, {"channel": "chromium"}, {}):
            try:
                browser = pw.chromium.launch(**kw)
                break
            except Exception:
                continue
        if browser is None:
            print("(no browser: the window's /undo wasn't drawn)")
        else:
            pg = browser.new_page(viewport={"width": 1280, "height": 860})
            pg.goto(f"http://127.0.0.1:{port}/")
            pg.wait_for_function("typeof showUndo === 'function'", timeout=30000)
            pg.evaluate("showUndo()")
            pg.wait_for_selector("#modal.open .undo-row", timeout=10000)
            rows = pg.locator("#modal-body .undo-row").all_inner_texts()
            check(f"the window's /undo lists the changes ({len(rows)}: {rows[0].splitlines()[0] if rows else ''})",
                  rows and rows[0].startswith("Made plan.md"))
            shot = os.path.join(tempfile.gettempdir(), "hearth-undo-window.png")
            pg.screenshot(path=shot)
            print("screenshot:", shot)
            pg.click("#modal-body .undo-row >> nth=0 >> [data-undo]")
            end = __import__("time").time() + 10
            while os.path.exists(os.path.join(WS, "plan.md")) and __import__("time").time() < end:
                __import__("time").sleep(0.2)
            check("and Undo there puts it back", not os.path.exists(os.path.join(WS, "plan.md")))
            browser.close()
    srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all undo and Auto checks passed")
