"""Finding a tool that isn't loaded by default.

Most tools are held back to keep the prompt small, and the model reaches them
by describing what it needs. The matcher scored a query token against a
description with a plain substring test, so a short word landed inside a longer
one: "all" hit install, finally and usually, and "am" hit a voice named
am_michael. A query that matched nothing came back with a handful of unrelated
tools under "you can call them now", which the model then chose from, and whose
schemas it paid for in context for the rest of the session.

The second half is the menu the model reads. It is rebuilt at import from the
tools that are actually held back, so it cannot name one that does not exist,
and this checks that it still is: the fallback literal underneath it had gone
stale enough to offer a battery reading nothing produced.

    .\\.venv\\Scripts\\python.exe tests\\test_tool_discovery.py
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T                                       # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def ask(query):
    T.reset_unlocked_tools()
    return [d["name"] for d in T.unlock_tools(query)]


# ------------------------------------------------- a query that means nothing
for q in ("asdfgh qwerty", "am i on battery", "zzzzz", "hmm ok then"):
    got = ask(q)
    check(f"{q!r} unlocks nothing rather than something unrelated: {got[:3]}",
          not got)

# The exact shape of the old bug: a filler word matching inside longer words.
got = ask("show me all of it")
check(f"'all' inside a sentence is filler, not a match for install: {got[:3]}",
      not got)

# ------------------------------------------------- a query that means something
WANTED = [
    ("image generation", "generate_image"),
    ("make a picture", "generate_image"),
    ("draw me a photo", "generate_image"),
    ("zip archive", "extract_archive_file"),
    ("write my own tool", "create_plugin"),
    ("soul", "edit_soul"),
    ("network", "network_info"),
    ("wifi", "network_info"),
    ("whats my ip", "network_info"),
    ("installed apps", "list_installed_apps"),
    ("pick a different voice", "set_voice"),
    ("run several agents at once", "launch_team"),
    ("send an email", "send_email"),
    ("summarize a huge pdf", "read_pdf_large"),
    ("end the session", "end_session"),
]
for q, want in WANTED:
    got = ask(q)
    check(f"{q!r} finds {want}", want in got)

check("a vague query does not flood the context", all(len(ask(q)) <= 10
                                                      for q, _ in WANTED))

# 'all' on its own is the documented way to ask for everything, and it still is.
check("'all' on its own still means everything", len(ask("all")) > 30)
check("an empty query means the same", len(ask("")) > 30)
T.reset_unlocked_tools()

# ------------------------------------- what the catalogue offers is reachable
# The menu is rebuilt at import from the tools that are actually held back, so
# it cannot name one that does not exist. Check that it did rebuild, and that
# every tool it lists can in fact be asked for by name.
schema = T._LOAD_TOOLS_SCHEMA["function"]["description"]
check("the menu was built from the real deferred set", "by group:" in schema)
listed = set(re.findall(r"[a-z][a-z0-9_]{3,}", schema.split("by group:")[-1]))
known = {t["name"] for t in T.TOOL_DEFINITIONS}
named = sorted(n for n in listed if n in known)
check(f"the menu names tools that exist ({len(named)} of them)", len(named) > 10)
unreachable = [n for n in named if n in T._DEFERRED_TOOLS and not ask(n)]
check("and every one of them can be asked for by name"
      + (": " + ", ".join(unreachable) if unreachable else ""), not unreachable)

# The reading itself, on a machine that has one. This box is a desktop, so the
# value is supplied rather than waited for.
import psutil                                                       # noqa: E402

_real = psutil.sensors_battery
_B = collections.namedtuple("_B", "percent secsleft power_plugged")
psutil.sensors_battery = lambda: _B(63.4, 4200, False)
try:
    info = json.loads(T.execute_tool("system_info", {}))
finally:
    psutil.sensors_battery = _real
check("a laptop gets a charge level", info.get("battery_percent") == 63)
check("and is told whether it is plugged in", info.get("on_ac_power") is False)
check("and roughly how long it has", info.get("battery_hours_left") == 1.2)

psutil.sensors_battery = lambda: None
try:
    info = json.loads(T.execute_tool("system_info", {}))
finally:
    psutil.sensors_battery = _real
check("a desktop is not given an imaginary battery",
      "battery_percent" not in info)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all tool discovery checks passed")
