"""What the PC hands the phone for notifications and questions: plain words, the
command apart, the relay's own address, and a session's model reaching its agent."""
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="hearth-relay-notes-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ.pop("HEARTH_RELAY_URL", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hearth import relay_bridge as RB  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok    " if ok else "FAIL  ") + name)
    fails += 0 if ok else 1


md = ("**Auto in agent chats is fixed.** Phone sessions with `OpenCode` now follow the [switch](https://x.y).\n\n"
      "## Details\n- one\n- two\n\n```python\nprint(1)\n```")
body = RB.note_body(md)
check(f"a notification body has no markdown ({body!r})",
      not any(c in body for c in "*`#[]") and body.startswith("Auto in agent chats is fixed. Phone sessions"))
check("and stops after two sentences", body.count(".") <= 2 and "Details" not in body)
long = RB.note_body("word " * 200)
check(f"a long one ends on a whole word ({len(long)} chars)", len(long) <= 200 and long.endswith("…"))
check("a title loses its markdown", RB.plain_text("__Codex__ asks") == "Codex asks")

p = RB.ask_parts("OpenCode", "OpenCode asks: may it run `Get-ChildItem -Path C:\\x | Where-Object { $_.Length -gt 1 }`?")
check(f"an agent's question says who wants what ({p['head']!r})", p["head"] == "OpenCode wants to run a command")
check(f"and keeps the command apart ({p['cmd']!r})", p["cmd"].startswith("Get-ChildItem") and p["cmd"].endswith("}"))
n = RB.ask_note(p)
check(f"its notification: who and what on top, the command under ({n})",
      n["title"] == "OpenCode wants to run a command" and n["body"].startswith("Get-ChildItem"))
q = RB.ask_parts("JARVIS", "Delete C:\\old.txt? It's outside the workspace.")
check(f"a step that isn't a command reads as words ({q})",
      q["head"] == "JARVIS wants to delete C:\\old.txt" and q["cmd"] == "" and q["why"] == "It's outside the workspace.")
w = RB.ask_parts("JARVIS", "Run `ls ?x`? Auto checked it.", cmd="ls ?x --all")
check(f"the whole command given beside the question wins ({w['cmd']!r})", w["cmd"] == "ls ?x --all" and w["why"] == "Auto checked it.")

check(f"the relay is app.inglen.app by default ({RB.relay_url()})", RB.relay_url() == "https://app.inglen.app")
from hearth import web  # noqa: E402
s = web._load_settings()
s["relay_url"] = "https://hearth-relay.inglen.workers.dev/"
web._save_settings(s)
check(f"a saved workers.dev address moves to it ({RB.relay_url()})", RB.relay_url() == "https://app.inglen.app")

from hearth import claude_sessions  # noqa: E402
a = claude_sessions.args_for(["claude"], "abc", "default", "opus")
check(f"a session's picked model reaches Claude Code ({a[-4:]})", "--model" in a and a[a.index("--model") + 1] == "opus")
check("and none is passed without one", "--model" not in claude_sessions.args_for(["claude"], "abc", "default"))

import asyncio  # noqa: E402
from hearth import acp_brain  # noqa: E402


class FakeRpc:
    def __init__(self):
        self.calls = []

    async def call(self, method, params, timeout):
        self.calls.append((method, params))
        return {}


r = FakeRpc()
opened = {"models": {"currentModelId": "a", "availableModels": [{"modelId": "a"}, {"modelId": "b"}]}}
asyncio.run(acp_brain.use_model(r, "opencode", "s1", opened, "b"))
check(f"an ACP session switches with the agent's own call ({r.calls})",
      r.calls == [("session/set_model", {"sessionId": "s1", "modelId": "b"})])
r2 = FakeRpc()
asyncio.run(acp_brain.use_model(r2, "opencode", "s1", opened, "a"))
check("and doesn't when it's on that model already", r2.calls == [])

RB._seen("ph1", True)
check(f"a connected phone is said so for Settings > Plus ({RB.live()})", RB.live().get("ph1", {}).get("on") is True)
RB._seen("*", False)
check("and when the phone link starts again, none is taken as connected", RB.live().get("ph1", {}).get("on") is False)
import contextlib  # noqa: E402
import io  # noqa: E402
import re  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    RB._log("phone ph1 connected")
check(f"its log lines have a time and the surface, as the other chat apps' ({buf.getvalue().strip()})",
      re.match(r"^\d\d:\d\d:\d\d \[relay\] phone ph1 connected$", buf.getvalue().strip()) is not None)

print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
