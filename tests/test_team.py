"""A team of agents, as the panes show it and as the model is told it started.

The pane read each tool result from a field the sub-agent never writes, so
every result line was an empty arrow, and it took an end record's missing
status as success, so an agent that failed showed a green "done". Members
dropped over the size limit, with no prompt, or with a persona that does not
exist (the tool offered one) were left out of the reply without a word, so
the model believed that part of the task was being worked on.

The sub-agent runs for real against a stand-in model, and its own transcript
is what gets rendered.

    .\\.venv\\Scripts\\python.exe tests\\test_team.py
"""
from __future__ import annotations

import http.server
import json
import os
import re
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

WS = tempfile.mkdtemp(prefix="hearth-team-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


NOTE = os.path.join(WS, "note.txt")
with open(NOTE, "w", encoding="utf-8") as f:
    f.write("PANE-VISIBLE-CONTENT")
STATE = {"calls": 0, "fail": False, "call": None}


class _Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json(200, {"object": "list", "data": [{"id": "standin", "object": "model"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if STATE["fail"]:
            return self._json(400, {"error": {"message": "the model refused", "type": "bad"}})
        STATE["calls"] += 1
        if STATE["calls"] % 2 == 1:
            msg = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": STATE["call"] or {"name": "read_file",
                                              "arguments": json.dumps({"path": NOTE})}}]}
            fin = "tool_calls"
        else:
            msg, fin = {"role": "assistant", "content": "all finished"}, "stop"
        self._json(200, {"id": "x", "object": "chat.completion", "model": "standin",
                         "choices": [{"index": 0, "message": msg, "finish_reason": fin}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["LOCAL_API_BASE"] = f"http://127.0.0.1:{srv.server_address[1]}/v1"
os.environ["LOCAL_API_KEY"] = "x"
os.environ["LOCAL_MODEL"] = "standin"

from hearth import subagents, team                                   # noqa: E402

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def rendered(transcript):
    out = []
    for line in open(transcript, encoding="utf-8"):
        r = team._render_entry(json.loads(line))
        if r:
            out.append(_ANSI.sub("", r))
    return "\n".join(out)


# ----------------------------------------------------------------- the pane
r = subagents.spawn_subagent(persona="researcher", prompt="read the note", mode="sync")
pane = rendered(r["transcript_path"])
check("the agent ran", r.get("ok") is True)
check("a tool result shows what the tool returned, not an empty arrow",
      "↳ read_file: " in pane and "PANE-VISIBLE-CONTENT" in pane)
check("a finished agent ends on done", pane.rstrip().endswith("■ done"))

STATE["fail"] = True
r = subagents.spawn_subagent(persona="researcher", prompt="read the note", mode="sync")
pane = rendered(r["transcript_path"])
check(f"a failed agent ends on failed, with why: {pane.splitlines()[-1]!r}",
      r.get("ok") is False and "■ failed" in pane and "■ done" not in pane)
STATE["fail"] = False

# ----------------------------------------------- a sub-agent and your settings
# A sub-agent ran risky tools with nobody asked, so it could do what the
# user's own permission setting would have stopped to ask about.
TARGET = os.path.join(WS, "made_by_agent.txt")
STATE["call"] = {"name": "write_file",
                 "arguments": json.dumps({"path": TARGET, "content": "hello"})}
STATE["calls"] = 0
r = subagents.spawn_subagent(persona="coder", prompt="write the file", mode="sync")
check("without your standing permission a sub-agent's risky call does not run",
      not os.path.exists(TARGET))
check("and it is told why", "cannot ask" in rendered(r["transcript_path"]))
with open(os.path.join(WS, "permissions.json"), "w", encoding="utf-8") as f:
    json.dump({"write_file": "always"}, f)
STATE["calls"] = 0
subagents.spawn_subagent(persona="coder", prompt="write the file", mode="sync")
check("with allow-always for that tool, it runs", os.path.exists(TARGET))
os.remove(os.path.join(WS, "permissions.json"))
STATE["call"] = None

# --------------------------------------------------------- who actually started
# The stand-in runs on 127.0.0.1, so this is a local endpoint: a limit of 4.
team._open_wt = team._open_consoles = team._open_tmux = lambda _s: False
_spawn = subagents.spawn_subagent
subagents.spawn_subagent = lambda **kw: (
    {"ok": False, "error": f"unknown persona {kw['persona']!r}"} if kw["persona"] == "ghost"
    else {"ok": True, "agent_id": kw["name"], "transcript_path": ""})
res = team.launch_team([
    {"name": "A", "prompt": "a"}, {"name": "Blank", "prompt": ""},
    {"name": "Ghost", "persona": "ghost", "prompt": "g"},
    {"name": "B", "prompt": "b"}, {"name": "C", "prompt": "c"}, {"name": "D", "prompt": "d"},
    {"name": "E", "prompt": "e"}])
subagents.spawn_subagent = _spawn
dropped = {d["name"]: d["reason"] for d in res.get("dropped", [])}
check(f"members that did not start are named, with the reason: {dropped}",
      set(dropped) == {"Blank", "Ghost", "E"})
check("and the reply the model reads says so",
      all(n in res["note"] for n in ("Blank", "Ghost", "E")))

# A mixed team in one call: helpers on this PC, helpers on a provider's model,
# and agent programs, each where it was asked to run.
routes, programs = {}, []
_route_for = subagents.route_for
subagents.route_for = lambda on, model="": (
    (None, "") if not on else (("http://localhost:1234/v1", "hearth-builtin", ""), "") if on == "local"
    else (("https://api.anthropic.com/v1/", "k", "claude-haiku-4-5"), "") if on == "anthropic"
    else (None, f"'{on}' isn't a provider Hearth knows."))
subagents.spawn_subagent = lambda **kw: (routes.__setitem__(kw["name"], kw.get("route")) or
                                         {"ok": True, "agent_id": kw["name"], "transcript_path": ""})
from hearth import external_agents as _xa                            # noqa: E402
_xspawn = _xa.spawn
_xa.spawn = lambda agent, prompt, name="", cwd="": (programs.append((agent, name)) or
                                                    {"ok": True, "agent_id": name, "label": agent})
res = team.launch_team(
    [{"name": f"Local{i}", "prompt": "p", "runs_on": "local"} for i in range(4)]
    + [{"name": f"Haiku{i}", "prompt": "p", "runs_on": "anthropic", "model": "haiku"} for i in range(2)]
    + [{"name": "Nowhere", "prompt": "p", "runs_on": "nowhere"}]
    + [{"name": f"Codex{i}", "prompt": "p", "agent": "codex"} for i in range(2)])
subagents.spawn_subagent, subagents.route_for, _xa.spawn = _spawn, _route_for, _xspawn
dropped = {d["name"]: d["reason"] for d in res.get("dropped", [])}
check(f"a mixed team starts each member where it was asked to run {sorted(routes)} {programs} {dropped}",
      routes.get("Haiku0") and routes["Haiku0"][2] == "claude-haiku-4-5" and routes.get("Local3")
      and len(programs) == 2 and set(dropped) == {"Nowhere"} and "isn't a provider" in dropped["Nowhere"])

from hearth import tools as T                                        # noqa: E402
_real = {p["slug"] if "slug" in p else p["name"] for p in subagents.list_personas()}
_enum = next(t for t in T.TOOL_DEFINITIONS if t["name"] == "launch_team")[
    "parameters"]["properties"]["members"]["items"]["properties"]["persona"]["enum"]
check(f"launch_team only offers personas that exist: {sorted(set(_enum) - _real)}",
      set(_enum) <= _real)

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all team checks passed")
