"""Typing into a field doesn't wipe what's already in it.

desktop_type set a control's whole value: right for a search box, and in a live
test on Notepad it rewrote the document to add one line. On a long document that
loses everything else. Now 'replace' and 'append' can be asked for, and unasked a
field holding more than a short line is added to.

    .\\.venv\\Scripts\\python.exe tests\\test_desktop_type_mode.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth.desktop_a11y import _typed_value  # noqa: E402
from hearth.tools import TOOL_DEFINITIONS  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


doc = "Dear team,\nthe launch moves to Friday.\nThanks"
check("a document isn't wiped by a line typed into it",
      _typed_value(doc, "\nPS: bring snacks", None) == (doc + "\nPS: bring snacks", "added to the end"))
check("a long single line is added to as well", _typed_value("x" * 300, "y", None)[1] == "added to the end")
check("a search box is replaced", _typed_value("old query", "invoices", None) == ("invoices", "replaced"))
check("replace when asked, even a document", _typed_value(doc, "new text", "replace") == ("new text", "replaced"))
check("append when asked, even a short field", _typed_value("abc", "def", "append") == ("abcdef", "added to the end"))
schema = next(t for t in TOOL_DEFINITIONS if t["name"] == "desktop_type")["parameters"]["properties"]
check("the tool offers the choice", schema.get("mode", {}).get("enum") == ["replace", "append"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all typing mode checks passed")
