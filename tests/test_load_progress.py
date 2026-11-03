"""A local model loading on the PC reaches the phone as it goes: the engine's load progress is told on
the live bus (every phase change, the percent at most four times a second, with the model's name and
the time left once the pace is known), the phone bridge passes it to the phones that are here and
never keeps it for one that's away, and a phone that comes back mid-load asks for where it stands.

    .\\.venv\\Scripts\\python.exe tests\\test_load_progress.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-load-")
os.environ["HOME"] = os.environ["USERPROFILE"] = WORK
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(WORK, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import live_bus, llmserver as L, relay_bridge as RB  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


live_bus.IN_WINDOW = True                 # as in the window's process, where the engine loads
q = live_bus.subscribe()


def heard():
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.get("kind") == "load":
            out.append(ev)
    return out


t0 = time.time() - 6
L._set_progress(phase="starting", percent=5, marker="spawned", started_at=t0, finished_at=None, error=None,
                model="Qwen3-8B-Q4_K_M.gguf")
L._set_progress(phase="loading_weights", percent=30, marker="weights")
for p in range(31, 60):
    L._set_progress(phase="loading_weights", percent=p, marker="weights")
evs = heard()
check(f"a load in progress is told as it goes, with the model and the phase ({evs[:2]})",
      len(evs) >= 2 and evs[0]["model"] == "Qwen3-8B-Q4_K_M.gguf" and evs[0]["phase"] == "starting"
      and evs[1]["phase"] == "loading_weights")
check(f"the percent at most four times a second, every phase change at once ({len(evs)} for 31 updates)", len(evs) <= 3)
time.sleep(0.3)
L._set_progress(phase="loading_weights", percent=60, marker="weights")
ev = heard()[-1]
check(f"with the time left once the pace is known ({ev.get('eta_s')} s at {ev['percent']}% after {ev['elapsed_s']} s)",
      ev.get("eta_s") is not None and 2 <= ev["eta_s"] <= 6)
L._set_progress(phase="ready", percent=100, marker="ready", finished_at=time.time())
ev = heard()[-1]
check("and that it's ready", ev["phase"] == "ready" and ev["percent"] == 100 and "eta_s" not in ev)

# The phone bridge: to the phones that are here, never to the mailbox of one that's away.
pushed = []


class Room:
    def __init__(self, chat, up):
        self.chat, self.peer_up, self.chan = chat, up, (object() if up else None)


br = RB.Bridge.__new__(RB.Bridge)
br.adapter = type("A", (), {"rooms": {"p1": Room("p1", True), "p2": Room("p2", False)},
                            "_push": lambda self, chat, obj: pushed.append((chat, obj))})()
br.on_window_event({"kind": "load", "phase": "loading_weights", "percent": 42, "model": "Qwen3-8B-Q4_K_M.gguf", "eta_s": 9})
check(f"the bridge shows it on the phone that's here, and keeps nothing for one that's away ({pushed})",
      pushed == [("p1", {"type": "load", "phase": "loading_weights", "percent": 42, "model": "Qwen3-8B-Q4_K_M.gguf", "eta_s": 9})])
RB._window = lambda method, path, body=None, timeout=30: (
    {"phase": "loading_weights", "percent": 50, "model": "m.gguf", "started_at": time.time() - 10, "finished_at": None,
     "elapsed_s": 10.0} if path == "/api/llmserver/progress" else {})
got = RB.answer({"what": "load"})
check(f"a phone back mid-load asks where it stands ({got})",
      got.get("phase") == "loading_weights" and got.get("percent") == 50 and got.get("eta_s") == 10)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all load progress checks passed")
