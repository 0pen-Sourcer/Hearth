"""Inglen's brain reaches the coding agents' sessions on this PC: lists them,
reads where one got to, and sends one a message, getting its reply.

The owner's ask: "add this reaching out thing in Inglen too". The phone could
already open and carry on a Claude Code or Codex session (agent_sessions); the
brain had no way to. Looking is free; sending asks first in Ask mode, and in
Auto goes ahead as delegate does. The reply is the other agent's words, marked
so, never the person's.

    .\\.venv\\Scripts\\python.exe tests\\test_agent_sessions_tool.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-agent-tool-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
WS = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(WS)

from hearth import agent_sessions as S, tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def settings(**kw):
    with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(kw, f)


GAME = os.path.join(HOME, "code", "night-courier")
os.makedirs(GAME)
SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
path = os.path.join(HOME, ".claude", "projects", "-code-night-courier", SID + ".jsonl")
os.makedirs(os.path.dirname(path))
T = "2026-10-28T10:00:00Z"
with open(path, "w", encoding="utf-8") as f:
    for o in ({"type": "user", "cwd": GAME, "timestamp": T, "message": {"role": "user", "content": "make the menu stop flashing"}},
              {"type": "assistant", "cwd": GAME, "timestamp": T, "message": {"role": "assistant", "content": [
                  {"type": "tool_use", "name": "Bash", "id": "t1", "input": {"command": "npm test"}},
                  {"type": "text", "text": "The menu reloads on every focus; it loads once now."}]}}):
        f.write(json.dumps(o) + "\n")
os.utime(path, (1, 1))          # long ago: not "working now"
CID = S.chat_id("claude-code", SID)

out = tools.execute_tool("agent_sessions", {"action": "list"})
check(f"list names each session with its id, agent, title and project ({out!r})",
      CID in out and "Claude Code" in out and "make the menu stop flashing" in out and "night-courier" in out)
check("and filters by a word", CID in tools.execute_tool("agent_sessions", {"action": "list", "query": "courier"})
      and "No coding-agent sessions" in tools.execute_tool("agent_sessions", {"action": "list", "query": "spreadsheet"}))
out = tools.execute_tool("agent_sessions", {"action": "read", "session": CID})
check(f"read says where it got to, steps and words ({out[-160:]!r})",
      "[Person] make the menu stop flashing" in out and "Ran npm test" in out and "it loads once now" in out)

sent = {}


async def fake_say(cid, text, emit, ask):
    sent.update(cid=cid, text=text)
    emit("text", text="Tests pass: 12 of 12. Approve the merge?")
    return 0

S.say = fake_say
out = tools.execute_tool("agent_sessions", {"action": "send", "session": CID, "message": "run the tests"})
check(f"send carries the session on with their message ({sent})", sent == {"cid": CID, "text": "run the tests"})
check(f"and the reply comes back as that agent's words, not the person's ({out!r})",
      out.startswith("Claude Code answered (its words, not the person's") and "12 of 12" in out)
check("a session id that isn't one is refused", tools.execute_tool("agent_sessions", {"action": "read", "session": "x"}).startswith("Error"))

settings(approval_mode="ask")
check("looking needs no yes", tools.mode_allows("agent_sessions", {"action": "list"})
      and tools.mode_allows("agent_sessions", {"action": "read", "session": CID}))
check("sending asks in Ask mode", tools.mode_allows("agent_sessions", {"action": "send", "session": CID, "message": "x"}) is False)
settings(approval_mode="auto")
check("and in Auto goes ahead, as delegate does",
      tools.mode_allows("agent_sessions", {"action": "send", "session": CID, "message": "x"}) is True)
check("it is held back until asked for, and load_tools finds it",
      "agent_sessions" in tools._DEFERRED_TOOLS and "agent_sessions" in tools.execute_tool("load_tools", {"query": "coding agent session"}))

from hearth.term_render import tool_words  # noqa: E402
said = [tool_words("agent_sessions", a) for a in ({"action": "list"}, {"action": "read", "session": CID},
                                                   {"action": "send", "session": CID, "message": "x"})]
check(f"each step is said in words {said}",
      said == ["Looked at the coding agents' sessions", "Read Claude Code's session", "Sent a message to Claude Code"])
print("\n" + ("all agent session tool checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
