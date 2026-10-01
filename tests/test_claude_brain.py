"""Claude Code as the assistant's brain.

A stand-in for `claude -p` speaks the same stream format, so this checks what
Hearth does with it without a real install or a bill: text streams into the
chat, Claude Code's own tools show as the Hearth tools they amount to (a shell
command as a command, an edit as a diff), the next turn of the same chat
resumes the same session, and a permission question is answered by the turn's
own check, or only by the approval switch when nobody is watching.

    .\\.venv\\Scripts\\python.exe tests\\test_claude_brain.py
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


work = tempfile.mkdtemp(prefix="hearth-cb-")
os.environ["JARVIS_WORKSPACE"] = work
from hearth import claude_brain as cb                                # noqa: E402

cb._HOME = work
cb._SESSIONS = os.path.join(work, "claude_sessions.json")
cb.set_bridge_url("http://127.0.0.1:9")
argv_log = os.path.join(work, "argv.jsonl")
fake = os.path.join(work, "fake_claude.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write(f"""import json, sys
open({argv_log!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read()}}) + "\\n")
out = lambda o: print(json.dumps(o), flush=True)
out({{"type": "system", "subtype": "init", "session_id": "sess-123"}})
for w in ["I'm ", "JARVIS."]:
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": w}}}}}})
out({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": "t1", "name": "PowerShell", "input": {{"command": "Get-Date"}}}},
     {{"type": "tool_use", "id": "t2", "name": "Edit", "input": {{"file_path": "a.txt", "old_string": "x", "new_string": "y"}}}},
     {{"type": "tool_use", "id": "t3", "name": "ToolSearch", "input": {{"query": "x"}}}}]}}}})
out({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t1", "content": [{{"type": "text", "text": "Wednesday"}}]}},
     {{"type": "tool_result", "tool_use_id": "t2", "content": "edited"}}, {{"type": "tool_result", "tool_use_id": "t3", "content": "ok"}}]}}}})
out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "Done."}}}}}})
out({{"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-123", "num_turns": 3, "total_cost_usd": 0.01, "result": "I'm JARVIS.Done."}})
""")
cb.claude_exe = lambda: [sys.executable, fake]


def turn(prompt, history):
    events = []
    asyncio.run(cb.run_turn(prompt, emit=lambda t, **f: events.append((t, f)), history=history,
                            permission_check=lambda n, a: "allow"))
    return events


ev = turn("hello", [])
chunks = "".join(f["content"] for t, f in ev if t == "assistant_chunk")
calls = [(f["name"], f["args"]) for t, f in ev if t == "tool_call"]
results = [f["name"] for t, f in ev if t == "tool_result"]
check("text streams into the chat", chunks.startswith("I'm JARVIS."))
check("text after a tool call starts on a new paragraph", "\n\nDone." in chunks)
check("a shell command shows as run_command", ("run_command", {"command": "Get-Date", "shell": "powershell"}) in calls)
check("an edit shows as edit_file with its diff",
      any(n == "edit_file" and a["edits"][0] == {"old_text": "x", "new_text": "y"} for n, a in calls))
check("Claude Code's bookkeeping is left out", all(n != "ToolSearch" for n, _ in calls))
check("each tool gets its result", results == ["run_command", "edit_file"])
check("the full reply is reported for the bridges", any(t == "assistant" and f["content"].endswith("Done.") for t, f in ev))
check("the turn ends with done", ev[-1][0] == "done")
first = json.loads(open(argv_log).read().splitlines()[0])
check("the persona and Hearth's tools are passed", "--append-system-prompt-file" in first["argv"]
      and "--mcp-config" in first["argv"] and "mcp__hearth__approve" in first["argv"])
check("a new chat starts a new session", "--resume" not in first["argv"])

turn("and now?", [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "I'm JARVIS."}])
second = json.loads(open(argv_log).read().splitlines()[1])
check("the next turn of the same chat resumes its session",
      "--resume" in second["argv"] and second["argv"][second["argv"].index("--resume") + 1] == "sess-123")
check("a resumed turn sends only the new message", second["stdin"] == "and now?")

turn("what did we say?", [{"role": "user", "content": "a chat from another brain"},
                          {"role": "assistant", "content": "an answer about otters"}])
third = json.loads(open(argv_log).read().splitlines()[2])
check("a chat begun on another brain is passed as context", "otters" in third["stdin"])

# Permission questions
asked = []
cb._checks[cb._TOKEN] = lambda n, a: (asked.append((n, a)), "allow")[1]
r = cb.decide(cb._TOKEN, "Bash", {"command": "del x.txt"})
check("Claude's shell command is asked about as run_command", asked[-1] == ("run_command", {"command": "del x.txt", "shell": "bash"}))
check("an allow lets it through unchanged", r == {"behavior": "allow", "updatedInput": {"command": "del x.txt"}})
cb._checks[cb._TOKEN] = lambda n, a: {"decision": "deny", "reason": "use the bin instead"}
r = cb.decide(cb._TOKEN, "mcp__hearth__delete_path", {"path": "x"})
check("a decline carries the user's reason", r["behavior"] == "deny" and "use the bin instead" in r["message"])
check("a question without the right token is refused", cb.decide("nope", "Bash", {})["behavior"] == "deny")
cb._checks.pop(cb._TOKEN, None)
from hearth import tools                                             # noqa: E402
real_mode = tools.approval_mode
tools.approval_mode = lambda: "ask"
check("with nobody watching, Ask lets nothing risky through",
      cb.decide(cb._TOKEN, "Bash", {"command": "del x"})["behavior"] == "deny")
tools.approval_mode = real_mode

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all claude brain checks passed")
