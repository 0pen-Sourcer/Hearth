"""Codex as the assistant's brain.

A stand-in for `codex exec --json` speaks Codex's event format, so this checks
what Hearth does with it without a real install or a bill: replies and tool
work show in the chat as Hearth's own (a shell command as a command, an MCP
call under its Hearth tool name), the next turn of the same chat resumes the
same thread, Codex's shell gets the sandbox that matches the approval switch,
a hand-made Hearth entry in the user's Codex config is switched off for the
run, and a failed turn says why. Then Hearth's MCP server, started for Codex,
asks before a risky tool and does nothing on a no.

    .\\.venv\\Scripts\\python.exe tests\\test_codex_brain.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


work = tempfile.mkdtemp(prefix="hearth-cx-")
os.environ["JARVIS_WORKSPACE"] = work
codex_home = os.path.join(work, "codex-home")
os.makedirs(codex_home)
with open(os.path.join(codex_home, "config.toml"), "w", encoding="utf-8") as f:
    f.write('[mcp_servers.hearth]\ncommand = "python"\nargs = ["-m", "hearth.mcp_server"]\n'
            '[mcp_servers.hearth.env]\nJARVIS_AUTO_APPROVE = "1"\n'
            '[mcp_servers.docs]\nurl = "https://example.com/mcp"\n')
os.environ["CODEX_HOME"] = codex_home

from hearth import claude_brain as cb                                # noqa: E402
from hearth import codex_brain as cx                                 # noqa: E402
from hearth import tools                                             # noqa: E402

cb._HOME = work
cb._SESSIONS = os.path.join(work, "claude_sessions.json")
cb.set_bridge_url("http://127.0.0.1:9")
cb.current = lambda: "codex"

# The models and effort levels are Codex's own, with what Codex says each level is for.
with open(os.path.join(codex_home, "models_cache.json"), "w", encoding="utf-8") as _f:
    json.dump({"models": [{"slug": "gpt-test", "display_name": "GPT-Test", "visibility": "list", "context_window": 272000,
                           "supported_reasoning_levels": [{"effort": "low", "description": "Fast responses with lighter reasoning"},
                                                          {"effort": "ultra", "description": "Maximum reasoning"}],
                           "service_tiers": [{"id": "priority", "name": "Fast", "description": "2x speed, increased usage"}]},
                          {"slug": "hidden", "visibility": "hide"}]}, _f)
_listed = cx.models()
check("Codex's models are its own list, with its effort levels and what each is for",
      [m["id"] for m in _listed] == ["", "gpt-test"] and _listed[1]["efforts"] == ["low", "ultra"]
      and _listed[1]["effort_about"]["low"] == "Fast responses with lighter reasoning" and _listed[1]["ctx"] == 272000)
check("and which have a Fast tier, in Codex's words, with the tier to ask for",
      _listed[1]["fast"] == "2x speed, increased usage" and cx.fast_tier("gpt-test") == "priority")
argv_log = os.path.join(work, "argv.jsonl")
fake = os.path.join(work, "fake_codex.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write(f"""import json, sys
open({argv_log!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read()}}) + "\\n")
out = lambda o: print(json.dumps(o), flush=True)
if "FAIL" in open({argv_log!r}).read().splitlines()[-1]:
    out({{"type": "thread.started", "thread_id": "th-9"}})
    out({{"type": "error", "message": "You've hit your usage limit."}})
    out({{"type": "turn.failed", "error": {{"message": "You've hit your usage limit."}}}})
    sys.exit(1)
