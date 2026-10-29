"""Chat titles come from the model the memory pass uses, on every brain kind:
a model on this PC first, else the agent program asked once (protocol agents
too), within a short wait.

    .\\.venv\\Scripts\\python.exe tests\\test_title_any_brain.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-title-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import chat_sync, claude_brain, memory_pass  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


asked: list = []
memory_pass._local_route = lambda: None
claude_brain.active = lambda: True
claude_brain.current = lambda: "opencode"


def fake_quick(prompt, timeout=45, any_agent=False):
    asked.append((any_agent, timeout))
    return "Fixing Earphone Lag" if any_agent else ""


claude_brain.quick = fake_quick
got = chat_sync.title_for("how do I fix my bluetooth earphones lag", memory_pass.ask_once)
check(f"a protocol agent brain is asked for the title ({got!r}, {asked})", got == "Fixing Earphone Lag" and asked[0][0])
check("within a short wait, not the memory pass's minutes", asked[0][1] <= 30)

asked.clear()
memory_pass._local_route = lambda: ("http://127.0.0.1:9", "k", "m")
memory_pass._complete = lambda base, key, model: (lambda system, user: "Local Model Title")
got = chat_sync.title_for("how do I fix my bluetooth earphones lag", memory_pass.ask_once)
check(f"a model on this PC is used first, free ({got!r})", got == "Local Model Title" and not asked)

memory_pass._local_route = lambda: None
claude_brain.quick = lambda prompt, timeout=45, any_agent=False: ""
got = chat_sync.title_for("how do I fix my bluetooth earphones lag", memory_pass.ask_once)
check(f"nothing answering still gives the opening words ({got!r})", got.startswith("how do I fix"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all title checks passed")
