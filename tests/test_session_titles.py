"""Coding-agent sessions on the phone are named as their own app names them.

The owner's Codex session showed on the phone as "# Files pasted by the user:
## "/loo..." while Codex called it by its name: the title was the first
message, with what Codex adds before the person's words. Now: Codex's thread
names (~/.codex/session_index.jsonl, the newest entry wins), the words after
the last "## My request for Codex:" as Codex shows them, and the name given to
a Claude Code session (/rename).

    .\\.venv\\Scripts\\python.exe tests\\test_session_titles.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-session-titles-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import agent_sessions as S  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def write(path, *objs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(o) for o in objs) + "\n")


T = "2026-10-29T08:00:00Z"
GAME = os.path.join(HOME, "t")
os.makedirs(GAME)
CX = os.path.join(HOME, ".codex", "sessions", "2026", "10", "29")
PASTED = ('# Files pasted by the user:\n\n## "/loop/inventory.gd"\nextends Node\n\n'
          '## My request for Codex:\nFix the van losing its reflections')


def codex(sid, first):
    write(os.path.join(CX, f"rollout-x-{sid}.jsonl"),
          {"timestamp": T, "type": "session_meta", "payload": {"id": sid, "cwd": GAME}},
          {"timestamp": T, "type": "event_msg", "payload": {"type": "user_message", "message": first}},
          {"timestamp": T, "type": "event_msg", "payload": {"type": "agent_message", "message": "On it."}})


A, B = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
codex(A, PASTED)
codex(B, PASTED)
write(os.path.join(HOME, ".codex", "session_index.jsonl"),
      {"id": B, "thread_name": "Old name", "updated_at": T},
      {"id": B, "thread_name": "NIGHT COURIER goal", "updated_at": T})
CL = "33333333-3333-3333-3333-333333333333"
write(os.path.join(HOME, ".claude", "projects", "-t", CL + ".jsonl"),
      {"type": "user", "cwd": GAME, "timestamp": T, "message": {"role": "user", "content": "check the van"}},
      {"type": "assistant", "cwd": GAME, "timestamp": T, "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
      {"type": "custom-title", "customTitle": "Van reflections", "sessionId": CL})

titles = {r["id"]: r["title"] for r in S.listing(20)}
check(f"a Codex message's pasted files aren't its title: the person's words are ({titles.get(A)!r})",
      titles.get(A) == "Fix the van losing its reflections")
check(f"a thread Codex has named is called by that name, the newest one ({titles.get(B)!r})",
      titles.get(B) == "NIGHT COURIER goal")
check(f"a Claude Code session given a name is called by it ({titles.get(CL)!r})", titles.get(CL) == "Van reflections")
msgs = S.transcript(S.chat_id("codex", A), 10)["messages"]
check(f"and the message itself shows only what they asked ({msgs[0]['text']!r})",
      msgs[0]["text"] == "Fix the van losing its reflections")

print("\n" + ("all session title checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
