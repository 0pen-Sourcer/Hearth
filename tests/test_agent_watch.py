"""A coding agent finishing on the PC reaches the phone, as Shed does.

The owner saw Codex's own notification on the PC and none on the phone: the
phone was told only about turns it started. Now the bridge looks every 10 s;
a session written to and then quiet for 20 s, ending on the agent's words,
sends "Codex finished". Still working (ending on a step or on the person's
message), already there at start, or driven from the phone: nothing.

    .\\.venv\\Scripts\\python.exe tests\\test_agent_watch.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-agent-watch-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

from hearth import agent_sessions as S, relay_bridge as RB          # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ends = {}
S.read = lambda agent, sid: {"messages": [{"role": "user", "text": "fix it"}] + ends.get(sid, [])}
done = [{"role": "assistant", "text": "Fixed the menu.", "parts": [{"steps": [{"w": "Ran npm test"}]}, {"text": "Fixed the menu."}]}]
mid = [{"role": "assistant", "text": "Looking.", "parts": [{"text": "Looking."}, {"steps": [{"w": "Ran npm test"}]}]}]
err = [{"role": "assistant", "text": "You've hit your usage limit.", "error": True}]


def row(sid, at, title="Menu flashes"):
    return {"agent": "codex", "id": sid, "chat": "agent_codex_" + sid, "at": at, "title": title,
            "project": "NIGHT_COURIER", "label": "Codex"}


w = RB.AgentWatch()
t0 = 1_000_000_000
check("nothing for what was already there at start", w.look([row("a", t0)], t0 + 60_000, set()) == [])
ends["a"] = mid
check("written to just now: not yet", w.look([row("a", t0 + 61_000)], t0 + 62_000, set()) == [])
check("quiet, but it ended on a step (a command still running): nothing", w.look([row("a", t0 + 61_000)], t0 + 90_000, set()) == [])
ends["a"] = done
w.look([row("a", t0 + 95_000)], t0 + 96_000, set())
got = w.look([row("a", t0 + 95_000)], t0 + 120_000, set())
check(f"written, then quiet, ending on its words: Codex finished, opening that session {got}",
      len(got) == 1 and got[0]["title"] == "Codex finished" and got[0]["c"] == "agent_codex_a"
      and got[0]["body"].startswith("Menu flashes: Fixed the menu."))
check("and only once", w.look([row("a", t0 + 95_000)], t0 + 140_000, set()) == [])
ends["b"] = err
w.look([row("a", t0 + 95_000), row("b", t0 + 150_000, "Inventory")], t0 + 151_000, set())
got = w.look([row("a", t0 + 95_000), row("b", t0 + 150_000, "Inventory")], t0 + 175_000, set())
check(f"a new session that stopped on an error says it stopped {got}", len(got) == 1 and got[0]["title"] == "Codex stopped")
ends["c"] = done
w.look([row("c", t0 + 180_000)], t0 + 181_000, {"agent_codex_c"})
check("one the phone is driving is left to its own notification",
      w.look([row("c", t0 + 180_000)], t0 + 210_000, {"agent_codex_c"}) == [])

print("\n" + ("all agent watch checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
