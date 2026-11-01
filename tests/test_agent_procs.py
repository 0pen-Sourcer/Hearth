"""An agent brain's turn that is stopped partway (Stop, Ctrl+C, a timeout) ends its agent's process and
closes its pipes inside the loop: no agent left running, and no "unclosed transport" at exit.

    .\\.venv\\Scripts\\python.exe tests\\test_agent_procs.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


try:
    import psutil
except ImportError:
    print("skipped: pip install psutil")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="hearth-procs-")
pidfile = os.path.join(work, "agent.pid")
fake = os.path.join(work, "fake_agent.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write(textwrap.dedent(f"""
        import json, os, sys, time
        open({pidfile!r}, "w").write(str(os.getpid()))
        print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s1"}}), flush=True)
        while True:
            time.sleep(0.2)
    """))

SCENARIO = textwrap.dedent(f"""
    import asyncio, os, sys
    sys.path.insert(0, {ROOT!r})
    os.environ["HOME"] = os.environ["USERPROFILE"] = {work!r}
    os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join({work!r}, "ws")
    os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
    from hearth import {{mod}} as B
    obj, path = B, "{{exe_fn}}".split(".")
    for part in path[:-1]:
        obj = getattr(obj, part)
    setattr(obj, path[-1], lambda *a, **k: [sys.executable, {fake!r}])

    async def main():
        turn = asyncio.ensure_future(B.{{fn}}("hi", emit=lambda *a, **k: None))
        for _ in range(100):
            await asyncio.sleep(0.1)
            if os.path.exists({pidfile!r}):
                break
        await asyncio.sleep(0.5)
        turn.cancel()
        try:
            await turn
        except asyncio.CancelledError:
            pass
    asyncio.run(main())
    print("done", flush=True)
""")

for mod, exe_fn, fn in (("claude_brain", "claude_exe", "_run_claude"), ("codex_brain", "_base.exe_for", "_run_exec"),
                        ("cursor_brain", "_base.exe_for", "run_turn")):
    if os.path.exists(pidfile):
        os.remove(pidfile)
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", SCENARIO.format(mod=mod, exe_fn=exe_fn, fn=fn)],
                       capture_output=True, text=True, timeout=120)
    pid = int(open(pidfile).read()) if os.path.exists(pidfile) else 0
    time.sleep(0.5)
    alive = bool(pid) and psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    if alive:
        psutil.Process(pid).kill()
    check(f"{mod}: the turn started its agent and was stopped partway ({r.stdout.strip()[-40:]!r})",
          bool(pid) and "done" in r.stdout)
    check(f"{mod}: the agent's process is gone once the turn has stopped", not alive)
    noisy = [l for l in r.stderr.splitlines() if "unclosed" in l or "closed pipe" in l or "Exception ignored" in l]
    check(f"{mod}: nothing is left for the garbage collector at exit ({noisy[:2]})", not noisy)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent process checks passed")
