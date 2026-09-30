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

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all external agent checks passed")