out({{"type": "thread.started", "thread_id": "th-1"}})
out({{"type": "turn.started"}})
out({{"type": "item.completed", "item": {{"id": "i0", "type": "reasoning", "text": "Checking the date."}}}})
out({{"type": "item.started", "item": {{"id": "i1", "type": "command_execution", "command": "Get-Date", "aggregated_output": "", "status": "in_progress"}}}})
out({{"type": "item.completed", "item": {{"id": "i1", "type": "command_execution", "command": "Get-Date", "aggregated_output": "Wednesday", "exit_code": 0, "status": "completed"}}}})
out({{"type": "item.started", "item": {{"id": "i2", "type": "mcp_tool_call", "server": "hearth_brain", "tool": "open_app", "arguments": {{"name": "Spotify"}}, "status": "in_progress"}}}})
out({{"type": "item.completed", "item": {{"id": "i2", "type": "mcp_tool_call", "server": "hearth_brain", "tool": "open_app", "arguments": {{"name": "Spotify"}}, "result": {{"content": [{{"type": "text", "text": "Opened Spotify"}}]}}, "status": "completed"}}}})
out({{"type": "item.completed", "item": {{"id": "i3", "type": "file_change", "changes": [{{"path": "notes.txt", "kind": "update"}}], "status": "completed"}}}})
out({{"type": "item.completed", "item": {{"id": "i4", "type": "agent_message", "text": "I'm JARVIS."}}}})
out({{"type": "item.completed", "item": {{"id": "i5", "type": "agent_message", "text": "Spotify is open."}}}})
out({{"type": "turn.completed", "usage": {{"input_tokens": 1200, "cached_input_tokens": 0, "output_tokens": 40}}}})
""")
_real_exe_for = cb.exe_for
cb.exe_for = lambda p: [sys.executable, fake] if p == "codex" else _real_exe_for(p)


def turn(prompt, history):
    events = []
    asyncio.run(cb.run_turn(prompt, emit=lambda t, **f: events.append((t, f)), history=history,
                            permission_check=lambda n, a: "allow"))
    return events


# The stand-in above speaks `codex exec`; the app server comes further down.
os.environ["HEARTH_CODEX_EXEC"] = "1"
tools.approval_mode = lambda: "ask"
ev = turn("hello", [])
chunks = "".join(f["content"] for t, f in ev if t == "assistant_chunk")
calls = [(f["name"], f["args"]) for t, f in ev if t == "tool_call"]
results = [(f["name"], f["content"]) for t, f in ev if t == "tool_result"]
check("Codex's replies reach the chat, one paragraph each", chunks == "I'm JARVIS.\n\nSpotify is open.")
check("its reasoning shows as thinking", any(t == "thinking_chunk" and "date" in f["content"] for t, f in ev))
check("a shell command shows as run_command", ("run_command", {"command": "Get-Date"}) in calls)
check("a Hearth tool shows under its own name", ("open_app", {"name": "Spotify"}) in calls)
check("a file change shows as edit_file", ("edit_file", {"path": "notes.txt"}) in calls)
check("each tool gets its result",
      ("run_command", "Wednesday") in results and ("open_app", "Opened Spotify") in results)
check("the full reply is reported for the bridges",
      any(t == "assistant" and f["content"] == "I'm JARVIS.\n\nSpotify is open." for t, f in ev))
check("the turn ends with done, naming Codex", ev[-1][0] == "done" and ev[-1][1]["stats"]["brain"] == "Codex")

first = json.loads(open(argv_log).read().splitlines()[0])
argv = first["argv"]
joined = " ".join(argv)
check("it runs codex exec with JSON events and the prompt on stdin",
      argv[0:2] == ["exec", "--json"] and argv[-1] == "-" and first["stdin"] == "hello")
check("Codex never stops to ask on its own", 'approval_policy="never"' in argv)
check("Hearth's tools are attached and run without Codex asking",
      "mcp_servers.hearth_brain.default_tools_approval_mode=\"approve\"" in argv
      and "HEARTH_BRAIN_GATE = \"1\"" in joined)
check("on Ask, Codex's own edits are refused", 'sandbox_mode="read-only"' in argv)
# Its shell ran commands on Ask without anyone being asked; read-only only
# stopped writes. On Ask it has no shell: commands go through Hearth's, which asks.
check("on Ask, Codex has no shell of its own", "features.shell_tool=false" in argv and "features.unified_exec=false" in argv)
check("the user's own Hearth entry is off for the run, other servers untouched",
      "mcp_servers.hearth.enabled=false" in argv and "mcp_servers.docs.enabled=false" not in argv)
check("the persona goes in as developer instructions", any(a.startswith("developer_instructions=") for a in argv))

tools.approval_mode = lambda: "auto"
hist = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "I'm JARVIS."}]
turn("open spotify", hist)
second = json.loads(open(argv_log).read().splitlines()[1])["argv"]
check("the next turn of the same chat resumes the same Codex thread", second[0:3] == ["exec", "resume", "th-1"])
check("on Auto, Codex works in the workspace with the network on, and its own shell",
      'sandbox_mode="workspace-write"' in second and "sandbox_workspace_write.network_access=true" in second
      and "features.shell_tool=false" not in second)
from hearth import codex_brain as _cx  # noqa: E402
tools.approval_mode = lambda: "edits"
edits = _cx._sandbox()
check("on Auto-edit its edits stay in the workspace and commands still need a yes",
      'sandbox_mode="workspace-write"' in edits and "features.shell_tool=false" in edits)
tools.approval_mode = lambda: "auto"

ev = turn("FAIL please", [])
check("a failed turn says why", any(t == "error" and "usage limit" in f["message"] for t, f in ev))

cb.exe_for = lambda p: None
ev = turn("hi", [])
check("without Codex installed it says how to get it",
      any(t == "error" and "isn't installed" in f["message"] for t, f in ev))

# The app server: text streams as it is written, Codex asks Hearth before each
# command and file change, and something said part-way reaches the running turn.
os.environ.pop("HEARTH_CODEX_EXEC", None)
app_log = os.path.join(work, "app.jsonl")
fake_app = os.path.join(work, "fake_app.py")
with open(fake_app, "w", encoding="utf-8") as f:
    f.write(f"""import json, sys
