"""Appearance says what is so: a new install opens in Dark with Rose, so the
Classic card no longer calls itself the default.

    python tests/test_appearance_words.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


classic = re.search(r'data-mode="classic".*?<small>(.*?)</small>', html, re.S)
check(f"the Classic card doesn't call itself the default ({classic.group(1) if classic else None!r})",
      classic and "default" not in classic.group(1).lower())
check("a new install opens in Dark", "localStorage.getItem('hearth_theme') || 'dark'" in html)
check("with Rose", "localStorage.getItem('hearth_accent') || 'rose'" in html)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all appearance word checks passed")
