"""docs/TOOLS.md counts and lists Inglen's own tools, never a workspace plugin.

A plugin file in the workspace loads as a tool on that PC only; generating the
doc there used to count and list it as a built-in.

    .\\.venv\\Scripts\\python.exe tests\\test_tools_doc.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ws = tempfile.mkdtemp(prefix="hearth-tools-doc-")
os.makedirs(os.path.join(ws, "plugins"))
with open(os.path.join(ws, "plugins", "zz_local_probe.py"), "w", encoding="utf-8") as f:
    f.write('TOOL = {"name": "zz_local_probe", "description": "A stand-in plugin.", '
            '"parameters": {"type": "object", "properties": {}}}\n\n'
            'def run(args):\n    return "ok"\n')
os.environ["HEARTH_WORKSPACE"] = ws

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import hearth                                  # noqa: E402
import gen_tools_doc                           # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


names = {d["name"] for d in hearth.TOOL_DEFINITIONS}
check("the stand-in plugin loaded as a tool", "zz_local_probe" in names)
out = os.path.join(ws, "TOOLS.md")
gen_tools_doc.main(out)
text = open(out, encoding="utf-8").read()
check("the plugin isn't listed", "zz_local_probe" not in text)
m = re.search(r"\*\*(\d+) tools\*\*", text)
check("the count leaves it out", m and int(m.group(1)) == len(names) - 1)
listed = re.findall(r"^\| `([a-z0-9_]+)`", text, re.M)
check("the count matches the rows", m and int(m.group(1)) == len(listed))

shutil.rmtree(ws, ignore_errors=True)
print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
