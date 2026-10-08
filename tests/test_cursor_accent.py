"""The cursor Hearth draws where it clicks takes the accent of the look in use.

Before, both the desktop ring and the browser dot were always the same orange,
whatever look and accent were picked."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-cursor-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import web, browse, agent_cursor      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


web._save_settings(dict(web._load_settings(), ui_theme="dark", ui_accent="ocean"))
check(f"on Dark with the ocean accent, the browser cursor is blue {browse._cursor_js().count('91,147,194')}",
      browse._cursor_js().count("91,147,194") == 3)
check(f"and so is the desktop ring {agent_cursor._accent()}", agent_cursor._accent() == (0x5B, 0x93, 0xC2))
from hearth import activity_hud                   # noqa: E402
check(f"the screenshot dots and the activity pill's dot follow it too {hex(activity_hud._accent())}",
      activity_hud._accent() == (0xC2 << 16 | 0x93 << 8 | 0x5B))
web._save_settings(dict(web._load_settings(), ui_theme="classic"))
check(f"on Classic it is Classic's violet {agent_cursor._accent()}", agent_cursor._accent() == (0x8B, 0x5C, 0xF6))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all cursor accent checks passed")
