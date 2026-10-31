"""Hearth's environment variables are HEARTH_*, and the old JARVIS_* still work.

JARVIS is the default name of the agent, which can be renamed; the variables
belong to the framework. Every read goes through hearth.env, which takes the
HEARTH_ name first and falls back to the JARVIS_ one, so existing setups and
scripts keep working.

    .\\.venv\\Scripts\\python.exe tests\\test_env_names.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import env                                               # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


for k in ("HEARTH_T1", "JARVIS_T1", "HEARTH_T2", "JARVIS_T2"):
    os.environ.pop(k, None)
os.environ["JARVIS_T1"] = "old"
check("a setting under the old JARVIS_ name is still read", env.get("T1") == "old")
os.environ["HEARTH_T1"] = "new"
check("the HEARTH_ name wins when both are set", env.get("T1") == "new")
check("an unset setting gives the default", env.get("T2", "d") == "d")
env.set("T2", 5)
check("a write sets both names, so nothing reading the old one drifts",
      os.environ.get("HEARTH_T2") == "5" and os.environ.get("JARVIS_T2") == "5")
env.pop("T2")
check("and a removal clears both", "HEARTH_T2" not in os.environ and "JARVIS_T2" not in os.environ)

direct = re.compile(r"""os\.(?:environ(?:\.get|\.setdefault|\.pop)?\s*[\[(]|getenv\()\s*["']JARVIS_""")
offenders = []
for f in glob.glob(os.path.join(ROOT, "hearth", "*.py")) + [os.path.join(ROOT, "hearth_cli.py")]:
    for n, ln in enumerate(open(f, encoding="utf-8"), 1):
        if direct.search(ln):
            offenders.append(f"{os.path.relpath(f, ROOT)}:{n}")
check(f"no module reads a JARVIS_ variable directly {offenders[:5]}", not offenders)

# A function that assigns or imports a local named _env makes every _env.get()
# in that function refer to the local, so the reads above it crash with
# UnboundLocalError. Loading a model failed that way.
import ast
shadowed = []
for f in glob.glob(os.path.join(ROOT, "hearth", "*.py")) + [os.path.join(ROOT, "hearth_cli.py")]:
    src = open(f, encoding="utf-8").read()
    if "env as _env" not in src:
        continue
    for fn in ast.walk(ast.parse(src)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            binds = (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == "_env") or \
                    (isinstance(n, (ast.Import, ast.ImportFrom)) and any((a.asname or a.name) == "_env" for a in n.names))
            if binds:
                shadowed.append(f"{os.path.relpath(f, ROOT)}:{n.lineno} in {fn.name}")
check(f"no function rebinds _env over the settings module {sorted(set(shadowed))[:5]}", not shadowed)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all environment name checks passed")
