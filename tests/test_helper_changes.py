"""Two helpers can't write one file at once, and a helper's file changes can be undone.

Before, two of Hearth's own helpers given overlapping work wrote over each
other with nothing said, and nothing a helper wrote could be taken back."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

work = tempfile.mkdtemp(prefix="hearth-changes-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hearth                                  # noqa: E402
from hearth import subagents                   # noqa: E402
import hearth.tools as _t                      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class AsyncScript:
    """A model that makes the given tool calls, one per turn, then answers."""

    def __init__(self, calls):
        self.calls = list(calls)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        if not self.calls:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))])
        name, args = self.calls.pop(0)
        call = SimpleNamespace(id=f"c{len(self.calls)}", type="function",
                               function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))])


_t.standing_permission = lambda name, args: "allow"
tools = [{"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
         for n in ("write_file", "edit_file", "delete_path")]


def run(agent_id, calls):
    tpath = subagents._transcript_path(agent_id)
    subagents._append_transcript(tpath, {"kind": "start", "persona": "coder", "name": agent_id[-4:], "prompt": "x"})
    out = asyncio.run(subagents._subagent_turns(AsyncScript(calls), "stand-in", [{"role": "user", "content": "go"}],
                                                tools, 10, agent_id, tpath, [], None, hearth.execute_tool))
    from hearth import helper_changes as _hc
    _hc.release(agent_id)              # as _run_one_sync does when a helper ends
    results = [json.loads(l) for l in tpath.read_text(encoding="utf-8").splitlines() if l.strip()]
    return out, [r.get("preview", "") for r in results if r.get("kind") == "tool_result"]


shared = os.path.join(work, "notes.md")
Path(shared).write_text("original\n", encoding="utf-8")

# Noah is part-way through notes.md when Rosa is sent to write it too.
_, noah = run("sub_1_noah", [("write_file", {"path": "notes.md", "content": "noah's version\n"})])
from hearth import helper_changes  # noqa: E402
helper_changes.claim("sub_1_noah", "write_file", {"path": "notes.md"})     # still working
_, rosa = run("sub_2_rosa", [("write_file", {"path": "notes.md", "content": "rosa's version\n"})])
check(f"a second helper is told who has the file instead of writing over it {rosa}",
      any("working on notes.md" in r for r in rosa) and Path(shared).read_text(encoding="utf-8") == "noah's version\n")
helper_changes.release("sub_1_noah")
_, rosa2 = run("sub_3_rosa", [("write_file", {"path": "notes.md", "content": "rosa's version\n"})])
check("once the first helper is done, the file is free", Path(shared).read_text(encoding="utf-8") == "rosa's version\n")

# Undo puts the edited file back and removes the one it made.
fresh = os.path.join(work, "draft.txt")
_, _ = run("sub_4_ivy0", [("write_file", {"path": "notes.md", "content": "ivy was here\n"}),
                         ("write_file", {"path": "draft.txt", "content": "new\n"})])
story = subagents.agent_story("sub_4_ivy0")
check(f"the panel lists what it changed {story.get('changes')}",
      story.get("changes", {}).get("can_undo") and len(story["changes"]["files"]) == 2)
undone = helper_changes.undo("sub_4_ivy0")
check(f"undo puts the file back and removes the new one {undone}",
      undone.get("ok") and Path(shared).read_text(encoding="utf-8") == "rosa's version\n" and not os.path.exists(fresh)
      and len(undone.get("restored", [])) == 2)
check("undo only once", not helper_changes.undo("sub_4_ivy0").get("ok"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all helper change checks passed")
