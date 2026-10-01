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
check("on Ask, Codex's own shell can read but not change anything", 'sandbox_mode="read-only"' in argv)
check("the user's own Hearth entry is off for the run, other servers untouched",
      "mcp_servers.hearth.enabled=false" in argv and "mcp_servers.docs.enabled=false" not in argv)
check("the persona goes in as developer instructions", any(a.startswith("developer_instructions=") for a in argv))

tools.approval_mode = lambda: "auto"
hist = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "I'm JARVIS."}]
turn("open spotify", hist)
second = json.loads(open(argv_log).read().splitlines()[1])["argv"]
check("the next turn of the same chat resumes the same Codex thread", second[0:3] == ["exec", "resume", "th-1"])
check("on Auto, Codex works in the workspace with the network on",
      'sandbox_mode="workspace-write"' in second and "sandbox_workspace_write.network_access=true" in second)

ev = turn("FAIL please", [])
check("a failed turn says why", any(t == "error" and "usage limit" in f["message"] for t, f in ev))

cb.exe_for = lambda p: None
ev = turn("hi", [])
check("without Codex installed it says how to get it",
      any(t == "error" and "isn't installed" in f["message"] for t, f in ev))

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
