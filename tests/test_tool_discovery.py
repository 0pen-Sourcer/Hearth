"""Finding a tool that isn't loaded by default.

Most tools are held back to keep the prompt small. The model reaches one by
naming it, so it has to be able to see every name: the menu showed five per
group and hid the rest behind "...", which left 23 of 64 tools reachable only
by guessing a keyword that happened to hit them, the window, click and team
tools among them. Thirty built-in tools also had no group at all and were
filed under "Custom / plugins", beside the image generator.

Picking by name is the main path and resolves exactly. The keyword search is a
fallback for when the model is unsure of the name, and is not what this holds
to a standard.

    .\\.venv\\Scripts\\python.exe tests\\test_tool_discovery.py
"""
from __future__ import annotations

import collections
import json
import os
# What these calls do is a test, not something the agent did: keep it out of
# the real activity log.
os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
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


# Tools from this PC's own plugin files aren't Hearth's, wherever the test runs.
from hearth import plugins as _plugins  # noqa: E402
BUILTIN = {t["name"] for t in T.TOOL_DEFINITIONS} - _plugins._plugin_names(T.WORKSPACE)
DEFERRED = sorted(T._DEFERRED_TOOLS & BUILTIN)
MENU = T._LOAD_TOOLS_SCHEMA["function"]["description"]

# ------------------------------------------------------ every name is visible
check("the menu was built from the real deferred set", "by group:" in MENU)
hidden = [n for n in DEFERRED if n not in MENU]
check(f"every one of the {len(DEFERRED)} deferred tools is named in the menu"
      + (f", missing {hidden}" if hidden else ""), not hidden)
check("nothing is hidden behind an ellipsis", "..." not in MENU)
check(f"and naming them all stays cheap ({len(MENU) // 4} tokens)", len(MENU) // 4 < 600)

# --------------------------------------------------- and every tool has a home
uncategorised = sorted(n for n in BUILTIN if n not in T._TOOL_CATEGORY)
check("every built-in tool has a group, none is filed as a plugin"
      + (f": {uncategorised}" if uncategorised else ""), not uncategorised)
stale = sorted(k for k in T._TOOL_CATEGORY if k not in BUILTIN and k not in T.NOT_ON_THIS_PC)
check("and no group lists a tool that does not exist"
      + (f": {stale}" if stale else ""), not stale)
check("desktop control is its own group",
      T._TOOL_CATEGORY.get("computer_click") == "Desktop control")

# ------------------------------------------------------- choosing by name
check("select: returns exactly what was named",
      ask("select:focus_window,computer_click") == ["focus_window", "computer_click"])
check("an unknown name in a select is skipped, the rest still load",
      ask("select:no_such_tool,launch_team") == ["launch_team"])
check("a select of nothing real loads nothing", ask("select:no_such_tool") == [])
for name in ("list_windows", "read_pdf_large", "launch_team", "get_time"):
    check(f"the bare name {name!r} resolves to that tool alone", ask(name) == [name])
check("a name is not outranked by a description that mentions its words",
      ask("summarize_file") == ["summarize_file"])

# ------------------------------------------------------ the keyword fallback
# Held only to finding the obvious tool among its results, since this is the
# path for a model that is unsure, not the main one.
for q, want in (("image generation", "generate_image"), ("images", "generate_image"),
                ("make a picture", "generate_image"), ("zip archive", "extract_archive_file"),
                ("network", "network_info"), ("send an email", "send_email"),
                ("summarize a huge pdf", "read_pdf_large"), ("soul", "edit_soul")):
    got = ask(q)
    check(f"{q!r} finds {want}", want in got)
check("a keyword never floods the context", len(ask("file")) <= 10)
check("'all' still loads everything", len(ask("all")) > 30)
T.reset_unlocked_tools()

# ------------------------------------- a charge level, which the menu offers
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
psutil.sensors_battery = lambda: None
try:
    info = json.loads(T.execute_tool("system_info", {}))
finally:
    psutil.sensors_battery = _real
check("a desktop is not given an imaginary battery", "battery_percent" not in info)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all tool discovery checks passed")
