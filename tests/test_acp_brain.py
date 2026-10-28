"""An agent that speaks the Agent Client Protocol can be JARVIS's brain.

Before, only Claude Code, Codex and Cursor could; OpenCode, Copilot CLI,
Goose, Grok Build, Pi and the rest could only take tasks as helpers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-acp-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import claude_brain as cb, external_agents as xa, web      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A stand-in agent: it runs a shell command it must ask about, then a Hearth
# tool through MCP, then answers. It records what it was asked to do.
log = os.path.join(work, "agent_log.jsonl")
agent = os.path.join(work, "agent.py")
Path(agent).write_text(r'''
import json, sys
log = open(sys.argv[1], "a")
out = lambda o: (sys.stdout.write(json.dumps({"jsonrpc": "2.0", **o}) + "\n"), sys.stdout.flush())
note = lambda sid, u: out({"method": "session/update", "params": {"sessionId": sid, "update": u}})
asked = {}
for raw in sys.stdin:
    m = json.loads(raw)
    log.write(raw); log.flush()
    meth, p = m.get("method"), m.get("params") or {}
    if "result" in m:                       # Hearth answering a permission request
        asked[m["id"]] = m["result"]
        continue
    if meth == "initialize":
        out({"id": m["id"], "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True,
             "sessionCapabilities": {"resume": {}}}}})
    elif meth in ("session/new", "session/resume"):
        sid = p.get("sessionId") or "ses_1"
        out({"id": m["id"], "result": {"sessionId": sid, "configOptions": [{"id": "model", "category": "model",
             "type": "select", "currentValue": "fast", "options": [{"value": "fast", "name": "Fast"}, {"value": "smart", "name": "Smart"}]}]}})
    elif meth == "session/set_config_option":
        out({"id": m["id"], "result": {}})
    elif meth == "session/prompt":
        sid = p["sessionId"]
        text = p["prompt"][0]["text"]
        note(sid, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "Thinking."}})
        note(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "bash", "kind": "execute", "status": "pending", "rawInput": {}})
        out({"id": 900, "method": "session/request_permission", "params": {"sessionId": sid,
             "toolCall": {"toolCallId": "t1", "title": "rm -rf build", "kind": "execute", "rawInput": {"command": "rm -rf build"}},
             "options": [{"optionId": "yes", "kind": "allow_once", "name": "Allow"}, {"optionId": "no", "kind": "reject_once", "name": "Reject"}]}})
        while 900 not in asked:
            r = json.loads(sys.stdin.readline()); log.write(json.dumps(r) + "\n"); log.flush()
            if "result" in r:
                asked[r["id"]] = r["result"]
        if asked[900]["outcome"].get("optionId") == "no":
            note(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "failed", "content": []})
            out({"id": m["id"], "result": {"stopReason": "end_turn"}})
            del asked[900]
            continue
        del asked[900]
        note(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress", "kind": "execute", "title": "rm -rf build", "rawInput": {"command": "rm -rf build"}})
        note(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed", "content": [{"type": "content", "content": {"type": "text", "text": "removed"}}]})
        note(sid, {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "hearth_brain_memory_save", "kind": "other", "status": "in_progress", "rawInput": {"text": "likes teal"}})
        note(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "completed", "content": [{"type": "content", "content": {"type": "text", "text": "saved"}}]})
        note(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "All "}})
        note(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done."}})
        note(sid, {"sessionUpdate": "usage_update", "used": 10, "size": 128000})
        out({"id": m["id"], "result": {"stopReason": "end_turn", "usage": {"inputTokens": 12, "outputTokens": 3}}})
''', encoding="utf-8")

cb.ACP["stand-in-acp"] = {"label": "Stand-in", "plan": "Stand-in", "site": "https://example.org", "agent": "stand-in",
                          "args": [agent, log]}
cb.BRAINS["stand-in-acp"] = "Stand-in"
cb.SITES["stand-in-acp"] = "https://example.org"
xa.AGENTS["stand-in"] = {"label": "Stand-in", "exe": ["stand-in"], "args": lambda allow: [], "stream": "text"}
real_which = xa._which
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)
cb.ensure_bridge = lambda: None
web._save_settings(dict(web._load_settings(), onboarded=True))

check("an installed protocol agent is offered as a brain",
      any(b["id"] == "stand-in-acp" and b["installed"] for b in cb.choices()))
check(f"it can be picked {cb.set_brain('stand-in-acp')!r}", cb.set_brain("stand-in-acp") is None and cb.current() == "stand-in-acp")
s = web._load_settings()
s["stand-in-acp_model"] = "smart"
web._save_settings(s)

events, asked = [], []


def emit(kind, **f):
    events.append((kind, f))


def allow(name, args):
    asked.append((name, args))
    return "allow"


asyncio.run(cb.run_turn("clean the build", emit=emit, history=[], permission_check=allow))
calls = [(f["name"], f["args"]) for k, f in events if k == "tool_call"]
results = [f["name"] for k, f in events if k == "tool_result"]
check(f"the command it wants to run is asked about, as the command {asked}", asked == [("run_command", {"command": "rm -rf build"})])
check(f"its tool calls show as Hearth's tools, a Hearth tool by its own name {calls} {results}",
      calls[0] == ("run_command", {"command": "rm -rf build"}) and calls[1][0] == "memory_save" and results == ["run_command", "memory_save"])
reply = next((f["content"] for k, f in events if k == "assistant"), "")
done = next(f for k, f in events if k == "done")
check(f"the reply and the turn's numbers come back {reply!r} {done['stats']}",
      reply == "All done." and done["stats"]["tokens_in"] == 12 and done["stats"]["ctx"] == 128000 and done["stats"]["model"] == "smart")
sent = [json.loads(l) for l in open(log, encoding="utf-8")]
new = next(m for m in sent if m.get("method") == "session/new")
check("Hearth's tools are attached as an MCP server", new["params"]["mcpServers"][0]["name"] == "hearth_brain")
check("the chosen model is set on the session",
      any(m.get("method") == "session/set_config_option" and m["params"]["value"] == "smart" for m in sent))
check("the persona goes in front of a new chat's first message",
      "brain of Inglen" in next(m for m in sent if m.get("method") == "session/prompt")["params"]["prompt"][0]["text"])

# The next message in the same chat goes on with the same session.
events.clear(); asked.clear()
hist = [{"role": "user", "content": "clean the build"}, {"role": "assistant", "content": "All done."}]
asyncio.run(cb.run_turn("thanks", emit=emit, history=hist, permission_check=lambda n, a: "deny"))
sent = [json.loads(l) for l in open(log, encoding="utf-8")]
check("the next message resumes its session", any(m.get("method") == "session/resume" and m["params"]["sessionId"] == "ses_1" for m in sent))
reply = next((f["content"] for k, f in events if k == "assistant"), "")
check(f"told no, it says what it stopped at instead of blaming the sign-in {reply!r}",
      "rm -rf build" in reply and not any(k == "error" for k, f in events))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all ACP brain checks passed")
