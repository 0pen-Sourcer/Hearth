"""Checks for the batched desktop steps and the element wait.

A batch drives the real mouse and keyboard, so the two things worth proving
without a desktop are that it cannot be talked into running something outside
the desktop, and that it stops the moment a step fails rather than typing the
rest of the sequence into whatever ended up in front.

    .\\.venv\\Scripts\\python.exe tests\\test_desktop_batch.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T                                    # noqa: E402
from hearth import desktop_a11y as A                             # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class _Recorder:
    """Stands in for the real handlers so a step can be observed, not performed."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or {}

    def install(self, *names):
        self.saved = {n: T._HANDLERS.get(n) for n in names}
        for n in names:
            T._HANDLERS[n] = self._make(n)
        return self

    def restore(self):
        for n, fn in self.saved.items():
            if fn is not None:
                T._HANDLERS[n] = fn

    def _make(self, name):
        def _h(args):
            self.calls.append((name, dict(args)))
            return self.fail_on.get(name) or f"did {name}"
        return _h


# ---------------------------------------------------------------- the boundary
r = _Recorder().install("desktop_click")
out = T._execute_actions({"actions": [
    {"tool": "desktop_click", "name": "OK"},
    {"tool": "run_command", "command": "whoami"},
]})
check("a tool outside the desktop set is refused", "run_command" in out and "not one a batch" in out)
check("and nothing runs when a later step is illegal", r.calls == [])
r.restore()

out = T._execute_actions({"actions": [{"name": "OK"}]})
check("a step with no tool is refused", "no 'tool'" in out)

out = T._execute_actions({"actions": []})
check("an empty batch is refused", out.startswith("Error:"))

out = T._execute_actions({"actions": [{"tool": "computer_key", "key": "a"}] * 13})
check("an oversized batch is refused", "more than the 12" in out)

# ---------------------------------------------------------------- stop on error
r = _Recorder(fail_on={"desktop_type": "Error: no such field"}).install(
    "desktop_click", "desktop_type", "computer_key")
out = T._execute_actions({"actions": [
    {"tool": "desktop_click", "name": "Search"},
    {"tool": "desktop_type", "name": "Search", "text": "x"},
    {"tool": "computer_key", "key": "enter"},
]})
check("a failed step stops the batch", [c[0] for c in r.calls] == ["desktop_click", "desktop_type"])
check("and the unrun steps are reported", "1 not run" in out)
r.restore()

# ------------------------------------------------------- a stale idx is refused
r = _Recorder().install("desktop_click")
out = T._execute_actions({"actions": [
    {"tool": "desktop_click", "name": "File"},
    {"tool": "desktop_click", "idx": 4},
]})
check("an idx after a click is refused, not clicked", len(r.calls) == 1)
check("and it says why", "old" in out and "name" in out)
r.restore()

# An idx as the FIRST step still refers to the snapshot the model just read.
r = _Recorder().install("desktop_click")
T._execute_actions({"actions": [{"tool": "desktop_click", "idx": 4}]})
check("an idx as the first step is honoured", r.calls == [("desktop_click", {"idx": 4})])
r.restore()

# ---------------------------------------------------------------- the wait
_saved = (A.available, A.snapshot)
try:
    frames = [
        {"window": "Setup", "elements": [{"idx": 0, "type": "Text", "name": "Working", "x": 1, "y": 2}]},
        {"window": "Setup", "elements": [
            {"idx": 0, "type": "Text", "name": "Working", "x": 1, "y": 2},
            {"idx": 1, "type": "Button", "name": "Finish", "x": 9, "y": 8},
        ]},
    ]
    polls = []

    def _fake_snapshot(cap=50, window=""):
        frame = frames[min(len(polls), len(frames) - 1)]
        polls.append(window)
        return frame

    A.available = lambda: True
    A.snapshot = _fake_snapshot
    out = T._wait_for_element({"name": "finish", "timeout": 5})
    check("a control that appears late is found", "[1]" in out and "Finish" in out)
    check("and the idx is offered to desktop_click", "desktop_click(idx=1)" in out)

    polls.clear()
    frames = [{"window": "Setup", "elements": [
        {"idx": 0, "type": "Button", "name": "Cancel", "x": 1, "y": 2},
        {"idx": 1, "type": "Button", "name": "Retry", "x": 3, "y": 4},
    ]}]
    out = T._wait_for_element({"name": "Finish", "timeout": 1})
    check("a timeout is an error", out.startswith("Error:"))
    check("and it names what is on screen instead", "Cancel" in out and "Retry" in out)
finally:
    A.available, A.snapshot = _saved

# ------------------------------------------------ clicking a numbered control
# A marked capture numbers every control, and smart_click(mark=N) clicks one by
# its real centre. The schema listed x and y as required, and required fields
# are checked before a tool runs, so every mark-only call was refused and never
# reached the click. The click itself is stubbed: nothing touches the screen.
_seen = []
_real_smart = T._HANDLERS["smart_click"]
T._HANDLERS["smart_click"] = lambda p: (_seen.append(dict(p)), "clicked (stub)")[1]
try:
    r = T.execute_tool("smart_click", {"mark": 5})
    check("smart_click by mark alone reaches the click", bool(_seen)
          and "NOT executed" not in str(r))
    _seen.clear()
    T.execute_tool("smart_click", {"x": 10, "y": 20})
    check("and by pixel still does", bool(_seen))
finally:
    T._HANDLERS["smart_click"] = _real_smart
r = T.execute_tool("smart_click", {})
check("a call with neither mark nor a pixel is refused, not crashed",
      str(r).startswith("Error"))

# ------------------------------------------------ a sub-agent's turn limit
# The tool said an override was capped; nothing capped it.
import inspect                                                      # noqa: E402
from hearth import subagents as _SA                                 # noqa: E402
check("a sub-agent's turn override is held to the shared ceiling",
      "min(turns_cap, _CEILING)" in inspect.getsource(_SA))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all desktop batch checks passed")
