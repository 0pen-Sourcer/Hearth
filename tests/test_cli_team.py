"""The terminal's /team shows a helper's steps in words and acts on what it changed.

Before, /team <n> printed raw tool arguments, and a helper's changes could be
brought in, discarded or undone only from the window."""

from __future__ import annotations

import asyncio
import io
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-cliteam-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hearth_cli                                  # noqa: E402
from hearth import subagents, helper_changes      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A finished helper that wrote one file directly.
aid = "sub_1_cli000"
tpath = subagents._transcript_path(aid)
target = os.path.join(work, "notes.md")
Path(target).write_text("before\n", encoding="utf-8")
helper_changes.keep_before(aid, "write_file", {"path": target})
Path(target).write_text("after\n", encoding="utf-8")
for rec in ({"kind": "start", "persona": "coder", "name": "Noah", "prompt": "update the notes"},
            {"kind": "assistant_tool_call", "calls": [{"name": "write_file", "args": '{"path": "C:\\\\Users\\\\x\\\\notes.md"}'}]},
            {"kind": "tool_result", "name": "write_file", "preview": "Wrote notes.md"},
            {"kind": "end", "ok": True}):
    subagents._append_transcript(tpath, rec)

cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
ansi = re.compile(r"\x1b\[[0-9;]*m")


def run(cmd):
    buf = io.StringIO()
    with redirect_stdout(buf):
        asyncio.run(cli.handle_command(cmd))
    return ansi.sub("", buf.getvalue())


out = run("/team 1")
check(f"a helper's steps read as what each was pointed at {out!r}",
      "› write_file  notes.md" in out and "{" not in out and "changed 1 file" in out)
out = run("/team changes 1")
check(f"/team changes lists the files and says what can be done {out!r}", "notes.md" in out and "/team undo 1" in out)
out = run("/team undo 1")
check(f"/team undo puts the file back {out!r}", "Undone: 1 file" in out and Path(target).read_text(encoding="utf-8") == "before\n")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all CLI team checks passed")
