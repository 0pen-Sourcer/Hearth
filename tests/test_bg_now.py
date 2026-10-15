"""A slow command can be sent to the background on request.

A command a reply was waiting on could only become a background job once its
whole wait (two minutes by default) ran out; asking to move it did nothing.

    .\\.venv\\Scripts\\python.exe tests\\test_bg_now.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-bgnow-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
sys.path.insert(0, ROOT)

from hearth import jobs  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


got = {}
t0 = time.time()
cmd = f'"{sys.executable}" -c "import time; time.sleep(20)"'
runner = threading.Thread(target=lambda: got.update(jobs.run_or_background(cmd, WS, "cmd", 60)), daemon=True)
runner.start()
time.sleep(1.5)
moved = jobs.background_now()
runner.join(10)
took = time.time() - t0
check(f"asking moves the waited-on command to the background at once ({took:.1f}s, {moved})",
      moved and got.get("background") and got.get("asked") and took < 8)
job = jobs.get_job(got.get("job_id", "")) if got.get("job_id") else {}
check(f"and it keeps running as a job {job.get('status')}", job.get("status") == "running")
check("nothing is left waiting", jobs.background_now() == [])
try:
    jobs.kill_job(got["job_id"])
except Exception:
    pass

# Without asking, a quick command still comes back with its output.
r = jobs.run_or_background(f'"{sys.executable}" -c "print(42)"', WS, "cmd", 30)
check("a quick command still answers in place", not r.get("background") and "42" in (r.get("output") or ""))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all background-now checks passed")
