"""Helpers can ask each other, and wait for the answer.

Before, a helper could only hear from JARVIS or the person; one that needed
another helper's finding had to finish and hope JARVIS passed it along."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

work = tempfile.mkdtemp(prefix="hearth-msg-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hearth                       # noqa: E402
from hearth import subagents        # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


check("helpers are given message_helper",
      "message_helper" in [t["function"]["name"] for t in subagents._filter_tools(["web_search"])])
check("the main assistant is pointed to tell_subagent instead",
      "tell_subagent" in str(hearth.execute_tool("message_helper", {"to": "x", "text": "y"})))

# Rosa is working; she answers whatever reaches her inbox.
rosa = "sub_1_rosa00"
subagents._append_transcript(subagents._transcript_path(rosa), {"kind": "start", "persona": "researcher", "name": "Rosa", "prompt": "look up API X"})
with subagents._BG_RESULTS_LOCK:
    subagents._BG_RUNNING.add(rosa)
with subagents._INBOX_LOCK:
    subagents._INBOX[rosa] = []
heard = []


def rosa_listens():
    for _ in range(100):
        got = subagents._take_told(rosa)
        if got:
            heard.extend(got)
            subagents.tell_subagent("sub_2_noah00", "API X needs the edge case: empty input returns []", sender="Rosa",
                                    reply_to="Rosa")
            return
        time.sleep(0.1)


threading.Thread(target=rosa_listens, daemon=True).start()


class AsyncAsker:
    """Noah asks Rosa and waits, then answers with what she said."""

    def __init__(self):
        self.turn = 0
        self.seen = ""
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.turn += 1
        if self.turn == 1:
            call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(
                name="message_helper", arguments=json.dumps({"to": "rosa", "text": "Does API X need the empty-input case?",
                                                             "wait_for_reply": True})))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))])
        self.seen = kw["messages"][-1]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))])


noah = "sub_2_noah00"
tpath = subagents._transcript_path(noah)
subagents._append_transcript(tpath, {"kind": "start", "persona": "coder", "name": "Noah", "prompt": "fix the parser"})
with subagents._BG_RESULTS_LOCK:
    subagents._BG_RUNNING.add(noah)
with subagents._INBOX_LOCK:
    subagents._INBOX[noah] = []
asker = AsyncAsker()
tools = subagents._filter_tools(["read_file"])
asyncio.run(subagents._subagent_turns(asker, "stand-in", [{"role": "user", "content": "go"}], tools, 5, noah, tpath,
                                      [], None, hearth.execute_tool))
check(f"Rosa got the question, told who asked {heard}", heard and "from Noah" in heard[0] and "message_helper to Noah" in heard[0])
check(f"Noah waited and got her answer as the result {asker.seen[:120]!r}", "empty input returns []" in asker.seen)
noah_story = subagents.agent_story(noah)
check("Noah's steps show what he sent and what came back",
      any(s.get("sent") for s in noah_story["steps"]) and any(s.get("from") == "Rosa" for s in noah_story["steps"]))
rosa_story = subagents.agent_story(rosa)
check("Rosa's steps show the question", any(s.get("from") == "Noah" for s in rosa_story["steps"]))
talk = {a["id"]: a for a in subagents.team_state()}[noah]["talk"]
check(f"the room hears who talked to whom {talk}", talk and talk["to_id"] == rosa and "API X" in talk["text"])

# No one by that name, and no talking in circles.
r = asyncio.run(subagents.message_helper(noah, "Noah", {"to": "Zed", "text": "hi"}))
check(f"a name that isn't working says who is {r}", not r["ok"] and "Rosa" in r["error"])
subagents._SENT[noah] = subagents.MESSAGES_PER_HELPER
r = asyncio.run(subagents.message_helper(noah, "Noah", {"to": "Rosa", "text": "again?"}))
check("after enough messages it is told to finish", not r["ok"] and "enough" in r["error"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all helper message checks passed")
