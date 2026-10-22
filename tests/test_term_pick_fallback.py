"""The terminal's pickers work without prompt_toolkit.

A build that didn't carry prompt_toolkit crashed on /models ("No module named
'prompt_toolkit'"): the picker imported it at the top. Without it the picker is
a numbered list and a typed number.

    .\\.venv\\Scripts\\python.exe tests\\test_term_pick_fallback.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


CHILD = f"""
import asyncio, sys
sys.modules['prompt_toolkit'] = None          # as in a build without it
sys.path.insert(0, {ROOT!r})
from hearth import term_pick
sys.stdin.isatty = lambda: True
print('HAVE', term_pick.HAVE_PT)
got = asyncio.run(term_pick.pick('Pick a model', [('a', 'Qwen 9B'), ('b', 'Ornith 9B')], default='a'))
print('GOT', got)
"""
r = subprocess.run([sys.executable, "-c", CHILD], input="2\n", capture_output=True, text=True, timeout=60)
out = r.stdout
check(f"the picker module loads without prompt_toolkit (exit {r.returncode}) {r.stderr[-200:]}",
      r.returncode == 0 and "HAVE False" in out)
check("it lists the choices with numbers", " 1. Qwen 9B" in out and " 2. Ornith 9B" in out and "(now)" in out)
check("and a typed number picks one", "GOT b" in out)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all terminal picker fallback checks passed")
