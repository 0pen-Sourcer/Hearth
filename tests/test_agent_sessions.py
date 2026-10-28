"""The coding agents' own sessions, read and carried on: Claude Code, Codex,
Gemini CLI and Pi from their files, OpenCode from its own answers. They are
listed newest first by project, a transcript reads as a chat (the person's
words, the agent's replies, its steps said plainly, none of the program's own
notes), and a message carries a session on: the agent resumes that session in
its project folder, and a step it asks about comes back as a question.

The agent programs here are small stand-ins that answer the way each one does.

    .\\.venv\\Scripts\\python.exe tests\\test_agent_sessions.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-agent-sessions-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import agent_sessions as S, claude_brain, relay_bridge as RB  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def lines(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def touch(path, ago):
    t = time.time() - ago
    os.utime(path, (t, t))


# Three project folders.
PA, PB, PC = (os.path.join(HOME, "code", n) for n in ("shop", "blog", "notes"))
for p in (PA, PB, PC):
    os.makedirs(p)

# Claude Code: a summary line, the program's own notes, a tool call, a sub-agent's line.
CL = "11111111-1111-1111-1111-111111111111"
cl_path = os.path.join(HOME, ".claude", "projects", PA.replace(os.sep, "-").replace(":", "-"), CL + ".jsonl")
write(cl_path, lines(
    {"type": "summary", "summary": "Fix the checkout total"},
    {"type": "user", "cwd": PA, "sessionId": CL, "timestamp": "2026-10-27T09:00:00Z",
     "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
    {"type": "user", "cwd": PA, "sessionId": CL, "timestamp": "2026-10-27T09:00:05Z",
     "message": {"role": "user", "content": "the checkout total is off by one cent"}},
    {"type": "assistant", "cwd": PA, "timestamp": "2026-10-27T09:00:09Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "Looking at the rounding."},
                                                   {"type": "tool_use", "id": "t1", "name": "Bash",
                                                    "input": {"command": "npm test"}}]}},
    {"type": "user", "cwd": PA, "timestamp": "2026-10-27T09:00:20Z",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1 failing"}]}},
    {"type": "assistant", "cwd": PA, "isSidechain": True, "timestamp": "2026-10-27T09:00:21Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "a sub-agent's aside"}]}},
    {"type": "assistant", "cwd": PA, "timestamp": "2026-10-27T09:00:30Z", "model": "claude-opus-5-5",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed: it rounds once, at the end."}]}}))
touch(cl_path, 300)

# Codex: its session line, the environment it adds as the person's turn, events and a shell call.
CX = "22222222-2222-2222-2222-222222222222"
cx_path = os.path.join(HOME, ".codex", "sessions", "2026", "10", "27", f"rollout-2026-10-27T08-00-00-{CX}.jsonl")
write(cx_path, lines(
    {"timestamp": "2026-10-27T08:00:00Z", "type": "session_meta", "payload": {"id": CX, "cwd": PB}},
    {"timestamp": "2026-10-27T08:00:00Z", "type": "response_item",
     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
    {"timestamp": "2026-10-27T08:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "add an RSS feed"}},
    {"timestamp": "2026-10-27T08:00:03Z", "type": "response_item",
     "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["ls", "posts"]})}},
    {"timestamp": "2026-10-27T08:00:09Z", "type": "event_msg", "payload": {"type": "agent_message", "message": "Added feed.xml."}}))
touch(cx_path, 600)

# Gemini CLI: a hash of the folder, which another agent's session names.
GM = "33333333-3333-3333-3333-333333333333"
gm_path = os.path.join(HOME, ".gemini", "tmp", hashlib.sha256(PA.encode()).hexdigest(), "chats", "session-2026-10-26-333.json")
write(gm_path, json.dumps({"sessionId": GM, "lastUpdated": "2026-10-26T10:00:00Z", "messages": [
    {"type": "user", "timestamp": "2026-10-26T10:00:00Z", "content": "explain the cart module"},
    {"type": "gemini", "timestamp": "2026-10-26T10:00:05Z", "content": "It keeps the items and totals.",
     "toolCalls": [{"name": "read_file", "args": {"absolute_path": os.path.join(PA, "cart.js")}}]}]}))
touch(gm_path, 3600)

# Pi.
PI = "44444444-4444-4444-4444-444444444444"
pi_path = os.path.join(HOME, ".pi", "agent", "sessions", "--code-notes--", f"2026-10-25T10-00-00_{PI}.jsonl")
write(pi_path, lines(
    {"type": "session", "id": PI, "timestamp": "2026-10-25T10:00:00Z", "cwd": PC},
    {"type": "message", "timestamp": "2026-10-25T10:00:01Z",
     "message": {"role": "user", "content": [{"type": "text", "text": "tidy the notes index"}]}},
    {"type": "message", "timestamp": "2026-10-25T10:00:04Z",
     "message": {"role": "assistant", "content": [{"type": "toolCall", "name": "bash", "arguments": {"command": "ls"}},
                                                  {"type": "text", "text": "Sorted by date now."}]}}))
touch(pi_path, 7200)

# The stand-in agent programs. Each writes down how it was started.
BIN = os.path.join(HOME, "bin")
RAN = os.path.join(HOME, "ran.jsonl")
write(os.path.join(BIN, "fake_claude.py"), f'''
import json, os, sys
text = sys.stdin.read()
open({RAN!r}, "a").write(json.dumps({{"who": "claude", "argv": sys.argv[1:], "cwd": os.getcwd(), "text": text}}) + "\\n")
out = lambda o: print(json.dumps(o), flush=True)
out({{"type": "system", "subtype": "init", "session_id": sys.argv[sys.argv.index("--resume") + 1]}})
out({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": "a", "name": "Read", "input": {{"file_path": "cart.js"}}}}]}}}})
for piece in ("Done: ", "the total rounds once."):
    out({{"type": "stream_event", "event": {{"delta": {{"type": "text_delta", "text": piece}}}}}})
out({{"type": "result", "result": "Done: the total rounds once.", "is_error": False}})
''')
write(os.path.join(BIN, "fake_codex.py"), f'''
import json, os, sys
text = sys.stdin.read()
open({RAN!r}, "a").write(json.dumps({{"who": "codex", "argv": sys.argv[1:], "cwd": os.getcwd(), "text": text}}) + "\\n")
out = lambda o: print(json.dumps(o), flush=True)
out({{"type": "thread.started", "thread_id": sys.argv[3]}})
out({{"type": "item.started", "item": {{"id": "1", "type": "command_execution", "command": "cat feed.xml"}}}})
out({{"type": "item.completed", "item": {{"id": "2", "type": "agent_message", "text": "The feed has 12 posts."}}}})
out({{"type": "turn.completed"}})
''')
write(os.path.join(BIN, "fake_opencode.py"), f'''
import json, os, sys
OC = {{"id": "ses_abc123", "title": "Write the README", "directory": {PB!r}, "time": {{"created": 1761400000000, "updated": 1761400500000}}}}
if sys.argv[1:3] == ["session", "list"]:
    print(json.dumps([OC])); sys.exit(0)
if sys.argv[1:2] == ["export"]:
    print(json.dumps({{"info": OC, "messages": [
        {{"info": {{"role": "user", "time": {{"created": 1761400000000}}}}, "parts": [{{"type": "text", "text": "write the README"}}]}},
        {{"info": {{"role": "assistant", "time": {{"created": 1761400100000}}}}, "parts": [
            {{"type": "tool", "tool": "write", "state": {{"input": {{"filePath": "README.md"}}}}}},
            {{"type": "text", "text": "Wrote it."}}]}}]}})); sys.exit(0)
# acp
log = open({RAN!r}, "a")
send = lambda o: (sys.stdout.write(json.dumps(dict(o, jsonrpc="2.0")) + "\\n"), sys.stdout.flush())
for line in sys.stdin:
    m = json.loads(line)
    meth = m.get("method")
    if meth == "initialize":
        send({{"id": m["id"], "result": {{"protocolVersion": 1, "agentCapabilities": {{"loadSession": True}}}}}})
    elif meth == "session/load":
        log.write(json.dumps({{"who": "opencode", "load": m["params"]["sessionId"], "cwd": m["params"]["cwd"], "pcwd": os.getcwd()}}) + "\\n"); log.flush()
        send({{"method": "session/update", "params": {{"update": {{"sessionUpdate": "agent_message_chunk", "content": {{"text": "an old reply"}}}}}}}})
        send({{"id": m["id"], "result": {{}}}})
    elif meth == "session/prompt":
        prompt_id = m["id"]
        log.write(json.dumps({{"who": "opencode", "text": m["params"]["prompt"][0]["text"]}}) + "\\n"); log.flush()
        send({{"id": 900, "method": "session/request_permission", "params": {{
            "toolCall": {{"kind": "execute", "title": "rm -rf build", "rawInput": {{"command": "rm -rf build"}}}},
            "options": [{{"optionId": "y", "kind": "allow_once"}}, {{"optionId": "n", "kind": "reject_once"}}]}}}})
    elif m.get("id") == 900:
        ok = m["result"]["outcome"].get("optionId") == "y"
        send({{"method": "session/update", "params": {{"update": {{"sessionUpdate": "agent_message_chunk",
              "content": {{"text": "Cleaned the build folder." if ok else "Left the build folder."}}}}}}}})
        send({{"id": prompt_id, "result": {{"stopReason": "end_turn"}}}})
''')
FAKE = {"claude-code": "fake_claude.py", "codex": "fake_codex.py", "opencode": "fake_opencode.py"}
claude_brain.exe_for = lambda p: [sys.executable, os.path.join(BIN, FAKE[p])] if p in FAKE else None

# ---------------------------------------------------------------- the listing
rows = S.listing()
by = {r["agent"]: r for r in rows}
check(f"every agent's sessions are listed ({sorted(by)})", sorted(by) == ["claude-code", "codex", "gemini-cli", "opencode", "pi"])
check("newest first", [r["agent"] for r in rows] == ["claude-code", "codex", "gemini-cli", "pi", "opencode"])
check(f"each with its project folder ({[r['project'] for r in rows]})",
      by["claude-code"]["cwd"] == PA and by["codex"]["cwd"] == PB and by["pi"]["cwd"] == PC
      and by["opencode"]["cwd"] == PB and by["claude-code"]["project"] == "shop")
check("Gemini's project is found from the folder its hash names", by["gemini-cli"]["cwd"] == PA)
check(f"titles are what the session was about ({[r['title'] for r in rows]})",
      by["claude-code"]["title"] == "Fix the checkout total" and by["codex"]["title"] == "add an RSS feed"
      and by["pi"]["title"] == "tidy the notes index" and by["opencode"]["title"] == "Write the README")
check(f"the model each session last ran on ({by['claude-code'].get('model')})", by["claude-code"].get("model") == "claude-opus-5-5")
check("sessions not in use aren't marked live", all(not r.get("live") for r in rows))
check("each is named by its agent and has a phone chat id", by["codex"]["label"] == "Codex"
      and S.parse_chat(by["codex"]["chat"]) == ("codex", CX))
check("the bridge answers the phone's request for them",
      [r["id"] for r in RB.answer({"what": "agent_sessions"})["sessions"]] == [r["id"] for r in rows])

# ---------------------------------------------------------------- transcripts
t = RB.answer({"what": "open", "chat": by["claude-code"]["chat"]})
said = [(m["role"], m["text"]) for m in t["messages"]]
check(f"a Claude Code session reads as a chat, without the program's notes or sub-agents ({said})",
      said == [("user", "the checkout total is off by one cent"),
               ("assistant", "Looking at the rounding.\n\nFixed: it rounds once, at the end.")])
check(f"its steps are said plainly ({t['messages'][1]['steps']})", t["messages"][1]["steps"][0]["w"] == "Ran npm test")
t = S.transcript(by["codex"]["chat"])
check(f"Codex: the person's words, not the environment it adds ({[m['text'] for m in t['messages']]})",
      [m["text"] for m in t["messages"]] == ["add an RSS feed", "Added feed.xml."]
      and t["messages"][1]["steps"][0]["w"].startswith("Ran ls posts"))
t = S.transcript(by["gemini-cli"]["chat"])
check(f"Gemini CLI ({[m['text'] for m in t['messages']]})", [m["text"] for m in t["messages"]]
      == ["explain the cart module", "It keeps the items and totals."] and "cart.js" in t["messages"][1]["steps"][0]["w"])
t = S.transcript(by["pi"]["chat"])
check("Pi", [m["text"] for m in t["messages"]] == ["tidy the notes index", "Sorted by date now."])
t = S.transcript(by["opencode"]["chat"])
check(f"OpenCode, from its own export ({[m['text'] for m in t['messages']]})",
      [m["text"] for m in t["messages"]] == ["write the README", "Wrote it."] and "README.md" in t["messages"][1]["steps"][0]["w"])
check("a session that isn't there says so", S.transcript(S.chat_id("codex", "nope")).get("error"))
check("part of an id isn't taken for a whole one", S.transcript(S.chat_id("codex", CX[:8])).get("error")
      and S.transcript(S.chat_id("claude-code", "1111")).get("error"))
check("an id the agent would read as a flag is refused", S.parse_chat("as_claude-code_--dangerously-skip-permissions") is None
      and S.parse_chat("as_codex_-p") is None)


# ---------------------------------------------------------------- carrying on
def say(chat, text, allow=True):
    got, asked = [], []

    def emit(kind, **f):
        got.append((kind, f))

    def ask(q):
        asked.append(q)
        return allow
    try:
        rc = asyncio.run(asyncio.wait_for(S.say(chat, text, emit, ask), 60))
    except asyncio.TimeoutError:
        rc = "stalled"
    return rc, got, asked


def ran(who):
    with open(RAN, encoding="utf-8") as f:
        return [json.loads(x) for x in f if json.loads(x).get("who") == who]


rc, got, _ = say(by["claude-code"]["chat"], "does it handle refunds?")
c = ran("claude")[-1]
check(f"Claude Code resumes that session, in its project folder ({c['argv'][:3]}, {c['cwd']})",
      c["argv"][:3] == ["-p", "--resume", CL] and os.path.samefile(c["cwd"], PA) and c["text"] == "does it handle refunds?")
check(f"with Before acting on Ask, the permission mode is said outright, so the person's own default can't skip it ({c['argv']})",
      c["argv"][c["argv"].index("--permission-mode") + 1] == "default" if "--permission-mode" in c["argv"] else False)
check(f"its steps and words come back as they happen, then the reply ({[k for k, _ in got]})",
      ("step", {"words": "Read cart.js", "tool": "read_file"}) in got and any(k == "live" for k, _ in got)
      and got[-1] == ("text", {"text": "Done: the total rounds once."}) and rc == 0)

rc, got, _ = say(by["codex"]["chat"], "how many posts?")
c = ran("codex")[-1]
check(f"Codex resumes that session in its folder ({c['argv'][:3]})", c["argv"][:3] == ["exec", "resume", CX]
      and os.path.samefile(c["cwd"], PB) and c["text"] == "how many posts?" and "--json" in c["argv"])
check("and answers", got[-1] == ("text", {"text": "The feed has 12 posts."}) and any(k == "step" for k, _ in got))

rc, got, asked = say(by["opencode"]["chat"], "clean the build")
c = [x for x in ran("opencode") if "load" in x][-1]
check(f"OpenCode reopens the session over ACP, in its folder ({c})", c["load"] == "ses_abc123" and c["cwd"] == PB
      and os.path.samefile(c["pcwd"], PB))
check(f"a step it asks about comes back as a question ({asked})", asked and "rm -rf build" in asked[0])
check(f"the replayed old messages aren't shown again, and the answer is ({got[-1]})",
      got[-1] == ("text", {"text": "Cleaned the build folder."}) and not any("an old reply" in str(f) for _, f in got))
rc, got, asked = say(by["opencode"]["chat"], "clean the build", allow=False)
check("told no, it doesn't", got[-1] == ("text", {"text": "Left the build folder."}))

# A session open in Claude Code on the PC right now: Claude writes ~/.claude/sessions/<pid>.json for it.
write(os.path.join(HOME, ".claude", "sessions", f"{os.getpid()}.json"), json.dumps({"pid": os.getpid(), "sessionId": CL}))
before = len(ran("claude"))
rc, got, _ = say(by["claude-code"]["chat"], "and refunds?")
check(f"a session open in its program right now isn't resumed a second time ({got[-1][1].get('message', '')[:60]})",
      rc == 3 and len(ran("claude")) == before and "open in Claude Code" in got[-1][1]["message"])
check("and the list says it's open", next(r for r in S.listing() if r["agent"] == "claude-code")["live"] == "open")
os.remove(os.path.join(HOME, ".claude", "sessions", f"{os.getpid()}.json"))
touch(cx_path, 10)
rc, got, _ = say(by["codex"]["chat"], "still there?")
check("one written seconds ago may still be working: it waits", rc == 3 and "minute and a half" in got[-1][1]["message"])
touch(cx_path, 600)

# A long session (pictures make them hundreds of MB): read from its end, title and folder from its start.
BIG = "55555555-5555-5555-5555-555555555555"
big_path = os.path.join(os.path.dirname(cl_path), BIG + ".jsonl")
with open(big_path, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "user", "cwd": PA, "timestamp": "2026-10-20T09:00:00Z",
                        "message": {"role": "user", "content": "start of a very long session"}}) + "\n")
    pad = "x" * 200_000
    for i in range(30):
        f.write(json.dumps({"type": "assistant", "cwd": PA, "timestamp": "2026-10-20T09:01:00Z",
                            "message": {"role": "assistant", "content": [{"type": "text", "text": f"part {i} {pad}"}]}}) + "\n")
    f.write(json.dumps({"type": "user", "cwd": PA, "timestamp": "2026-10-20T10:00:00Z",
                        "message": {"role": "user", "content": "the last thing I asked"}}) + "\n")
touch(big_path, 900)
t0 = time.time()
t = S.transcript(S.chat_id("claude-code", BIG))
check(f"a {os.path.getsize(big_path) // 1_000_000} MB session opens from its end ({time.time() - t0:.2f} s), "
      f"with its title and folder from its start",
      t.get("messages") and t["messages"][-1]["text"] == "the last thing I asked" and t["title"] == "start of a very long session"
      and t["cwd"] == PA)

rc, got, _ = say(by["gemini-cli"]["chat"], "hi")
check(f"an agent not installed says so ({got})", rc == 2 and got and "isn't installed" in got[0][1]["message"])
os.rename(PC, PC + "-moved")
rc, got, _ = say(by["pi"]["chat"], "hi")
check("a session whose folder is gone says so", rc == 2 and "folder" in got[0][1]["message"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent session checks passed")