log = open({app_log!r}, "a")
out = lambda o: print(json.dumps(o), flush=True)
def read():
    line = sys.stdin.readline()
    if not line.strip():
        return None
    m = json.loads(line)
    log.write(json.dumps(m) + "\\n"); log.flush()
    return m
log.write(json.dumps({{"argv": sys.argv[1:]}}) + "\\n"); log.flush()
while True:
    m = read()
    if m is None:
        break
    meth = m.get("method")
    if meth == "initialize":
        out({{"id": m["id"], "result": {{}}}})
    elif meth in ("thread/start", "thread/resume"):
        out({{"id": m["id"], "result": {{"thread": {{"id": m["params"].get("threadId") or "th-app"}}}}}})
    elif meth == "turn/start":
        text = m["params"]["input"][0]["text"]
        out({{"id": m["id"], "result": {{"turn": {{"id": "tu-1", "status": "inProgress"}}}}}})
        T = {{"threadId": "th-app", "turnId": "tu-1"}}
        if "steer me" in text:
            s = read()
            out({{"id": s["id"], "result": {{}}}})
            heard = s["params"]["input"][0]["text"]
            out({{"method": "item/completed", "params": {{**T, "item": {{"type": "agentMessage", "id": "m9", "text": "Heard: " + heard}}}}}})
            out({{"method": "turn/completed", "params": {{"threadId": "th-app", "turn": {{"id": "tu-1", "status": "completed"}}}}}})
            continue
        out({{"method": "item/started", "params": {{**T, "item": {{"type": "agentMessage", "id": "m1", "text": ""}}}}}})
        for d in ("Hel", "lo."):
            out({{"method": "item/agentMessage/delta", "params": {{**T, "itemId": "m1", "delta": d}}}})
        out({{"method": "item/completed", "params": {{**T, "item": {{"type": "agentMessage", "id": "m1", "text": "Hello."}}}}}})
        out({{"method": "item/started", "params": {{**T, "item": {{"type": "commandExecution", "id": "c0", "command": "dir", "commandActions": [], "status": "inProgress"}}}}}})
        cmd = {{"type": "commandExecution", "id": "c1", "command": "powershell.exe -Command 'echo hi'",
               "commandActions": [{{"type": "unknown", "command": "echo hi"}}], "status": "inProgress"}}
        out({{"method": "item/started", "params": {{**T, "item": cmd}}}})
        out({{"id": 900, "method": "item/commandExecution/requestApproval", "params": {{**T, "itemId": "c1", "command": cmd["command"], "commandActions": cmd["commandActions"]}}}})
        first = read()
        out({{"id": 901, "method": "item/commandExecution/requestApproval", "params": {{**T, "itemId": "c1", "command": cmd["command"], "commandActions": cmd["commandActions"], "reason": "outside the sandbox"}}}})
        second = read()
        ok = first["result"]["decision"] == "accept" and second["result"]["decision"] == "accept"
        out({{"method": "item/completed", "params": {{**T, "item": {{**cmd, "status": "completed" if ok else "declined", "aggregatedOutput": "hi" if ok else None, "exitCode": 0 if ok else None}}}}}})
        fc = {{"type": "fileChange", "id": "f1", "status": "inProgress",
              "changes": [{{"path": "notes.txt", "kind": {{"type": "update"}}, "diff": "@@ -1 +1 @@\\n-old\\n+new\\n"}}]}}
        out({{"method": "item/started", "params": {{**T, "item": fc}}}})
        out({{"id": 902, "method": "item/fileChange/requestApproval", "params": {{**T, "itemId": "f1"}}}})
        third = read()
        out({{"method": "item/completed", "params": {{**T, "item": {{**fc, "status": "completed" if third["result"]["decision"] == "accept" else "declined"}}}}}})
        out({{"method": "thread/tokenUsage/updated", "params": {{**T, "tokenUsage": {{"last": {{"inputTokens": 900, "outputTokens": 12}}}}}}}})
        out({{"method": "turn/completed", "params": {{"threadId": "th-app", "turn": {{"id": "tu-1", "status": "completed"}}}}}})
