"""The tray menu builds its chat rows without pystray refusing them, and a row
opens its own chat.

    .\\.venv\\Scripts\\python.exe tests\\test_tray_menu.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    import pystray
    from PIL import Image
except ImportError:
    print("skip: pystray not installed")
    sys.exit(0)

from hearth import tray  # noqa: E402

opened = []
tray.open_chat = lambda cid: opened.append(cid)
chats = [{"id": "c1", "title": "Plan the YC application", "pinned": True},
         {"id": "c2", "title": "x" * 80}, {"id": "c3", "title": ""}]
err = ""
items = []
try:
    items = [tray.chat_menu_item(c) for c in chats]
    menu = pystray.Menu(lambda: items)
    icon = pystray.Icon("t", Image.new("RGBA", (16, 16)), "t", menu)
    icon.update_menu()
except Exception as e:
    err = f"{type(e).__name__}: {e}"
check(f"chat rows build into a live menu ({err or 'no error'})", not err and len(items) == 3)
if items:
    items[1](None)
    check(f"a row opens its own chat, not the last one built ({opened})", opened == ["c2"])
    check(f"long titles are cut and empty ones named ({items[1].text!r}, {items[2].text!r})",
          len(items[1].text) <= 40 and items[2].text == "Untitled")

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall tray menu checks passed")
