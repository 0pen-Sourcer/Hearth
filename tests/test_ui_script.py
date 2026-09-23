"""The window is one file, and nothing checks it before it ships.

A stray bracket anywhere in it takes the whole interface down to a blank page,
and there is no import to fail and no test that would have noticed. So: parse
the script, and check the two things that silently do nothing rather than
break loudly.

An element fetched by an id that no markup defines returns null, and the code
around it is written defensively enough that a control simply never works. A
duplicated id is worse, because the first one wins and the second looks wired.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_script.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "hearth", "ui.html")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


SRC = open(UI, encoding="utf-8").read()

# The page carries one real script; the other match is a comment that mentions
# the tag. Size tells them apart without guessing at the comment's wording.
BLOCKS = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", SRC)
MAIN = max(BLOCKS, key=len) if BLOCKS else ""
check("the window has a script in it", len(MAIN) > 10000)

# ------------------------------------------------------------------ it parses
node = shutil.which("node")
if node:
    path = os.path.join(tempfile.gettempdir(), "hearth_ui_check.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(MAIN)
    r = subprocess.run([node, "--check", path], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[:1200])
    check("the script parses", r.returncode == 0)
else:
    print("skip  no node on this machine, parse check not run")

# --------------------------------------------------------------- ids line up
IDS = re.findall(r'\bid\s*=\s*"([^"]+)"', SRC)
dupes = sorted({i for i in IDS if IDS.count(i) > 1})
check("no id is defined twice" + (": " + ", ".join(dupes) if dupes else ""),
      not dupes)

DEFINED = set(IDS)
# Only the literal lookups. Anything built from a variable is out of reach here
# and is not what this is guarding against.
looked_up = set(re.findall(r"""\$\(\s*['"]([A-Za-z][\w-]*)['"]\s*\)""", MAIN))
looked_up |= set(re.findall(
    r"""getElementById\(\s*['"]([A-Za-z][\w-]*)['"]\s*\)""", MAIN))
# Ids the script creates at runtime rather than finding in the markup.
created = set(re.findall(r"""\.id\s*=\s*['"]([\w-]+)['"]""", MAIN))
created |= set(re.findall(r"""id=["']([\w-]+)["']""",
                          "".join(re.findall(r"innerHTML\s*=\s*([\s\S]{0,4000}?);\n", MAIN))))
missing = sorted(looked_up - DEFINED - created)
check("every id the script reaches for exists"
      + (": " + ", ".join(missing) if missing else ""), not missing)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all window script checks passed")
