"""One yes to using the computer covers a turn's desktop steps; the rest still ask.

    .\\.venv\\Scripts\\python.exe tests\\test_computer_one_yes.py
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, ROOT)
os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
import _home  # noqa: E402,F401

from hearth import approval, desktop_session as ds, tools as T  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


began = []
ds_begin, ds.begin = ds.begin, lambda name, args=None: began.append(name)
sends = T.sends_out
T.sends_out = lambda name, args=None: "after web" if (args or {}).get("leak") else ""


def run(answer):
    asked = []

    def check_(name, args):
        asked.append(name)
        return answer

    return ds._check_wrap(check_, None, {"id": None}), asked


try:
    c, asked = run("allow")
    out = [c("open_app", {"name": "notepad"}), c("desktop_click", {"idx": 0}),
           c("computer_type", {"text": "x"}), c("execute_actions", {"actions": []})]
    check("the first desktop step asks, the rest don't", asked == ["open_app"] and out == ["allow"] * 4)
    check("the session starts before the question, so the panel can hold it", began[:1] == ["open_app"])

    asked.clear()
    c("delete_path", {"path": "x"})
    check("a yes to the computer isn't a yes to anything else", asked == ["delete_path"])
    c("open_url", {"url": "https://example.com"})
    check("a web page has its own question", asked[-1] == "open_url")
    asked.clear()
    c("computer_type", {"text": "secret", "leak": True})
    check("a step that could send data out still asks", asked == ["computer_type"])

    c, asked = run("deny")
    c("desktop_click", {"idx": 0})
    c("desktop_click", {"idx": 1})
    check("a no isn't remembered as a yes", asked == ["desktop_click", "desktop_click"])

    c, asked = run({"decision": "always"})
    c("desktop_click", {"idx": 0})
    c("desktop_click", {"idx": 1})
    check("Always counts as the yes", asked == ["desktop_click"])

    asked = []

    async def check_async(name, args):
        asked.append(name)
        return "allow"

    c = ds._check_wrap(check_async, None, {"id": None})

    async def both():
        return [await c("desktop_click", {"idx": 0}), await c("computer_key", {"key": "a"})]

    check("the same for a turn that asks asynchronously",
          asyncio.run(both()) == ["allow", "allow"] and asked == ["desktop_click"])
finally:
    ds.begin = ds_begin
    T.sends_out = sends

check("the question says the whole of it",
      approval.card("desktop_click", {})["what"] == "use your computer for this"
      and "use your computer" in approval.card("open_app", {"name": "notepad"})["what"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all one-yes checks passed")
