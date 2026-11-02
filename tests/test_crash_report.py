"""The crash notice speaks only for a process that died of the fault, and its report fits
the address GitHub's new-issue page accepts.

A fault the process lived through (Windows hands faulthandler handled ones too), then a
kill, says nothing. A start while the last run is still going says nothing. A real
native crash still does, and its report leads with the call it died in, under the
length limit, without the home folder or account name.

    python tests/test_crash_report.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.parse import unquote

import _home  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = _home.HOME
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A folder under the temp home, so the stack carries the home path the way an install does.
SRC = os.path.join(HOME, "app")
os.makedirs(SRC)
CHILD = os.path.join(SRC, "tray_like.py")
with open(CHILD, "w") as f:
    f.write(f"""import sys, time, faulthandler, threading, ctypes
sys.path.insert(0, {ROOT!r})
from hearth import crashlog
crashlog.enable("tray")
how = sys.argv[1]

def busy_worker_with_a_long_name(n):
    if n:
        return busy_worker_with_a_long_name(n - 1)
    time.sleep(120)

for i in range(60):
    threading.Thread(target=busy_worker_with_a_long_name, args=(20,), daemon=True).start()
time.sleep(0.3)
if how in ("survive", "survive_live"):
    try:
        ctypes.string_at(1)        # a fault Windows hands to ctypes, which raises it as OSError
    except OSError:
        pass
    print("survived", flush=True)
    time.sleep(60)
elif how == "crash":
    def where_it_died():           # on the newest thread, which faulthandler lists first
        faulthandler._read_null()
    t = threading.Thread(target=where_it_died)
    t.start()
    t.join()
""")


def start(how):
    return subprocess.Popen([sys.executable, CHILD, how], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def next_start_pending():
    out = subprocess.run([sys.executable, "-c", f"""import sys, json
sys.path.insert(0, {ROOT!r})
from hearth import crashlog
crashlog.enable("tray")
print(json.dumps(crashlog.pending()))"""], capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


from hearth import crashlog  # noqa: E402

LOG = os.path.join(crashlog._folder(), "crash.log")

if sys.platform == "win32":
    p = start("survive")
    p.stdout.readline()
    time.sleep(8)
    with open(LOG, encoding="utf-8", errors="replace") as f:
        logged = "access violation" in f.read()
    p.kill()
    p.wait(30)
    check("a fault it lived through is in the log", logged)
    check("but after a kill, nothing is said", next_start_pending() is None)
    crashlog.dismiss()

    p = start("survive_live")
    p.stdout.readline()
    time.sleep(0.5)
    check("a start while the last run is still going says nothing", next_start_pending() is None)
    p.kill()
    p.wait(30)
    crashlog.dismiss()

p = start("crash")
p.wait(30)
pend = next_start_pending()
check("a real crash is still found", bool(pend) and "where_it_died" in (pend or {}).get("stack", ""))

url = crashlog.report_url(pend or {})
body = unquote(url)
check(f"its report fits what GitHub takes ({len(url)} <= {crashlog._URL_MAX} < 6950)",
      len(url) <= crashlog._URL_MAX < 6950)
check("and still names the call it died in", "where_it_died" in body)
lines = body.split("```\n")[1].splitlines() if "```\n" in body else []
cur = next((i for i, x in enumerate(lines) if x.startswith(("Current thread", "Stack (most"))), -1)
check("right under the fault line", cur >= 0 and "where_it_died" in lines[cur + 1])
user = os.path.basename(os.path.expanduser("~").rstrip("\\/"))
account = os.environ.get("USERNAME") or os.environ.get("USER") or user
check("without the home folder or account name",
      HOME not in body and user not in body and account.lower() not in body.lower())

short = crashlog.scrub('  File "C:\\Users\\SWAGGY~1\\AppData\\x.py", line 1 in f')
check(f"an 8.3 short home path is cut too ({short!r})", "SWAGGY~1" not in short)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all crash report checks passed")
