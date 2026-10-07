"""A helper stuck repeating a call that fails is told so, then made to answer.

Before, Hearth's own helpers had only the turn cap: one that kept running
the same failing command did so until it ran out of turns."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

work = tempfile.mkdtemp(prefix="hearth-guard-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import subagents          # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class AsyncStuck:
    """A model that runs the same failing command until it is given no tools."""

    def __init__(self):
        self.calls = 0
        self.without_tools = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls += 1
        if not kw.get("tools"):
            self.without_tools += 1
            msg = SimpleNamespace(content="The build keeps failing on a missing file; here is what I found.", tool_calls=None)
        else:
            call = SimpleNamespace(id=f"c{self.calls}", type="function",
                                   function=SimpleNamespace(name="run_command",
                                                            arguments=json.dumps({"command": "npm run build"})))
            msg = SimpleNamespace(content="", tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


subagents_tools = [{"type": "function", "function": {"name": "run_command", "parameters": {"type": "object"}}}]
tpath = Path(work) / "sub_guard.jsonl"
client = AsyncStuck()
# Every call is allowed, so the loop itself is what's being tested.
import hearth.tools as _t        # noqa: E402
_t.standing_permission = lambda name, args: "allow"
out = asyncio.run(subagents._subagent_turns(
    client, "stand-in", [{"role": "user", "content": "build it"}], subagents_tools, 30,
    "sub_guard", tpath, [], None, lambda name, args: "Error: Cannot find module 'webpack'"))
kinds = [json.loads(l).get("kind") for l in tpath.read_text(encoding="utf-8").splitlines() if l.strip()]
check(f"the helper answers instead of running out of turns {out.get('ok')} after {out.get('turns')} turns",
      out.get("ok") and out.get("turns", 99) < 30)
check(f"it was made to answer without tools once it was stuck ({client.without_tools})", client.without_tools == 1)
check("the transcript says it was going in circles", "guard" in kinds)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all helper loop guard checks passed")
