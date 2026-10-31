"""A step is said the same way in the window, the terminal and the phone.

Tools with no words of their own used to fall into the window's "read a file";
the brain's own description of a step is used where it gives one, in the
present while it runs and the past once done.

    .\\.venv\\Scripts\\python.exe tests\\test_step_words.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import step_words as S, live_bus, term_render, claude_brain, acp_brain   # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


check("the model's words in both tenses", S.tenses("Check the work") == ("Checking the work", "Checked the work"))
check("a verb that changes its spelling", S.tenses("Run the tests") == ("Running the tests", "Ran the tests"))
check("a dropped e and a doubled letter", S.tenses("Create a branch")[1] == "Created a branch"
      and S.tenses("Stop the server")[0] == "Stopping the server")
check("words already in the present", S.tenses("Listing files in src") == ("Listing files in src", "Listed files in src"))
check("words that don't start with a verb stay as said", S.tenses("Git status") == ("Git status", "Git status"))

w = S.words("load_tools", {"query": "image generation"})
check(f"load_tools has words of its own ({w})", w["done"] == "Got tools for image generation" and "file" not in w["done"].lower())
check("end_session says goodbye", S.words("end_session", {})["done"] == "Said goodbye")
check("an unknown tool says it was used", S.words("system_vibe", {}) == {"doing": "Using system vibe", "done": "Used system vibe"})
check("Inglen's own words when the brain gives none", S.words("run_command", {"command": "npm test"})["doing"] == "Running npm test")
check("the brain's description wins", S.words("run_command", {"command": "npm test"}, "Run the tests")["done"] == "Ran the tests")

check("Claude Code's description of its own command is read",
      claude_brain.says_of("Bash", {"command": "pytest", "description": "Run the unit tests"}) == "Run the unit tests")
check("an argument called description on Inglen's own tool isn't",
      claude_brain.says_of("mcp__hearth__create_plugin", {"description": "a tool"}) == "")
check("an ACP agent's description, else a title that says more than the command",
      acp_brain.says_of({"rawInput": {"command": "ls", "description": "List the files"}}) == "List the files"
      and acp_brain.says_of({"title": "ls", "rawInput": {"command": "ls"}}) == ""
      and acp_brain.says_of({"title": "Read the config file", "rawInput": {}}) == "Read the config file")

# One turn's tool call, as each surface gets it.
got = []
emit = S.Wrap(lambda kind, **f: got.append((kind, f)))
emit("tool_call", name="run_command", args={"command": "pytest"}, id="t1", says="Run the unit tests")
f = got[-1][1]
check("every tool call carries its words", f.get("words") == {"doing": "Running the unit tests", "done": "Ran the unit tests", "own": True})
ev = live_bus.turn_event("c1", "window", "t", "tool_call", f)
check("the phone gets the same words", ev["words"] == "Ran the unit tests" and ev["doing"] == "Running the unit tests")
check("as do the chat apps", term_render.tool_words("run_command", {"command": "pytest"}, "Run the unit tests") == "Ran the unit tests")

lines = []
t = term_render.Turn(out=lines.append)
t.emit("tool_call", **f)
check("the terminal says it in the present while it runs", t.status == "Running the unit tests" and not lines)
t.emit("tool_result", name="run_command", content="3 passed", id="t1")
check(f"and in the past once done ({lines})", any("Ran the unit tests" in l for l in lines) and any("3 passed" in l for l in lines))
t.emit("tool_call", name="load_tools", args={"query": "x"}, words=S.words("load_tools", {"query": "x"}))
t.emit("done", stats={})
check("a step that never reported back still gets its line", any("Got tools for x" in l for l in lines))

ui = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("the window doesn't file an unknown tool as reading a file", "_toolIconKey(name) || 'other'" in ui)
check("the window shows the step's own words", "ev.words" in ui and "_words" in ui)

print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
