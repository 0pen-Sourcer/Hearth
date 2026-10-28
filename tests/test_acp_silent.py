"""An agent brain that goes quiet: the person is told, and the turn keeps
waiting (a model may think for minutes without streaming it); it ends early
only on an error the agent logged and never sent back (OpenCode out of free
usage retries in silence), with the agent's own words. This lives in the one
dispatcher every agent brain passes through.

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

from hearth import claude_brain as cb, external_agents as xa, web  # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# A stand-in agent. With a log path it logs a refusal the way OpenCode does and
# never answers; without one it thinks silently for a while, then answers.
agent = os.path.join(work, "agent.py")
Path(agent).write_text(r'''
import json, sys, time
logpath = sys.argv[1] if len(sys.argv) > 1 else ""
quiet_for = float(sys.argv[2]) if len(sys.argv) > 2 else 0
out = lambda o: (sys.stdout.write(json.dumps({"jsonrpc": "2.0", **o}) + "\n"), sys.stdout.flush())
for raw in sys.stdin:
    m = json.loads(raw)
    meth, p = m.get("method"), m.get("params") or {}
    if meth == "initialize":
        out({"id": m["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif meth == "session/new":
        out({"id": m["id"], "result": {"sessionId": "ses_silent1"}})
    elif meth == "session/prompt":
        if logpath != "-":
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
            with open(logpath, "a") as f:
                f.write(f'timestamp={stamp} level=ERROR run=x message="stream error" session.id=ses_silent1 '
                        f'error.error="AI_RetryError: Failed after 3 attempts. Last error: Free usage limit reached. '
                        f'Subscribe to keep going: https://example.org/go"\n')
            continue
        time.sleep(quiet_for)
        out({"method": "session/update", "params": {"sessionId": "ses_silent1", "update": {
             "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Thought it through."}}}})
        out({"id": m["id"], "result": {"stopReason": "end_turn"}})
''', encoding="utf-8")
agent_log = os.path.join(work, "agent.log")

xa.AGENTS["stand-in"] = {"label": "Stand-in", "exe": ["stand-in"], "args": lambda allow: [], "stream": "text"}
real_which = xa._which
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)
cb.ensure_bridge = lambda: None
cb.BRAINS["stand-in-acp"] = "Stand-in"
cb.SITES["stand-in-acp"] = "https://example.org"
web._save_settings(dict(web._load_settings(), onboarded=True))


def turn(spec, quiet_s):
    cb.ACP["stand-in-acp"] = spec
    cb._EXE_SEEN.clear()
    cb.set_brain("stand-in-acp")
    cb.QUIET_S = quiet_s
    events = []
    t0 = time.time()
    asyncio.run(cb.run_turn("hi", emit=lambda k, **f: events.append((k, f)), history=[]))
    return events, time.time() - t0


base = {"label": "Stand-in", "plan": "Stand-in", "site": "https://example.org", "agent": "stand-in"}

events, took = turn(dict(base, args=[agent, agent_log], log=agent_log), 60)
err = next((f.get("message") for k, f in events if k == "error"), "")
check(f"an agent that logs its refusal ends the turn with its own words ({took:.0f} s): {err[:90]}",
      took < 60 and "Free usage limit reached" in err and "AI_RetryError" not in err)
check("and the turn is over", any(k == "done" for k, _ in events))

# Thinking quietly for 30 s, told after 6 s of quiet: told once, never cut off, the late answer arrives.
events, took = turn(dict(base, args=[agent, "-", "30"]), 6)
told = [f for k, f in events if k == "quiet"]
reply = next((f.get("content") for k, f in events if k == "assistant"), "")
check(f"a long quiet stretch is said once, plainly ({len(told)}): {(told[0]['message'] if told else '')[:70]}",
      len(told) == 1 and "quiet" in told[0]["message"] and "Stop" in told[0]["message"])
check(f"and the turn keeps waiting for the answer ({took:.0f} s): {reply!r}",
      reply == "Thought it through." and not any(k == "error" for k, _ in events) and took >= 29)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all quiet agent checks passed")
