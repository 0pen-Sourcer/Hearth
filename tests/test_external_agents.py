"""Other agent CLIs as Team members.

An external agent runs headless in the background and writes the same
transcript Hearth's own helpers write, so the Team view shows its steps and
its result reaches the main assistant. A stand-in script plays the agent here,
printing Claude Code's stream format, so no real account is needed.

    .\\.venv\\Scripts\\python.exe tests\\test_external_agents.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import external_agents as xa, subagents, team        # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


work = tempfile.mkdtemp(prefix="hearth-xa-")
subagents._TRANSCRIPT_DIR = __import__("pathlib").Path(work) / "subagents"
fake = os.path.join(work, "fake_claude.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write("""import sys, json
prompt = sys.stdin.read()
out = lambda o: print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "model": "stand-in"})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.txt"}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": "hello"}]}]}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "The file says hello."}]}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "The file says hello. (" + prompt.strip() + ")"})
""")

xa.AGENTS["stand-in"] = {"label": "Stand-in", "exe": ["stand-in"], "args": lambda allow: [fake],
                         "stream": "claude"}
xa.AGENTS["stand-in-text"] = {"label": "Text agent", "exe": ["stand-in"], "args": lambda allow: ["-c",
                              "import sys; print('line one'); print('done: ' + sys.stdin.read().strip())"],
                              "stream": "text"}
real_which = xa._which
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)

done = threading.Event()
got = {}
r = xa.spawn("stand-in", "read a.txt", name="Probe", cwd=work,
             on_done=lambda i, res: (got.update(res), done.set()))
check("an installed agent starts", r.get("ok") and r.get("agent_id"))
done.wait(30)
check("its result comes back", got.get("ok") and got.get("text", "").startswith("The file says hello."))
check("the prompt reached it on stdin", "(read a.txt)" in got.get("text", ""))
recs = [json.loads(l) for l in open(subagents._transcript_path(r["agent_id"]), encoding="utf-8")]
kinds = [x["kind"] for x in recs]
check("the transcript has the same shape as a Hearth helper's",
      kinds[:2] == ["start", "route"] and "assistant_tool_call" in kinds and "tool_result" in kinds and kinds[-1] == "end")
check("tool calls are named", any(x.get("calls") == [{"name": "Read", "args": '{"file_path": "a.txt"}'}] for x in recs))
st = {a["id"]: a for a in subagents.team_state()}
check("the Team view shows it done, under the agent's name",
      st[r["agent_id"]]["status"] == "done" and st[r["agent_id"]]["persona"] == "Stand-in" and st[r["agent_id"]]["tools_used"] == 1)

done.clear(); got.clear()
r2 = xa.spawn("stand-in-text", "hi there", cwd=work, on_done=lambda i, res: (got.update(res), done.set()))
done.wait(30)
check("a plain-text agent's output is its result", got.get("ok") and "done: hi there" in got.get("text", ""))

check("an agent that is not installed is refused with a reason",
      "not installed" in (xa.spawn("codex", "x", cwd=work) if not real_which(["codex"]) else {"error": "not installed"}).get("error", ""))
check("an unknown agent is refused", not xa.spawn("nope", "x").get("ok"))

# launch_team routes a member with an agent to it, outside the local team limit
calls = []
xa_spawn = xa.spawn
xa.spawn = lambda *a, **k: (calls.append((a, k)), {"ok": True, "agent_id": "sub_1_x", "label": "Stand-in"})[1]
team._open_wt = team._open_consoles = team._open_tmux = lambda spawned: False
res = team.launch_team([{"name": "Ext", "agent": "stand-in", "prompt": "do it", "folder": work}])
check("launch_team hands an agent member to that agent", res.get("ok") and calls and calls[0][0][:2] == ("stand-in", "do it"))
xa.spawn = xa_spawn

# delegate: one task, the agent's answer relayed in the same turn
from hearth import tools                                            # noqa: E402
out = tools._delegate({"agent": "stand-in", "task": "read a.txt", "folder": work})
check("delegate waits and relays the agent's answer", "Stand-in answered" in out and "The file says hello." in out)
check("delegate without an agent says what is installed", tools._delegate({"task": "x"}).startswith("Error"))
check("the model is told about the agents it can hand work to",
      "delegate" in [t["name"] for t in tools.TOOL_DEFINITIONS])

# A slow agent: the turn moves on, and the answer still arrives later.
xa.AGENTS["stand-in-slow"] = {"label": "Slow agent", "exe": ["stand-in"], "args": lambda allow: ["-c",
                              "import time, sys; sys.stdin.read(); time.sleep(1.5); print('late answer')"],
                              "stream": "text"}
late = []
subagents._BG_COMPLETION_CB = lambda i, res: late.append(res)
tools._DELEGATE_WAIT_S = 0.3
out = tools._delegate({"agent": "stand-in-slow", "task": "x", "folder": work})
check("a slow agent does not hold the turn", "still working" in out)
t0 = time.time()
while not late and time.time() - t0 < 20:
    time.sleep(0.1)
check("its answer arrives afterwards like a background helper's", late and "late answer" in late[0].get("text", ""))

# npm installs CLIs as .cmd shims; the prompt must never go through cmd.exe.
shim_dir = tempfile.mkdtemp(prefix="hearth-shim-")
os.makedirs(os.path.join(shim_dir, "node_modules", "g", "dist"))
open(os.path.join(shim_dir, "node_modules", "g", "dist", "index.js"), "w").close()
shim = os.path.join(shim_dir, "g.cmd")
with open(shim, "w") as f:
    f.write('@IF EXIST "%~dp0\\node.exe" (\n  "%~dp0\\node.exe"  "%~dp0\\node_modules\\g\\dist\\index.js" %*\n)'
            ' ELSE (\n  node  "%~dp0\\node_modules\\g\\dist\\index.js" %*\n)\n')
argv = xa._direct_argv(shim)
if sys.platform == "win32":
    check("an npm shim runs its script directly, not through cmd.exe",
          argv[-1].endswith("index.js") and not argv[0].lower().endswith(".cmd"))
    xa.AGENTS["stand-in-shim"] = {"label": "Shim", "exe": ["stand-in-shim"], "args": lambda allow: ["-p"],
                                  "stream": "text", "prompt_arg": True}
    bad = os.path.join(shim_dir, "opaque.cmd")
    open(bad, "w").write("@echo off\r\nsomething %*\r\n")
    xa._which = lambda names: bad if names == ["stand-in-shim"] else (sys.executable if names == ["stand-in"] else real_which(names))
    r = xa.spawn("stand-in-shim", "hi & del x", cwd=work)
    check("a shim it can't see through is refused rather than given a prompt", not r.get("ok"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all external agent checks passed")
