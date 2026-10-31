"""A saved secret never reaches a model or the phone: tool results, the tool
server agent brains use, and what an agent session or agent brain shows are
cleaned of the person's own saved keys (matched exactly, whatever their shape)
and of key shapes. Stand-in keys, home and workspace.

    .\\.venv\\Scripts\\python.exe tests\\test_redact_tool_results.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import asyncio
import os
import sys

WS = _home.WORKSPACE
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
for k in ("LOCAL_API_KEY", "HEARTH_AUTO_APPROVE"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


SHAPELESS = "custom-STANDIN-no-known-shape-4417"      # a custom server's key: no prefix to spot it by
ANT = "sk-ant-STANDINSTANDINSTANDINSTANDIN99"
BOT = "STANDIN-BOT-TOKEN-shapeless-2203"
UUID = "123e4567-e89b-12d3-a456-426614174000"

from hearth import keystore, tools  # noqa: E402
tools.WORKSPACE = WS
keystore.write_settings({"llm_provider": "custom", "llm_key": SHAPELESS, "provider_keys": {"anthropic": ANT},
                         "approval_mode": "auto"}, os.path.join(WS, "settings.json"))
keystore.write_json(os.path.join(_home.HOME, ".hearth", "phone_bridge.json"), {"bot_token": BOT})

notes = os.path.join(WS, "notes.txt")
with open(notes, "w", encoding="utf-8") as f:
    f.write(f"server key {SHAPELESS}\nanthropic {ANT}\nbot {BOT}\njob id {UUID}\nnothing else\n")

out = tools.execute_tool("read_file", {"path": notes})
check(f"a tool result has no saved key in it ({out[:120]!r})",
      SHAPELESS not in out and ANT not in out and BOT not in out and "<REDACTED_KEY>" in out)
check("and keeps the rest, ids included", "nothing else" in out and UUID in out)

os.environ.update(HEARTH_BRAIN_URL="", HEARTH_BRAIN_GATE="")
from hearth import mcp_server  # noqa: E402
out = str(mcp_server._run("read_file", {"path": notes}))
check("through the tool server too", SHAPELESS not in out and ANT not in out)

from hearth.channel_core import out_of  # noqa: E402
check("a step's output on the phone is cleaned", SHAPELESS not in out_of(f"printed {SHAPELESS}"))

seen = []
from hearth import agent_sessions, claude_brain as cb  # noqa: E402


async def fake_run(agent, exe, sid, cwd, text, emit, ask, drain=None, **_):
    emit("live", text=f"your key is {SHAPELESS}")
    emit("step", words=f"cat key {BOT}", tool="run_command", cmd=f"echo {ANT}")
    emit("step_out", out=f"{BOT}")
    emit("text", text=f"done {ANT}")
    return 0

agent_sessions.parse_chat = lambda cid: ("opencode", "s1")
agent_sessions.read = lambda agent, sid: {"cwd": WS, "path": ""}
agent_sessions.live_state = lambda *a: ""
agent_sessions._exe = lambda agent: ["opencode"]
agent_sessions.run_in = fake_run
asyncio.run(agent_sessions.say("x", "hi", lambda k, **f: seen.append((k, f)), lambda q: False))
flat = repr(seen)
check(f"what an agent session sends to the phone or window is cleaned ({len(seen)} events)",
      seen and SHAPELESS not in flat and ANT not in flat and BOT not in flat)

seen.clear()


async def fake_turn(prompt, emit, history, check_, *a):
    emit("tool_result", name="run_command", content=f"key={SHAPELESS}", id="t1")
    emit("assistant_chunk", content=f"here: {ANT}")
    emit("done", reply=f"it was {BOT}")
    return 0

cb._turn_on = fake_turn
cb.current = lambda: "codex"
cb.used_up = lambda agent: None
cb.helper_news = lambda emit=None: []
asyncio.run(cb.run_turn("hi", emit=lambda k, **f: seen.append((k, f)), history=[], permission_check=None))
flat = repr(seen)
check("what an agent brain shows is cleaned", seen and SHAPELESS not in flat and ANT not in flat and BOT not in flat)

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall redaction checks passed")
