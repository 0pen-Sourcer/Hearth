"""grep_search through ripgrep.

The pattern went to ripgrep as a bare argument, so one starting with "-" was
read as an option, and ripgrep's --pre=PROGRAM runs a program on every file it
searches. An error (a bad regex) was reported as "(no matches)", and output
was decoded in the Windows locale, garbling non-English matches.

A stand-in rg records what it is given and answers like the real one.

    .\\.venv\\Scripts\\python.exe tests\\test_grep_search.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="hearth-grep-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T                                        # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


BIN = tempfile.mkdtemp(prefix="hearth-fake-rg-")
LOG = os.path.join(BIN, "argv.json")
FAKE = os.path.join(BIN, "fake_rg.py")
with open(FAKE, "w", encoding="utf-8") as f:
    f.write(f'''import json, sys
args = sys.argv[1:]
json.dump(args, open({LOG!r}, "w"))
pat = args[args.index("-e") + 1] if "-e" in args else next(a for a in args if not a.startswith("-") and not a.isdigit())
if pat == "(":
    sys.stderr.write("regex parse error: unclosed group")
    sys.exit(2)
sys.stdout.buffer.write("notes.txt:1:नमस्ते café\\n".encode("utf-8"))
''')
if sys.platform == "win32":
    rg = os.path.join(BIN, "rg.bat")
    with open(rg, "w") as f:
        f.write(f'@"{sys.executable}" "{FAKE}" %*\n')
else:
    rg = os.path.join(BIN, "rg")
    with open(rg, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    os.chmod(rg, 0o755)
_which = shutil.which
shutil.which = lambda name, *a, **k: rg if name == "rg" else _which(name, *a, **k)

T.execute_tool("grep_search", {"pattern": "--pre=calc.exe", "path": WS})
args = json.load(open(LOG))
i = args.index("--pre=calc.exe")
check(f"a pattern that looks like an option is passed as the pattern: {args}",
      i > 0 and args[i - 1] == "-e")
check("and the path cannot be read as one either", "--" in args and args.index("--") > i)

out = T.execute_tool("grep_search", {"pattern": "(", "path": WS})
check(f"a search ripgrep refuses reports why, not '(no matches)': {out[:80]!r}",
      "Error" in out and "unclosed" in out)

out = T.execute_tool("grep_search", {"pattern": "hello", "path": WS})
check("matches in any script come back as they are", "नमस्ते café" in out)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all grep_search checks passed")
