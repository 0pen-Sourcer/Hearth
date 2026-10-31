"""Every slash command that exists is listed, and every listed one exists.

The terminal's /help and its autocomplete, and the window's / menu, were kept
by hand next to the code that handles the commands, and had drifted: about
twenty terminal commands were missing from autocomplete and several from
/help. These checks read the lists and the handlers and compare them.

    .\\.venv\\Scripts\\python.exe tests\\test_slash_commands.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------------------ terminal
cli = open(os.path.join(ROOT, "hearth_cli.py"), encoding="utf-8").read()
handled = set(re.findall(r'low(?:\s*==\s*|\.startswith\(\s*)"(/[a-z][\w-]*)', cli))
for grp in re.findall(r"low\s+in\s+\(([^)]*)\)", cli):
    handled |= set(re.findall(r'"(/[a-z][\w-]*)"', grp))
i = cli.index("SLASH_COMMANDS = {")
auto = set(re.findall(r'"(/[a-z][\w-]*)":', cli[i:cli.index("\n}\n", i)]))
# /help prints HELP_GROUPS; another name for a listed command is in its description.
h = cli.index("HELP_GROUPS = [")
listed_in_help = set(re.findall(r'"(/[a-z][\w-]*)"', cli[h:cli.index("\n]\n", h)])) | {"/help"}
listed_in_help |= {c for c, d in re.findall(r'"(/[a-z][\w-]*)":\s*"([^"]*)"', cli[i:cli.index("\n}\n", i)])
                   if d.startswith("same as /")}

check(f"every terminal command in autocomplete is handled {sorted(auto - handled)}", not (auto - handled))
check(f"every handled terminal command is in autocomplete {sorted(handled - auto)}", not (handled - auto))
check(f"every terminal command appears in /help {sorted(handled - listed_in_help)}", not (handled - listed_in_help))

# ------------------------------------------------------------------ window
ui = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
a = ui.index("async function handleSlash(raw) {")
body = ui[a:ui.index("\n}\n", a)]
cases = set(re.findall(r"case\s+'(/[a-z][\w-]*)'", body))
j = ui.index("const SLASH_COMMANDS = [")
menu = set(re.findall(r"cmd:\s*'(/[a-z][\w-]*)", ui[j:ui.index("];", j)]))
aliases = {"/quit", "/logs", "/onboard", "/usage"}          # named in their main command's description
check(f"every / menu entry is handled in the window {sorted(menu - cases)}", not (menu - cases))
check(f"every window command is in the / menu {sorted(cases - menu - aliases)}", not (cases - menu - aliases))
check("the window's /help is built from the / menu", "SLASH_COMMANDS.map" in body)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all slash command checks passed")