""")
cb.exe_for = lambda p: [sys.executable, fake_app] if p == "codex" else _real_exe_for(p)
asked = []


def app_turn(prompt, history=None, steer=None, edits_ok=True):
    events = []
    said = list(steer or [])

    def check_(name, args):
        asked.append((name, args))
        return "allow" if name == "run_command" or edits_ok else "deny"
    asyncio.run(cb.run_turn(prompt, emit=lambda t, **f: events.append((t, f)), history=history or [],
                            permission_check=check_, drain_steer=lambda: [said.pop(0)] if said else []))
    return events


tools.approval_mode = lambda: "ask"
asked.clear()
ev = app_turn("hello there", edits_ok=False)
chunks = [f["content"] for t, f in ev if t == "assistant_chunk"]
check(f"Codex's reply streams as it is written {chunks}", chunks[:2] == ["Hel", "lo."])
check(f"Hearth is asked once for a command Codex asks about twice {asked}",
      [a for a in asked if a[0] == "run_command"] == [("run_command", {"command": "echo hi"})])
check("a file change is asked about with its diff",
      ("edit_file", {"path": "notes.txt", "diff": "@@ -1 +1 @@\n-old\n+new\n"}) in asked)
check("and shows as a diff card", any(t == "tool_call" and f["name"] == "edit_file" and "+new" in f["args"].get("diff", "")
                                      for t, f in ev))
check("a change the user turned down is not made, and says so",
      any(t == "tool_result" and f["name"] == "edit_file" and "not changed" in f["content"] for t, f in ev))
check("the command's output comes back", any(t == "tool_result" and f["name"] == "run_command" and f["content"] == "hi" for t, f in ev))
check("a command Codex started and dropped leaves no card behind",
      not any(f.get("id") == "c0" for t, f in ev if t in ("tool_call", "tool_result")))
app = [json.loads(l) for l in open(app_log, encoding="utf-8")]
start = next(m for m in app if m.get("method") == "thread/start")["params"]
check(f"Codex runs as an app server, asking Hearth before it does anything {app[0]}",
      app[0]["argv"][0] == "app-server" and start["approvalPolicy"] == "untrusted" and start.get("developerInstructions"))
check("the turn ends with done and its token counts",
      ev[-1][0] == "done" and ev[-1][1]["stats"]["tokens_in"] == 900)
ev = app_turn("steer me please", steer=["use tabs"])
check("something said part-way reaches the running turn",
      any(t == "steered" for t, f in ev) and any(t == "assistant" and "Heard: use tabs" in f["content"] for t, f in ev)
      and any(m.get("method") == "turn/steer" and m["params"]["expectedTurnId"] == "tu-1" for m in
              (json.loads(l) for l in open(app_log, encoding="utf-8"))))
cb.fast_on = lambda p: True
_cx.fast_tier = lambda m: "priority"
app_turn("hello again")
last_turn = [json.loads(l) for l in open(app_log, encoding="utf-8") if '"turn/start"' in l][-1]
check("Fast asks Codex for its priority tier", last_turn["params"].get("serviceTier") == "priority")
dead = os.path.join(work, "dead_app.py")
open(dead, "w").write("import sys; sys.exit(0)")
cb.exe_for = lambda p: [sys.executable, dead] if p == "codex" else _real_exe_for(p)
import time as _time                                                  # noqa: E402
t0 = _time.time()
ev = app_turn("hi")
check(f"a Codex without the app server falls back at once ({_time.time() - t0:.1f}s)", _time.time() - t0 < 10)
cb.exe_for = lambda p: None

# Hearth's MCP server as Codex starts it: risky tools ask Hearth first.
answers = []


class _Ask(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if self.path == "/api/brain/tool":
            passed.append((body["tool"], body["input"]))
            out = json.dumps({"result": '{"ok": true, "status": "launched"}'}).encode()
        else:
            answers.append(body["tool"])
            out = json.dumps({"behavior": VERDICT[0], "message": "The user declined this."}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


VERDICT = ["deny"]
passed: list = []
srv =ThreadingHTTPServer(("127.0.0.1", 0), _Ask)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update(HEARTH_BRAIN_URL=f"http://127.0.0.1:{srv.server_address[1]}",
                  HEARTH_BRAIN_TOKEN="t", HEARTH_BRAIN_GATE="1")
from hearth import mcp_server                                        # noqa: E402

target = os.path.join(work, "gate.txt")
out = mcp_server._run("write_file", {"path": target, "content": "x"})
check("a risky tool asks Hearth first", answers == ["mcp__hearth__write_file"])
check("and on a no it does nothing", "Not done" in str(out) and not os.path.exists(target))
VERDICT[0] = "allow"
mcp_server._run("write_file", {"path": target, "content": "x"})
check("on a yes it runs", os.path.exists(target))
answers.clear()
mcp_server._run("read_file", {"path": target})
check("a safe tool doesn't ask", answers == [])
offered = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
check("Codex gets no approve tool to call", "approve" not in offered)
check("as Hearth's brain it can send helpers and talk to them",
      {"spawn_subagent", "tell_subagent", "get_subagent_result"} <= offered)
out = mcp_server._run("spawn_subagent", {"persona": "researcher", "prompt": "look it up", "name": "Maya"})
check("a helper call is run by the Hearth that started the server, where helpers live",
      passed == [("spawn_subagent", {"persona": "researcher", "prompt": "look it up", "name": "Maya"})]
      and "launched" in out)
import subprocess                                                    # noqa: E402
listed = lambda extra: subprocess.run(
    [sys.executable, "-X", "utf8", "-c",
     "from hearth import mcp_server as m; print(sorted(t.name for t in m.mcp._tool_manager.list_tools()))"],
    capture_output=True, text=True, cwd=ROOT, env={**os.environ, **extra}).stdout
check("a helper gets no helpers of its own", "spawn_subagent" not in listed({"HEARTH_HELPER": "1"}))
check("another app using Hearth's server isn't offered them either",
      "spawn_subagent" not in listed({"HEARTH_BRAIN_URL": "", "HEARTH_BRAIN_TOKEN": ""}))
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all codex brain checks passed")
