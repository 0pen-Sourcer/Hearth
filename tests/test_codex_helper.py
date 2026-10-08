"""A Codex helper can be told something while it works.

Before, a Codex helper ran as `codex exec`, which takes its task and runs to
the end: a message from the person or another helper had no way in."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-codexhelper-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
os.environ.pop("HEARTH_CODEX_EXEC", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import external_agents as xa, subagents      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A stand-in for `codex app-server`: one command, then it waits to be steered
# and says what it heard.
fake = os.path.join(work, "fake_codex.py")
Path(fake).write_text(r'''
import json, sys, time
out = lambda o: (sys.stdout.write(json.dumps(o) + "\n"), sys.stdout.flush())
heard = ""
for raw in sys.stdin:
    m = json.loads(raw)
    meth, p, mid = m.get("method"), m.get("params") or {}, m.get("id")
    if meth == "initialize":
        out({"id": mid, "result": {}})
    elif meth == "thread/start":
        assert p["approvalPolicy"] == "never"
        out({"id": mid, "result": {"thread": {"id": "th1"}}})
    elif meth == "turn/start":
        out({"id": mid, "result": {"turn": {"id": "tu1"}}})
        item = {"type": "commandExecution", "id": "c1", "command": "npm test", "commandActions": [{"command": "npm test"}]}
        out({"method": "item/started", "params": {"item": item}})
        out({"method": "item/completed", "params": {"item": {**item, "aggregatedOutput": "4 passed", "exitCode": 0}}})
        # The turn stays open until word comes from outside.
    elif meth == "turn/steer":
        heard = p["input"][0]["text"]
        out({"id": mid, "result": {}})
        out({"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "a1", "text": "Tests pass. Heard: " + heard}}})
        out({"method": "turn/completed", "params": {"turn": {"id": "tu1", "status": "completed"}}})
''', encoding="utf-8")

real_which, real_argv = xa._which, xa._direct_argv
xa._which = lambda names: sys.executable if names == ["codex"] else real_which(names)
xa._direct_argv = lambda exe: [sys.executable, fake] if exe == sys.executable else real_argv(exe)
done, got = threading.Event(), {}
r = xa.spawn("codex", "run the tests", name="Cody", cwd=work, on_done=lambda i, res: (got.update(res), done.set()))
aid = r.get("agent_id")
for _ in range(50):
    if subagents.can_tell(aid):
        break
    time.sleep(0.1)
check("a working Codex helper can be told something", subagents.can_tell(aid))
told = subagents.tell_subagent(aid, "also check the linter", sender="Noah")
done.wait(30)
xa._which, xa._direct_argv = real_which, real_argv
check(f"the message reaches it part-way and it answers with it {told} {got.get('text', '')!r}",
      told.get("ok") and got.get("ok") and "also check the linter" in got.get("text", ""))
story = subagents.agent_story(aid)
check(f"its command shows as a step with what came back {story['steps'][:1]}",
      story["steps"] and story["steps"][0]["tool"] == "run_command" and "4 passed" in (story["steps"][0]["result"] or ""))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all Codex helper checks passed")
