"""After a crash, the next start says so once: "Inglen closed unexpectedly",
with Send report (a filled-in GitHub issue, no personal paths) and Dismiss.

The crash here is real: a child process records like the tray does, then dies
in native code (faulthandler's own segfault). A clean exit and a kill say nothing.
Chromium, a temp home.

    python tests/test_crash_notice.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-crash-home-")
WS = os.path.join(HOME, "Inglen")
os.makedirs(WS)
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "tour_done": True}, f)
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


CHILD = os.path.join(HOME, "tray_like.py")
with open(CHILD, "w") as f:
    f.write(f"""import sys, time, faulthandler
sys.path.insert(0, {ROOT!r})
from hearth import crashlog
crashlog.enable("tray")
how = sys.argv[1]
if how == "crash":
    def where_it_died():
        faulthandler._sigsegv()
    where_it_died()
elif how == "wait":
    time.sleep(60)
""")


def run(how):
    p = subprocess.Popen([sys.executable, CHILD, how], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if how == "wait":
        time.sleep(1.5)
        p.send_signal(signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    p.wait(30)


def next_start_pending():
    out = subprocess.run([sys.executable, "-c", f"""import sys, json
sys.path.insert(0, {ROOT!r})
from hearth import crashlog
crashlog.enable("tray")
print(json.dumps(crashlog.pending()))"""], capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


from hearth import crashlog  # noqa: E402

run("clean")
check("a clean exit leaves nothing to say", next_start_pending() is None)
run("wait")
check("a killed run leaves nothing to say", next_start_pending() is None)
run("crash")
p = next_start_pending()
check("a crash is found at the next start", bool(p) and "Fatal Python error" in p["stack"])
check("with the call it died in", bool(p) and "where_it_died" in p["stack"])
check("and no personal paths in it", bool(p) and HOME not in p["stack"]
      and os.path.basename(HOME) not in p["stack"])

from hearth import web  # noqa: E402

srv = web.serve("127.0.0.1", 0)
port = srv.server_address[1]
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    # Windows has Edge; elsewhere Playwright's own Chromium.
    b = (pw.chromium.launch(channel="msedge", args=["--disable-gpu"]) if sys.platform == "win32"
         else pw.chromium.launch())
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    ctx.route("https://github.com/**", lambda r: r.fulfill(status=200, body="issue form"))
    pg = ctx.new_page()
    pg.goto(f"http://127.0.0.1:{port}/")
    pg.wait_for_selector(".toast.ask", timeout=20000)
    time.sleep(0.4)
    pg.screenshot(path=os.path.join(HOME, "crash_toast.png"))
    t = pg.locator(".toast.ask").first
    check(f"the window says it once, quietly ({t.inner_text()!r})",
          t.inner_text().split("\n")[0] == "Inglen closed unexpectedly"
          and pg.locator(".toast.ask").count() == 1 and t.locator(".body").count() == 0)
    check("with Send report and Dismiss",
          [x.strip() for x in t.locator("button").all_inner_texts()] == ["Send report", "Dismiss"])
    with ctx.expect_page() as opened:
        t.locator("button[data-a=yes]").click()
    try:   # a noopener tab starts blank, then goes to the address
        opened.value.wait_for_url(lambda u: u.startswith(crashlog.ISSUES_URL), timeout=15000)
    except Exception:
        pass
    url = opened.value.url
    body = unquote(url)
    check("Send report opens a new GitHub issue, filled in",
          url.startswith(crashlog.ISSUES_URL) and "Inglen closed unexpectedly" in body
          and "Fatal Python error" in body and "where_it_died" in body)
    check("without their home folder or account name", HOME not in body and os.path.basename(HOME) not in body)
    check("nothing is pending after it", crashlog.pending() is None)
    pg.reload()
    time.sleep(4)
    check("and the next open says nothing", pg.locator(".toast.ask").count() == 0)

    run("crash")
    next_start_pending()
    pg.reload()
    pg.wait_for_selector(".toast.ask", timeout=20000)
    pg.locator(".toast.ask button[data-a=later]").click()
    ok = False
    for _ in range(30):
        if crashlog.pending() is None:
            ok = True
            break
        time.sleep(0.1)
    check("Dismiss forgets it, and sends nothing", ok and len(ctx.pages) == 2)
    b.close()

srv.shutdown()
long = crashlog.report_url({"stack": "\n".join(f"  File \"x.py\", line {i} in f{i}" for i in range(2000)),
                            "version": "0"})
check(f"a long stack is cut from the top to fit an address ({len(long)})",
      len(long) <= crashlog._URL_MAX and "f1999" in unquote(long))
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all crash notice checks passed")
