"""Reading a place that holds keys asks in every approval mode, and Auto's
reviewer can't let it through: Inglen's own tools and loop, the tool server an
agent brain uses, agent sessions' permission questions and the agents' own read
rules. Stand-in home, workspace, keys and model.

    .\\.venv\\Scripts\\python.exe tests\\test_secret_reads.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import asyncio
import json
import os
import sys
import threading
from types import SimpleNamespace as NS

WS = _home.WORKSPACE if hasattr(_home, "WORKSPACE") else os.environ["HEARTH_WORKSPACE"]
HOME = _home.HOME
for k in ("HEARTH_AUTO_APPROVE", "JARVIS_AUTO_APPROVE", "LOCAL_API_KEY"):
    os.environ.pop(k, None)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import auto_review, claude_brain as cb, headless, memory_extract, tools, acp_brain  # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.note_turn_async = lambda *a, **k: None
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
tools.WORKSPACE = WS

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def mode(m, **extra):
    with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(dict({"approval_mode": m, "auto_reviewer": "off"}, **extra), f)


os.makedirs(os.path.join(HOME, ".ssh"))
os.makedirs(os.path.join(HOME, ".hearth"))
with open(os.path.join(HOME, ".ssh", "id_ed25519"), "w") as f:
    f.write("standin private key")

SECRET_CALLS = [
    ("read_file", {"path": "~/.ssh/id_ed25519"}),
    ("read_file", {"path": os.path.join(HOME, ".hearth", "plus.json")}),
    ("read_file", {"path": "project/.env"}),
    ("run_command", {"command": "type %USERPROFILE%\\.aws\\credentials"}),
    ("run_command", {"command": "Get-Content $env:USERPROFILE\\.hearth\\relay.json"}),
    ("run_command", {"command": "cat ../" + os.path.basename(HOME) + "/.ssh/id_ed25519", "cwd": WS}),
    ("run_command", {"command": "cat id_ed25519", "cwd": "~/.ssh"}),
    ("run_command", {"command": "python -c \"print(open(r'" + HOME + "/.docker/config.json').read())\""}),
    ("run_command", {"command": "echo $env:LOCAL_API_KEY"}),
    ("run_command", {"command": "Get-ChildItem env:"}),
    ("run_command", {"command": "copy \"%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Login Data\" x.db"}),
    ("run_command", {"command": "lazagne.exe all"}),
    ("start_job", {"command": "cat ~/.netrc"}),
    ("grep_search", {"pattern": "sk-ant", "path": "~"}),
]
PLAIN_CALLS = [
    ("read_file", {"path": "notes.txt"}),
    ("run_command", {"command": "npm test"}),
    ("run_command", {"command": "cmdkey /list"}),
    ("read_file", {"path": "project/.env.example"}),
]

for m in ("ask", "edits", "auto"):
    mode(m)
    leaked = [(n, a) for n, a in SECRET_CALLS if tools.mode_allows(n, a)]
    check(f"{m}: no read of a key place runs without asking ({leaked})", not leaked)
    unflagged = [(n, a) for n, a in SECRET_CALLS if not tools.is_risky(n, a)]
    check(f"{m}: every one counts as needing approval ({unflagged})", not unflagged)
check("a spoken yes can't approve it either", all(tools.needs_a_tap(n, a) for n, a in SECRET_CALLS))
mode("auto")
check("ordinary reads and commands are as before",
      not any(tools.sends_out(n, a) for n, a in PLAIN_CALLS) and tools.mode_allows("run_command", {"command": "npm test"}))

why = tools.sends_out(*SECRET_CALLS[0])
check(f"the question says what would be read and that it holds keys ({why!r})",
      "id_ed25519" in why and "SSH keys" in why and "always asks" in why)
check("the window's question carries that reason", auto_review.why(*SECRET_CALLS[0]) == why)

# The reviewer saying go, or a standing yes, doesn't cover it.
_allows = auto_review.allows
auto_review.allows = lambda name, args: True
check("Auto's reviewer can't wave it through", not tools.mode_allows(*SECRET_CALLS[3]))
auto_review.allows = _allows
with open(os.path.join(WS, "permissions.json"), "w", encoding="utf-8") as f:
    json.dump({"read_file": "always", "run_command": "always"}, f)
os.environ["HEARTH_AUTO_APPROVE"] = "1"
check("a standing yes and auto-approve still leave it asking",
      all(tools.standing_permission(n, a) == "ask" for n, a in SECRET_CALLS))
os.environ.pop("HEARTH_AUTO_APPROVE")
os.remove(os.path.join(WS, "permissions.json"))

# A settings.json in the workspace asks only while it still holds keys as plain text.
mode("auto", llm_key="sk-ant-STANDINSTANDINSTANDINSTANDIN")
check("settings.json with a plain key asks", bool(tools.sends_out("read_file", {"path": "settings.json"})))
from hearth import keystore  # noqa: E402
keystore.migrate()
check("once sealed it reads freely", not tools.sends_out("read_file", {"path": "settings.json"}))
mode("auto")

# ---- Inglen's own loop, with nobody there and auto-approve on
def chunk(content=None, tool=None, finish=None):
    return NS(choices=[NS(index=0, delta=NS(content=content, reasoning_content=None, model_extra={},
                                             tool_calls=[tool] if tool else None), finish_reason=finish)],
              usage=None, model_extra={})


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()


def model(*steps):
    replies = [[chunk(tool=NS(index=0, id=f"call_{i}", type="function",
                              function=NS(name=n, arguments=json.dumps(a))), finish="tool_calls")]
               for i, (n, a) in enumerate(steps)]
    replies.append([chunk(content="Done."), chunk(finish="stop")])

    async def fake(client, **kw):
        return Stream(replies.pop(0))
    return fake


results = []
os.environ["HEARTH_AUTO_APPROVE"] = "1"
headless.chat_create = model(("read_file", {"path": "~/.ssh/id_ed25519"}))
asyncio.run(headless.run_once("show me my ssh key", model="m", history=[], chat_id="c1", permission_check=None,
                              emit=lambda k, **f: results.append(f.get("content")) if k == "tool_result" else None))
os.environ.pop("HEARTH_AUTO_APPROVE")
check(f"with nobody to ask, auto-approve doesn't read it ({results})",
      results and "standin private key" not in str(results) and "Not run" in str(results[0]))

# ---- the tool server an agent brain uses
os.environ.update(HEARTH_BRAIN_URL="http://127.0.0.1:9", HEARTH_BRAIN_TOKEN=cb._TOKEN, HEARTH_BRAIN_GATE="1")
from hearth import mcp_server  # noqa: E402
asked = []


def post_hearth(route, tool, inp):
    asked.append(tool)
    return {"behavior": "deny", "message": "The user declined this."}


mcp_server._post_hearth = post_hearth
mcp_server._ask_hearth = lambda tool, inp: post_hearth("", tool, inp)
out = str(mcp_server._run("read_file", {"path": "~/.ssh/id_ed25519"}))
check(f"through the tool server it asks and isn't read ({out[:60]!r})",
      asked and "standin private key" not in out and "Not done" in out)

# ---- agents' own permission questions, as Inglen sees them
mode("auto")
for label, (n, a) in (("Claude Code's Read", cb.hearth_view("Read", {"file_path": "~/.ssh/id_ed25519"})),
                      ("Claude Code's Bash", cb.hearth_view("Bash", {"command": "cat ~/.hearth/plus.json"})),
                      ("an ACP agent's read", acp_brain.hearth_call({"kind": "read", "locations": [
                          {"path": os.path.join(HOME, ".ssh", "id_ed25519")}]})),
                      ("OpenCode's shell", acp_brain.hearth_call({"kind": "execute", "rawInput": {
                          "command": "cat " + os.path.join(WS, "..", ".hearth", "plus.json")}}))):
    check(f"{label} asks in Auto", not tools.mode_allows(n, a))
from hearth.channel_core import question_for  # noqa: E402
q = question_for("OpenCode", "run_command", {"command": "cat ~/.ssh/id_ed25519"})
check(f"an agent session's question says it holds keys ({q!r})", "OpenCode asks" in q and "SSH keys" in q)

# ---- the agents' own read rules
oc = json.loads(acp_brain._env_for("opencode")["OPENCODE_CONFIG_CONTENT"])
rd = (oc.get("permission") or {}).get("read") or {}
check(f"OpenCode asks Inglen before reading a key place ({list(rd)[:4]})",
      isinstance(rd, dict) and rd.get("*") == "allow" and any(".ssh" in k and v == "ask" for k, v in rd.items())
      and any(".env" in k and v == "ask" for k, v in rd.items()))
cs = cb.claude_settings()
asks = (cs.get("permissions") or {}).get("ask") or []
check(f"Claude Code asks before reading a key place ({asks[:3]})",
      any(r.startswith("Read(") and ".ssh" in r for r in asks) and any(".env" in r for r in asks))
os.environ["LOCAL_API_KEY"] = "sk-ant-STANDINENVSTANDINENVSTANDIN"
env = cb.agent_env()
check("agent programs don't inherit the key in use", "LOCAL_API_KEY" not in env and "HEARTH_EMAIL_PASSWORD" not in env)
check("OpenCode's environment is the same", "LOCAL_API_KEY" not in acp_brain._agent_env("opencode"))

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall secret read checks passed")
