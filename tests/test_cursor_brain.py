"""Cursor's agent as the assistant's brain.

A stand-in for `cursor-agent -p` speaks its stream format, so this checks what
Hearth does with it without a real install or a bill: replies stream once
(Cursor repeats each stretch of text as a whole message), its tools show as
the Hearth tools they amount to, the persona rides in front of a chat's first
message only, the next turn resumes the session, and the folder gets the one
permission Hearth needs without losing what the user already had there.

    .\\.venv\\Scripts\\python.exe tests\\test_cursor_brain.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


work = tempfile.mkdtemp(prefix="hearth-cur-")
os.environ["JARVIS_WORKSPACE"] = work
os.environ["HEARTH_WORKSPACE"] = work
from hearth import claude_brain as cb                                # noqa: E402
from hearth import cursor_brain as cur                               # noqa: E402
from hearth import tools                                             # noqa: E402

cb._HOME = work
cb._SESSIONS = os.path.join(work, "sessions.json")
cur._LIST_CACHE = os.path.join(work, "cursor_models.json")
cb.set_bridge_url("http://127.0.0.1:9")
cb.current = lambda: "cursor-agent"
argv_log = os.path.join(work, "argv.jsonl")
fake = os.path.join(work, "fake_cursor.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write(f"""import json, sys
if "--list-models" in sys.argv:
    print("Available models\\n\\nauto - Auto (current, default)\\ngpt-5.2 - GPT-5.2\\n"
          "claude-opus-5-thinking-high - Claude Opus 5 1M Thinking\\n\\nTip: use --model")
    sys.exit(0)
open({argv_log!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read()}}) + "\\n")
out = lambda o: print(json.dumps(o), flush=True)
sid = "sess-9"
piece = lambda t: out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": t}}]}}, "timestamp_ms": 1}})
out({{"type": "system", "subtype": "init", "session_id": sid, "model": "Auto"}})
out({{"type": "thinking", "subtype": "delta", "text": "Reading the list."}})
piece("On "); piece("it.")
piece("On it.")                                   # the stretch, repeated whole
out({{"type": "tool_call", "subtype": "started", "call_id": "c0", "tool_call": {{"getMcpToolsToolCall": {{"args": {{"server": "hearth_brain"}}}}}}}})
out({{"type": "tool_call", "subtype": "started", "call_id": "c1", "tool_call": {{"readToolCall": {{"args": {{"path": "list.txt"}}}}}}}})
out({{"type": "tool_call", "subtype": "completed", "call_id": "c1", "tool_call": {{"readToolCall": {{"args": {{"path": "list.txt"}}, "result": {{"success": {{"content": "milk, eggs"}}}}}}}}}})
out({{"type": "tool_call", "subtype": "started", "call_id": "c2", "tool_call": {{"mcpToolCall": {{"args": {{"name": "hearth_brain-open_app", "toolName": "open_app", "args": {{"name": "Spotify"}}}}}}}}}})
out({{"type": "tool_call", "subtype": "completed", "call_id": "c2", "tool_call": {{"mcpToolCall": {{"args": {{"toolName": "open_app", "args": {{"name": "Spotify"}}}}, "result": {{"success": {{"content": [{{"text": {{"text": "Opened Spotify"}}}}]}}}}}}}}}})
out({{"type": "tool_call", "subtype": "started", "call_id": "c3", "tool_call": {{"shellToolCall": {{"args": {{"command": "del x"}}}}}}}})
out({{"type": "tool_call", "subtype": "completed", "call_id": "c3", "tool_call": {{"shellToolCall": {{"result": {{"rejected": {{"reason": ""}}}}}}}}}})
# Two calls side by side: both start, then both finish.
out({{"type": "tool_call", "subtype": "started", "call_id": "c4", "tool_call": {{"webSearchToolCall": {{"args": {{"searchTerm": "wan 2.2 vram"}}}}}}}})
out({{"type": "tool_call", "subtype": "started", "call_id": "c5", "tool_call": {{"mcpToolCall": {{"args": {{"toolName": "get_time", "args": {{}}}}}}}}}})
out({{"type": "tool_call", "subtype": "completed", "call_id": "c4", "tool_call": {{"webSearchToolCall": {{"args": {{"searchTerm": "wan 2.2 vram"}}, "result": {{"rejected": {{"reason": "User Rejected"}}}}}}}}}})
out({{"type": "tool_call", "subtype": "completed", "call_id": "c5", "tool_call": {{"mcpToolCall": {{"args": {{"toolName": "get_time", "args": {{}}}}, "result": {{"success": {{"content": [{{"text": {{"text": "noon"}}}}]}}}}}}}}}})
piece("Milk and eggs.")
piece("Milk and eggs.")                           # repeated whole, mid-turn
piece("Anything else?")
out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Anything else?"}}]}}}})
out({{"type": "result", "subtype": "success", "is_error": False, "result": "On it.Milk and eggs.Anything else?", "session_id": sid,
     "usage": {{"inputTokens": 900, "outputTokens": 12}}}})
