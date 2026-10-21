"""Telling a helper something while it works.

A stand-in model answers the helper's requests, so this runs the real helper
loop without a model: a message given part-way is in front of the model on its
next step, the transcript records it, a helper that has finished says so, and
a task handed to another agent's program says it can't take one.

    .\\.venv\\Scripts\\python.exe tests\\test_tell_subagent.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


work = tempfile.mkdtemp(prefix="hearth-tell-")
os.environ["JARVIS_WORKSPACE"] = work
os.environ["LOCAL_API_BASE"] = "http://127.0.0.1:9/v1"
import openai                                                        # noqa: E402
from hearth import subagents                                         # noqa: E402
from hearth import claude_brain as cb                                # noqa: E402

subagents._TRANSCRIPT_DIR = __import__("pathlib").Path(work) / "subagents"
cb.current = lambda: None
seen: list = []          # what the model was shown on each request
told: dict = {}


def _msg(content=None, calls=None):
    m = types.SimpleNamespace(content=content, tool_calls=calls, reasoning_content=None)
    m.model_dump = lambda **k: {"role": "assistant", "content": content}
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=m, finish_reason="stop")], usage=None)


class FakeCompletions:
    async def create(self, **kw):
        seen.append([str(m.get("content")) for m in kw["messages"] if isinstance(m, dict)])
        if len(seen) == 1:
            # Part-way through: someone tells the helper something.
            agent_id = next(iter(subagents._INBOX))
            told["id"] = agent_id
            told["can"] = subagents.can_tell(agent_id)
            told["result"] = subagents.tell_subagent(agent_id, "use tabs, not spaces")
            call = types.SimpleNamespace(id="c1", type="function", function=types.SimpleNamespace(
                name="list_directory", arguments=json.dumps({"path": work})))
            return _msg(None, [call])
        return _msg("Done, with tabs.")


class FakeClient:
    def __init__(self, **kw):
        self.chat = types.SimpleNamespace(completions=FakeCompletions())
        self.models = types.SimpleNamespace(list=self._list)

    async def _list(self):
        return types.SimpleNamespace(data=[types.SimpleNamespace(id="stand-in")])


openai.AsyncOpenAI = FakeClient

r = subagents.spawn_subagent("coder", "format the file", mode="sync")
check(f"the helper ran to its answer {r.get('text')!r} {r.get('error')!r}", r.get("ok") and "tabs" in r.get("text", ""))
check("a working helper can be told something", told.get("can") and told.get("result", {}).get("ok"))
check("the message is in front of it on its next step",
      len(seen) >= 2 and any("use tabs, not spaces" in c and "the user" in c for c in seen[1]))
check("it wasn't there before it was said", not any("use tabs" in c for c in seen[0]))
lines = [json.loads(x) for x in open(subagents._transcript_path(told["id"]), encoding="utf-8")]
check("the transcript records what was said and by whom",
      any(x.get("kind") == "told" and x.get("text") == "use tabs, not spaces" for x in lines))
_again = subagents.tell_subagent(told["id"], "one more thing")
check(f"once it has finished, telling it starts it again on that, as a follow-up {_again.get('note') or _again.get('error')!r}",
      not subagents.can_tell(told["id"]) and _again.get("ok") and _again.get("follows") == told["id"])
with subagents._BG_RESULTS_LOCK:
    subagents._BG_RUNNING.add("sub_other_agent")
check("a task handed to another agent's program says it can't take one",
      "can't take a message" in subagents.tell_subagent("sub_other_agent", "hi").get("error", ""))
with subagents._BG_RESULTS_LOCK:
    subagents._BG_RUNNING.discard("sub_other_agent")
check("an empty message is refused", not subagents.tell_subagent(told["id"], "  ").get("ok"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all tell-a-helper checks passed")
