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
cb._SEEN = os.path.join(work, "agent_models.json")
cb._LIST_CACHE = os.path.join(work, "claude_models.json")
cb.set_bridge_url("http://127.0.0.1:9")
argv_log = os.path.join(work, "argv.jsonl")
fake = os.path.join(work, "fake_claude.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write(f"""import json, sys, time
said = sys.stdin.readline()                       # stdin stays open: one message a line
out = lambda o: print(json.dumps(o), flush=True)
if '"initialize"' in said:
    out({{"type": "control_response", "response": {{"subtype": "success", "request_id": "models", "response": {{"models": [
        {{"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
          "description": "Opus 5.5 \\u00b7 Best for everyday, complex tasks", "supportsEffort": True,
          "supportedEffortLevels": ["low", "medium", "high"]}},
        {{"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus",
          "description": "Opus 5.5 \\u00b7 Best for everyday, complex tasks", "supportsEffort": True,
          "supportedEffortLevels": ["low", "medium", "high"]}},
        {{"value": "haiku", "resolvedModel": "claude-haiku-4-5", "displayName": "Haiku",
          "description": "Haiku 4.5 \\u00b7 Fastest for quick answers"}},
        {{"value": "fable", "resolvedModel": "claude-fable-5-1", "displayName": "Fable 5.1",
          "description": "For your toughest challenges"}}]}}}}}})
    sys.exit(0)
said = json.loads(said)["message"]["content"][0]["text"]
open({argv_log!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "stdin": said}}) + "\\n")
out({{"type": "system", "subtype": "init", "session_id": "sess-123"}})
for w in ["I'm ", "JARVIS."]:
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": w}}}}}})
out({{"type": "stream_event", "event": {{"type": "content_block_start", "content_block": {{"type": "tool_use", "name": "Write", "id": "t0"}}}}}})
for piece in ['{{"file_path": "notes.txt", "content": "line one\\nline two is long enough', ' to be passed on as it is written"}}']:
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "input_json_delta", "partial_json": piece}}}}}})
out({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": "t1", "name": "PowerShell", "input": {{"command": "Get-Date"}}}},
     {{"type": "tool_use", "id": "t2", "name": "Edit", "input": {{"file_path": "a.txt", "old_string": "x", "new_string": "y"}}}},
     {{"type": "tool_use", "id": "t3", "name": "ToolSearch", "input": {{"query": "x"}}}}]}}}})
out({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t1", "content": [{{"type": "text", "text": "Wednesday"}}]}},
     {{"type": "tool_result", "tool_use_id": "t2", "content": "edited"}}, {{"type": "tool_result", "tool_use_id": "t3", "content": "ok"}}]}}}})
out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "Done."}}}}}})
out({{"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-123", "num_turns": 3, "total_cost_usd": 0.01, "result": "I'm JARVIS.Done.", "fast_mode_state": "off",
     "modelUsage": {{"claude-haiku-4-5": {{"outputTokens": 12, "contextWindow": 200000}},
                    "claude-opus-5-5": {{"outputTokens": 480, "contextWindow": 1000000}}}}}})
# Anything said while it worked is its next step; stdin closing ends it.
for more in sys.stdin:
    told = json.loads(more)["message"]["content"][0]["text"]
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "Told: " + told}}}}}})
    out({{"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-123", "num_turns": 4, "total_cost_usd": 0.02, "result": "Told: " + told}})
