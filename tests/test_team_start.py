"""Starting a helper from the Team view, and agents that can't be held back.

The Team view could only watch helpers JARVIS had started; there was no way
to start one there. /api/team/start takes a task and either one of Hearth's
own helper roles or an installed agent CLI. Separately, Kimi Code's headless
mode approves every tool call by design, so it runs only when the approval
switch is on Auto.

    .\\.venv\\Scripts\\python.exe tests\\test_team_start.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import web, subagents, external_agents as xa, tools      # noqa: E402

calls = []
real_sub, real_xa = subagents.spawn_subagent, xa.spawn
subagents.spawn_subagent = lambda persona, prompt, **k: (calls.append(("helper", persona, prompt, k)),
                                                         {"ok": True, "agent_id": "sub_1_a"})[1]
xa.spawn = lambda agent, prompt, **k: (calls.append(("agent", agent, prompt, k)),
                                       {"ok": True, "agent_id": "sub_2_b", "label": "Stand-in"})[1]

s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def post(body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/team/start", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, r = post({"persona": "researcher", "task": "compare three TTS engines"})
check("a Hearth helper starts in the background",
      code == 200 and r.get("ok") and calls and calls[-1][:3] == ("helper", "researcher", "compare three TTS engines")
      and calls[-1][3].get("mode") == "background")
folder = tempfile.mkdtemp()
code, r = post({"agent": "codex", "task": "add tests", "folder": folder})
check("an agent CLI starts in the folder given",
      code == 200 and calls[-1][:3] == ("agent", "codex", "add tests") and calls[-1][3].get("cwd") == folder)
code, r = post({"persona": "researcher"})
check("no task is refused", code == 400)
code, r = post({"task": "x"})
check("neither a role nor an agent is refused", code == 400)
code, r = post({"agent": "codex", "task": "x", "folder": os.path.join(folder, "nope")})
check("a folder that doesn't exist is refused", code == 400)

r = web._team_choices() if hasattr(web, "_team_choices") else {}
check("the choices list Hearth's roles and the installed agents",
      isinstance(r, dict) and r.get("roles") and isinstance(r.get("agents"), list))
srv.shutdown()

# Kimi Code approves everything in headless mode, so only Auto lets it run.
subagents.spawn_subagent, xa.spawn = real_sub, real_xa
real_which, real_mode = xa._which, tools.approval_mode
xa._which = lambda names: sys.executable if names == ["kimi"] else real_which(names)
tools.approval_mode = lambda: "ask"
r = xa.spawn("kimi", "hello", cwd=tempfile.mkdtemp())
check("Kimi Code is refused when Hearth asks before acting", not r.get("ok") and "Auto" in (r.get("error") or ""))
check("its arguments no longer use a flag its docs don't have", "--print" not in xa.AGENTS["kimi"]["args"]("auto"))
xa._which, tools.approval_mode = real_which, real_mode

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all team start checks passed")
