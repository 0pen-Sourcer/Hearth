"""An agent brain starts again when an earlier message is edited.

Claude Code, Codex, Cursor and the ACP agents keep a session per chat, found
by the chat's first message. Editing a later message, or regenerating the last
reply, kept resuming that session, so the agent still remembered the turns
the edit had replaced.

    .\\.venv\\Scripts\\python.exe tests\\test_agent_session_edit.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="hearth-agentsess-")
sys.path.insert(0, ROOT)

from hearth import claude_brain as cb  # noqa: E402

cb._SESSIONS = os.path.join(HOME, "claude_sessions.json")
cb._HOME = HOME

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


u1, a1, u2, a2 = ({"role": "user", "content": "plan a trip to goa"}, {"role": "assistant", "content": "sure"},
                  {"role": "user", "content": "make it 3 days"}, {"role": "assistant", "content": "done"})
key = cb._chat_key([u1], "")

# Turn 2 answered "make it 3 days" and saved its session.
cb._ASKED.set("make it 3 days")
cb._save_session(key, "S1", last=cb.asked("make it 3 days"))
check("the next turn resumes the session", cb.session_for(key, [u1, a1, u2, a2]) == "S1")
check("a long chat compacted at the front still resumes it",
      cb.session_for(key, [{"role": "user", "content": "(summary of earlier turns)"}, u2, a2]) == "S1")
check("editing 'make it 3 days' starts the agent again from the chat as it is",
      cb.session_for(key, [u1, a1]) is None)
check("and so does regenerating its reply", cb.session_for(key, [u1, a1]) is None)

# A turn nobody typed (a helper's report) keeps what was asked last.
cb._ASKED.set("")
cb._save_session(key, "S2", last=cb.asked("report text"))
check("a report's turn keeps the message the session last answered",
      cb.session_for(key, [u1, a1, u2, a2]) == "S2" and cb.session_for(key, [u1, a1]) is None)

# Reports put in front of the message don't change what it is matched by.
cb._ASKED.set("make it 4 days")
cb._save_session(key, "S3", last=cb.asked("Helper Ana finished: ...\n\nmake it 4 days"))
check("helper reports in front of a message don't break the match",
      cb.session_for(key, [u1, a1, {"role": "user", "content": "make it 4 days"}, a2]) == "S3")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent session checks passed")
