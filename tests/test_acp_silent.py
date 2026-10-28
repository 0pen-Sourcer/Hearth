"""An agent brain that goes silent doesn't leave JARVIS "thinking" forever.
OpenCode, out of free usage, writes the refusal to its own log and keeps
retrying without a word to Hearth; Hearth reads that log and ends the turn
with the agent's own words. An agent that is silent with nothing logged is
given up on after a few minutes, said plainly.

    .\\.venv\\Scripts\\python.exe tests\\test_acp_silent.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-acp-silent-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import acp_brain as A, claude_brain as cb, external_agents as xa, web  # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# A stand-in agent that takes the prompt and never answers; with a log path it
# writes the refusal there the way OpenCode does.
agent = os.path.join(work, "agent.py")
Path(agent).write_text(r'''
import json, sys, time
logpath = sys.argv[1] if len(sys.argv) > 1 else ""
out = lambda o: (sys.stdout.write(json.dumps({"jsonrpc": "2.0", **o}) + "\n"), sys.stdout.flush())
for raw in sys.stdin:
    m = json.loads(raw)
    meth, p = m.get("method"), m.get("params") or {}
    if meth == "initialize":
        out({"id": m["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif meth == "session/new":
        out({"id": m["id"], "result": {"sessionId": "ses_silent1"}})
    elif meth == "session/prompt" and logpath:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
        with open(logpath, "a") as f:
            f.write(f'timestamp={stamp} level=ERROR run=x message="stream error" providerID=opencode modelID=glm '
                    f'session.id=ses_silent1 error.error="AI_RetryError: Failed after 3 attempts. Last error: Free usage '
                    f'limit reached. Subscribe to keep going: https://example.org/go"\n')
''', encoding="utf-8")
agent_log = os.path.join(work, "agent.log")

xa.AGENTS["stand-in"] = {"label": "Stand-in", "exe": ["stand-in"], "args": lambda allow: [], "stream": "text"}
real_which = xa._which
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)
cb.ensure_bridge = lambda: None
cb.BRAINS["stand-in-acp"] = "Stand-in"
cb.SITES["stand-in-acp"] = "https://example.org"
web._save_settings(dict(web._load_settings(), onboarded=True))


def turn(spec, limit):
    cb.ACP["stand-in-acp"] = spec
    cb.set_brain("stand-in-acp")
    A.SILENT_LIMIT_S, A.LOG_CHECK_S = limit, 1.0
    events = []
    t0 = time.time()
    asyncio.run(cb.run_turn("hi", emit=lambda k, **f: events.append((k, f)), history=[]))
    return events, time.time() - t0


events, took = turn({"label": "Stand-in", "plan": "Stand-in", "site": "https://example.org", "agent": "stand-in",
                     "args": [agent, agent_log], "log": agent_log}, 60)
err = next((f.get("message") for k, f in events if k == "error"), "")
check(f"an agent that logs its refusal ends the turn with its own words ({took:.0f} s): {err[:90]}",
      took < 30 and "Free usage limit reached" in err and "AI_RetryError" not in err)
check("and the turn is over", any(k == "done" for k, _ in events))

events, took = turn({"label": "Stand-in", "plan": "Stand-in", "site": "https://example.org", "agent": "stand-in",
                     "args": [agent]}, 4)
err = next((f.get("message") for k, f in events if k == "error"), "")
check(f"one silent with nothing logged is given up on, said plainly ({took:.0f} s): {err[:80]}",
      4 <= took < 30 and "sent nothing" in err)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all silent agent checks passed")
