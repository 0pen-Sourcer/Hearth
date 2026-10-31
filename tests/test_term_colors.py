"""The terminal wears the look saved in the window: its accent, statuses, tool
names, warnings and pickers are shades of that look, for every look. Errors and
diff lines keep their own red and green.

    .\\.venv\\Scripts\\python.exe tests\\test_term_colors.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import importlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-term-colors-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import look_icon  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def code(s):
    m = re.search(r"38;5;(\d+)m", s)
    return int(m.group(1)) if m else None


import hearth.term_render as TR  # noqa: E402
import hearth.term_pick as TP  # noqa: E402

for theme, accent, key in (("dark", "sage", "sage"), ("dark", "rose", "rose"), ("light", "ocean", "ocean"),
                           ("classic", "rose", "classic")):
    look_icon.saved = lambda t=theme, a=accent: (t, a)
    importlib.reload(TR)
    importlib.reload(TP)
    shades = set(TR.GRADS[key])
    check(f"{theme}/{accent}: accent, ok, warning and code are its shades",
          all(code(c) in shades for c in (TR.ACCENT, TR.OK, TR.WARN, TR.CODE)))
    if TP.STYLE is not None:
        sel = dict(TP.STYLE.style_rules).get("selected-option", "")
        check(f"{theme}/{accent}: the picker's chosen row is one of its shades ({sel})",
              any(TR.xterm_hex(n) in sel for n in shades))

check("errors stay red", code(TR.ERR) == 203)
check("xterm colours convert to hex", TR.xterm_hex(114) == "#87d787" and TR.xterm_hex(240) == "#585858"
      and TR.xterm_hex(9) == "#ff0000")

look_icon.saved = lambda: ("dark", "sage")
importlib.reload(TR)
import hearth_cli as H  # noqa: E402
sage = set(TR.GRADS["sage"])
check("the CLI's tool names, ok, warnings and frames are the look's shades",
      all(code(c) in sage for c in (H.C_TOOL, H.C_OK, H.C_WARN, H.C_FRAME, H.C_ACCENT)))
cli = H.JarvisCLI.__new__(H.JarvisCLI)
cli.current_model = ""
check("'no model' is said in the look's colour, not red", code(cli._model_label()) in sage)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all terminal colour checks passed")
