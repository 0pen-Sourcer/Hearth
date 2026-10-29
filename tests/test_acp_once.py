"""A one-off request to an ACP agent (the memory pass, the auto reviewer):
an error the agent only writes to its own log ends the wait as it does in a
turn, instead of holding the caller for its whole timeout, and the model
picked for the agent is used however the agent offers its models. With
OpenCode installed, a real one-off is asked too.

    .\\.venv\\Scripts\\python.exe tests\\test_acp_once.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-acp-once-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import claude_brain as cb, external_agents as xa  # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# A stand-in agent. "refuse": logs a refusal the way OpenCode does and never
# answers. "answer": offers its models as a list (set with session/set_model)
# and answers. Every request it gets is written to the record file.
agent = os.path.join(work, "agent.py")
Path(agent).write_text(r'''
import json, sys, time
mode, logpath, record = sys.argv[1], sys.argv[2], sys.argv[3]
out = lambda o: (sys.stdout.write(json.dumps({"jsonrpc": "2.0", **o}) + "\n"), sys.stdout.flush())
for raw in sys.stdin:
    m = json.loads(raw)
    with open(record, "a") as f:
        f.write(raw.strip() + "\n")
    meth, p = m.get("method"), m.get("params") or {}
    if meth == "initialize":
        out({"id": m["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif meth == "session/new":
        out({"id": m["id"], "result": {"sessionId": "ses_once1", "models": {"currentModelId": "fast",
             "availableModels": [{"modelId": "fast", "name": "Fast"}, {"modelId": "smart", "name": "Smart"}]}}})
    elif meth == "session/set_model":
        out({"id": m["id"], "result": {}})
    elif meth == "session/prompt":
        if mode == "refuse":
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
            with open(logpath, "a") as f:
                f.write(f'timestamp={stamp} level=ERROR run=x message="stream error" session.id=ses_once1 '
                        f'error.error="AI_APICallError: Free usage limit reached. Subscribe to keep going"\n')
            continue
        out({"method": "session/update", "params": {"sessionId": "ses_once1", "update": {
             "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello there"}}}})
        out({"id": m["id"], "result": {"stopReason": "end_turn"}})
''', encoding="utf-8")
agent_log = os.path.join(work, "agent.log")
record = os.path.join(work, "record.jsonl")

xa.AGENTS["stand-in"] = {"label": "Stand-in", "exe": ["stand-in"], "args": lambda allow: [], "stream": "text"}
real_which = xa._which
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)
real_current, real_chosen = cb.current, cb.chosen_model
cb.BRAINS["stand-in-acp"] = "Stand-in"
cb.SITES["stand-in-acp"] = "https://example.org"
cb.current = lambda: "stand-in-acp"
cb.chosen_model = lambda p: "smart" if p == "stand-in-acp" else real_chosen(p)
cb.QUIET_S = 3
base = {"label": "Stand-in", "plan": "Stand-in", "site": "https://example.org", "agent": "stand-in", "log": agent_log}


def ask(mode, timeout):
    cb.ACP["stand-in-acp"] = dict(base, args=[agent, mode, agent_log, record])
    cb._EXE_SEEN.clear()
    if os.path.exists(record):
        os.remove(record)
    t0 = time.time()
    got = cb.quick("Reply with exactly: hello there", timeout=timeout, any_agent=True)
    return got, time.time() - t0


got, took = ask("refuse", 60)
check(f"an agent that logs a refusal and goes silent lets the one-off go well before its timeout ({took:.0f} s)",
      got == "" and took < 30)
try:
    from hearth import acp_brain
    reply, why = asyncio.run(acp_brain.once("stand-in-acp", "hi"))
except Exception as e:
    reply, why = "", f"({type(e).__name__}: {e})"
check(f"and says why, in its own words: {why[:80]!r}", reply == "" and why.startswith("Free usage limit reached"))

got, took = ask("answer", 60)
sent = [json.loads(line) for line in Path(record).read_text().splitlines()] if os.path.exists(record) else []
switch = [m for m in sent if m.get("method") == "session/set_model"]
check(f"an agent that answers is heard ({took:.1f} s): {got!r}", got == "hello there" and took < 15)
check(f"on the model picked for it, set the way it offers models: {[m.get('params') for m in switch]}",
      len(switch) == 1 and switch[0]["params"].get("modelId") == "smart")

# The real thing, where OpenCode is installed.
cb.current, cb.chosen_model = real_current, real_chosen
cb._EXE_SEEN.clear()
if not cb.exe_for("opencode"):
    print("skip  OpenCode isn't installed")
else:
    from hearth import acp_brain
    t0 = time.time()
    try:
        reply, why = asyncio.run(asyncio.wait_for(acp_brain.once("opencode", "Reply with exactly: hello there"), 60))
    except asyncio.TimeoutError:
        reply, why = "", "timed out"
    took = time.time() - t0
    if not reply and cb._OUT_OF_USAGE.search(why):
        check(f"OpenCode out of free usage is said within a minute ({took:.0f} s): {why[:90]}", took < 60)
        print("skip  a real answer from OpenCode: it is out of free usage right now")
    else:
        check(f"OpenCode answers a one-off within a minute ({took:.0f} s): {reply[:60]!r} {why[:80]}",
              "hello there" in reply.lower() and took < 60)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all one-off agent checks passed")
