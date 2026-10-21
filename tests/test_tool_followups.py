"""Tools that only make sense after another one come with it.

Clicking and typing on a page were sent with every turn though only useful once
a page is open, and a job's controls were held back while the tools that start
a job told the model to use them. Now each set comes in when its parent runs.

    .\\.venv\\Scripts\\python.exe tests\\test_tool_followups.py
"""
from __future__ import annotations

import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-followups-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def sent():
    return {t["function"]["name"] for t in tools.to_openai_tools()}


tools.reset_unlocked_tools()
first = sent()
check("a new chat starts without the page and job follow-ups",
      not ({"browse_click", "browse_type", "get_job_result", "job_status", "job_kill"} & first)
      and {"browse", "start_job", "run_command"} <= first)

tools._browse = lambda p: "Opened example.com"
tools.execute_tool("browse", {"url": "https://example.com"})
check("opening a page brings clicking and typing on it", {"browse_click", "browse_type"} <= sent())

tools.reset_unlocked_tools()
import hearth.jobs as jobs  # noqa: E402
jobs.start_job = lambda *a, **k: {"job_id": "j1", "status": "running"}
tools.execute_tool("start_job", {"command": "echo hi"})
check("starting a job brings what checks on it and stops it",
      {"job_status", "job_wait", "job_kill", "get_job_result"} <= sent())

tools.reset_unlocked_tools()
jobs.run_or_background = lambda cmd, cwd, shell, timeout: {"ok": True, "background": True, "job_id": "j2", "output_tail": ""}
out = tools.execute_tool("run_command", {"command": "pip install something-slow"})
check(f"a command that carries on as a job brings the job tools too {out[:80]!r}",
      "j2" in out and {"job_status", "job_kill"} <= sent())

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all tool follow-up checks passed")