""")
cb.claude_exe = lambda: [sys.executable, fake]


listed = cb.agent_models("claude-code")["models"]
check("the models are the ones Claude Code itself lists, named by version",
      [(m["id"], m["label"]) for m in listed][:3] == [("", "Default"), ("opus", "Opus 5.5"), ("haiku", "Haiku 4.5")])
# Newer versions name the model itself and keep the description for what it's for.
check("a model named by its version keeps that name, with its description underneath",
      (listed[3]["label"], listed[3]["about"]) == ("Fable 5.1", "For your toughest challenges"))
check("each with the effort levels it takes, or none",
      listed[1]["efforts"] == ["low", "medium", "high"] and listed[2]["efforts"] == [])
check("the context window isn't known before a reply", listed[0]["ctx"] is None and cb.context_window("claude-code") is None)


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
partial = [f for t, f in ev if t == "tool_call_partial"]
check("a file being written is passed on while Claude Code is still writing it",
      bool(partial) and partial[0]["name"] == "write_file" and "line one" in partial[0]["args_text"])
check("Claude Code's bookkeeping is left out", all(n != "ToolSearch" for n, _ in calls))
check("each tool gets its result", results == ["run_command", "edit_file"])
check("calls made side by side carry their ids, so each result finds its own call",
      [f["id"] for t, f in ev if t == "tool_call"] == ["t1", "t2"]
      and [(f["id"], f["content"]) for t, f in ev if t == "tool_result"] == [("t1", "Wednesday"), ("t2", "edited")])
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

check("the model that answered and its context window are remembered",
      cb.context_window("claude-code") == 1000000
      and cb.agent_models("claude-code")["models"][1]["ctx"] == 1000000)
real_model, real_effort = cb.chosen_model, cb.chosen_effort
cb.chosen_model, cb.chosen_effort = (lambda p: "opus"), (lambda: "high")
turn("think hard", [])
argv = json.loads(open(argv_log).read().splitlines()[-1])["argv"]
check("the picked model and the think button's effort are passed",
      argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "high")
cb.chosen_model = lambda p: "haiku"
turn("quick one", [])
argv = json.loads(open(argv_log).read().splitlines()[-1])["argv"]
check("a model that takes no effort level isn't given one", "--effort" not in argv and "haiku" in argv)
cb.chosen_model, cb.chosen_effort = real_model, real_effort

# Something typed while it works reaches it, in the same run.
waiting = ["actually, use tabs"]
events = []
asyncio.run(cb.run_turn("format it", emit=lambda t, **f: events.append((t, f)), history=[],
                        permission_check=lambda n, a: "allow",
                        drain_steer=lambda: [waiting.pop()] if waiting else []))
argv = json.loads(open(argv_log).read().splitlines()[-1])["argv"]
check("it is started able to take messages while it works",
      argv[argv.index("--input-format") + 1] == "stream-json")
check("what is typed mid-reply is passed on and shown as passed on",
      ("steered", {"text": "actually, use tabs"}) in events)
check("and it acts on it in the same run",
      "Told: actually, use tabs" in "".join(f["content"] for t, f in events if t == "assistant_chunk"))
check("the turn still ends once, with done", [t for t, _ in events].count("done") == 1 and events[-1][0] == "done")

# Helpers. The brain sends one of Hearth's own through the bridge, the turn
# stays open until it reports, and the report is handed over in the same run.
import threading                                                     # noqa: E402
import time                                                          # noqa: E402
from hearth import subagents as sa                                   # noqa: E402
from hearth import tools                                             # noqa: E402

tools._notify = lambda *a, **k: None
real_execute = tools.execute_tool
AID = "sub_1700000000000_abc123"


def stand_in_spawn(name, args):
    with sa._BG_RESULTS_LOCK:
        sa._BG_RUNNING.add(AID)

    def finish():
        time.sleep(1.5)
        with sa._BG_RESULTS_LOCK:
            sa._BG_RUNNING.discard(AID)
        sa._default_completion_cb(AID, {"ok": True, "text": "17*23 = 391", "name": "Maya", "persona": "researcher"})

    threading.Thread(target=finish, daemon=True).start()
    return json.dumps({"ok": True, "status": "launched", "agent_id": AID, "mode": args.get("mode")})


tools.execute_tool = stand_in_spawn
events, sent = [], []


def emit_and_spawn(t, **f):
    events.append((t, f))
    if t == "tool_call" and not sent:                 # the brain calling spawn_subagent
        sent.append(cb.run_tool(cb._TOKEN, "spawn_subagent", {"persona": "researcher", "prompt": "17*23", "mode": "sync"}))


t0 = time.time()
asyncio.run(cb.run_turn("ask Maya", emit=emit_and_spawn, history=[], permission_check=lambda n, a: "allow"))
said = "".join(f["content"] for t, f in events if t == "assistant_chunk")
check("a helper the brain sends is started in the background, whatever mode it asked for",
      '"mode": "background"' in sent[0]["result"])
check("the turn stays open until the helper has reported", time.time() - t0 >= 1.4)
check("its report is handed to the brain in the same run", "Told: [Background event" in said and "17*23 = 391" in said)
check("and shows in the chat as a finished helper",
      any(t == "subagent_done" and f["name"] == "Maya" and f["agent_id"] == AID for t, f in events))
check("then the turn ends once", [t for t, _ in events].count("done") == 1)
tools.execute_tool = real_execute

check("a helper call without the right token is refused",
      cb.run_tool("nope", "spawn_subagent", {})["result"].startswith("Error"))
check("only the helper tools are run this way", cb.run_tool(cb._TOKEN, "run_command", {"command": "x"})["result"].startswith("Error"))
# An agent brain couldn't start a team at all: launch_team wasn't among its tools.
_ex = tools.execute_tool
tools.execute_tool = lambda n, a: json.dumps({"ok": True, "spawned": [{"agent_id": "sub_1_aaaaaa"}, {"agent_id": "sub_2_bbbbbb"}]})
cb._sent.clear()
r = cb.run_tool(cb._TOKEN, "launch_team", {"members": []})
check("a team started by an agent brain runs in Hearth, and the turn waits for every member",
      '"ok": true' in r["result"] and set(cb._sent) == {"sub_1_aaaaaa", "sub_2_bbbbbb"})
cb._sent.clear()
tools.execute_tool = _ex
# delegate from an agent brain waited inside the brain's tool server, which ends
# with the brain's turn: the answer was lost and the call stayed "running".
from hearth import external_agents as _xa, subagents as _sa   # noqa: E402
_xspawn, _later = _xa.spawn, []
_xa.spawn = lambda agent, task, cwd="", on_done=None, **k: (
    _later.append(on_done) or {"ok": True, "agent_id": "sub_7_cccccc", "label": "OpenCode"})
_sa.drain_pending_notifications()
t0 = time.time()
r = cb.run_tool(cb._TOKEN, "delegate", {"agent": "opencode", "task": "say hi"})
check(f"delegate from an agent brain returns at once and the turn waits for it {r['result'][:60]!r}",
      time.time() - t0 < 5 and "sub_7_cccccc" in r["result"] and "sub_7_cccccc" in cb._sent)
_later[0]("sub_7_cccccc", {"ok": True, "text": "hi there", "name": "OpenCode"})
news = cb.helper_news()
check(f"its answer arrives as a report {news[:1]}", any("hi there" in n for n in news))
_xa.spawn = _xspawn
cb._sent.clear()

# A report that was waiting when the user next speaks goes in ahead of what they said.
sa._default_completion_cb("sub_1_aaaaaa", {"ok": True, "text": "the file has 40 lines", "name": "Noah"})
turn("anything new?", [])
last = json.loads(open(argv_log).read().splitlines()[-1])["stdin"]
check("a report that was waiting reaches the brain with the next message",
      "the file has 40 lines" in last and last.rstrip().endswith("anything new?"))

# The window's idle check-in carries no message of its own.
from hearth.headless import NOTIFY_FLUSH                             # noqa: E402
runs = len(open(argv_log).read().splitlines())
ev = turn(NOTIFY_FLUSH, [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "I'm JARVIS."}])
check("an idle check-in with nothing to report doesn't start the agent",
      len(open(argv_log).read().splitlines()) == runs and ev[-1][0] == "done")
sa._default_completion_cb("sub_2_bbbbbb", {"ok": True, "text": "backup finished", "name": "Ivy"})
turn(NOTIFY_FLUSH, [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "I'm JARVIS."}])
last = json.loads(open(argv_log).read().splitlines()[-1])["stdin"]
check("one with a report gives the brain the report, never the marker",
      "backup finished" in last and NOTIFY_FLUSH not in last)

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


# Claude Code's fast mode had no switch here; now it does, and Claude Code says whether it applied.
from hearth import web as _w
_cfg = _w._load_settings(); _cfg["fast_claude-code"] = True; _w._save_settings(_cfg)
offered = [m.get("fast") for m in cb.agent_models("claude-code")["models"]]
before = len(open(argv_log).read().splitlines())
told = []
asyncio.run(cb.run_turn("hello fast", emit=lambda t, **f: told.append((t, f)), history=[], permission_check=lambda n, a: "allow"))
argv = json.loads(open(argv_log).read().splitlines()[before])["argv"]
check(f"with Fast on, Claude Code is asked for fast mode {argv[argv.index('--settings') + 1] if '--settings' in argv else argv}",
      all(offered) and "--settings" in argv and json.loads(argv[argv.index("--settings") + 1]).get("fastMode") is True)
check("when it says fast mode wasn't on, that is said once and the model isn't offered Fast again",
      any(t == "heads_up" for t, f in told) and not cb.agent_models("claude-code")["models"][0].get("fast"))
_cfg = _w._load_settings(); _cfg.pop("fast_claude-code", None); _w._save_settings(_cfg)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all claude brain checks passed")