""")
real_exe_for = cb.exe_for
cb.exe_for = lambda p: [sys.executable, fake] if p == "cursor-agent" else real_exe_for(p)


def turn(prompt, history):
    events = []
    asyncio.run(cb.run_turn(prompt, emit=lambda t, **f: events.append((t, f)), history=history,
                            permission_check=lambda n, a: "allow"))
    return events


os.makedirs(os.path.join(work, ".cursor"))
with open(os.path.join(work, ".cursor", "cli.json"), "w") as f:
    json.dump({"permissions": {"allow": ["Shell(ls)"], "deny": ["Read(secrets/**)"]}, "theme": "mine"}, f)
tools.approval_mode = lambda: "ask"
ev = turn("what's on my list?", [])
chunks = "".join(f["content"] for t, f in ev if t == "assistant_chunk")
calls = [(f["name"], f["args"]) for t, f in ev if t == "tool_call"]
results = {f["name"]: f["content"] for t, f in ev if t == "tool_result"}
check(f"each stretch of text reaches the chat once, as its own paragraph {chunks!r}",
      chunks == "On it.\n\nMilk and eggs.\n\nAnything else?")
check("its thinking shows as thinking", any(t == "thinking_chunk" for t, _ in ev))
check("its own file read shows as read_file", ("read_file", {"path": "list.txt"}) in calls)
check("a Hearth tool shows under its own name with its result",
      ("open_app", {"name": "Spotify"}) in calls and results.get("open_app") == "Opened Spotify")
check("looking up a tool's schema is left out", len(calls) == 5)
by_id = {f["id"]: f["content"] for t, f in ev if t == "tool_result"}
check("calls made side by side carry their ids, so each result finds its own call",
      [f["id"] for t, f in ev if t == "tool_call"] == ["c1", "c2", "c3", "c4", "c5"] and by_id.get("c5") == "noon")
check("its own web search shows what was searched for", ("web_search", {"query": "wan 2.2 vram"}) in calls)
check("a tool of its own that Cursor turned away isn't blamed on the user",
      by_id.get("c4", "").startswith("Not done") and "User Rejected" not in by_id.get("c4", ""))
check("a command Cursor's own shell was refused says so", results.get("run_command", "").startswith("Not done"))
check("the turn ends with done, naming Cursor", ev[-1][0] == "done" and ev[-1][1]["stats"]["brain"] == "Cursor")

first = json.loads(open(argv_log).read().splitlines()[0])
check("it runs headless with streamed JSON and the server pre-approved",
      first["argv"][:4] == ["-p", "--output-format", "stream-json", "--stream-partial-output"]
      and "--approve-mcps" in first["argv"] and "--resume" not in first["argv"])
check("a chat's first message carries the persona in front",
      first["stdin"].endswith("what's on my list?") and "running inside Cursor" in first["stdin"])

cli = json.load(open(os.path.join(work, ".cursor", "cli.json")))
mcp = json.load(open(os.path.join(work, ".cursor", "mcp.json")))
check("the folder allows Hearth's server, and on Ask turns Cursor's direct writes away",
      "Mcp(hearth_brain:*)" in cli["permissions"]["allow"] and "Write(**)" in cli["permissions"]["deny"])
check("its own web search is allowed, as Hearth's is", cli.get("autoAcceptWebSearch") is True)
check("what was already in that file stays",
      "Shell(ls)" in cli["permissions"]["allow"] and "Read(secrets/**)" in cli["permissions"]["deny"]
      and cli.get("theme") == "mine")
check("Hearth's server is attached, asking before risky tools itself",
      mcp["mcpServers"]["hearth_brain"]["env"].get("HEARTH_BRAIN_GATE") == "1")

tools.approval_mode = lambda: "edits"
hist = [{"role": "user", "content": "what's on my list?"}, {"role": "assistant", "content": "Milk and eggs."}]
turn("and the eggs?", hist)
second = json.loads(open(argv_log).read().splitlines()[1])
check("the next turn resumes the session and sends only the new message",
      second["argv"][second["argv"].index("--resume") + 1] == "sess-9" and second["stdin"] == "and the eggs?")
cli = json.load(open(os.path.join(work, ".cursor", "cli.json")))
check("off Ask, its direct writes are allowed again", "Write(**)" not in cli["permissions"]["deny"]
      and cli["permissions"]["allow"].count("Mcp(hearth_brain:*)") == 1)

ms = cur.models()
check(f"its models are the ones it lists {[m['id'] for m in ms]}",
      [(m["id"], m["label"]) for m in ms] == [("", "Default"), ("gpt-5.2", "GPT-5.2"),
                                              ("claude-opus-5-thinking-high", "Claude Opus 5 1M Thinking")]
      and ms[2]["ctx"] == 1000000)

cb.exe_for = lambda p: None
ev = turn("hi", [])
check("without it installed it says how to get it", any(t == "error" and "isn't installed" in f["message"] for t, f in ev))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all cursor brain checks passed")
