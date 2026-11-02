"""Every data file a module in hearth/ reads beside itself goes into the bundle:
the top-level DATAS entries of Hearth.spec are evaluated and compared with what
is in the folder. Runs nothing from PyInstaller.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_spec_data.py
"""
from __future__ import annotations

import ast
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

src = open(os.path.join(ROOT, "Hearth.spec"), encoding="utf-8").read()
node = next(n for n in ast.parse(src).body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DATAS")
# Only the entries built from globs and joins: the rest name fixed files.
kept = [e for e in node.value.elts if isinstance(e, ast.Starred)]
datas = []
for e in kept:
    datas += eval(compile(ast.Expression(e.value), "spec", "eval"),
                  {"glob": glob, "os": os, "REPO_ROOT": ROOT})
bundled = {os.path.normcase(p) for p, _dest in datas}

want = glob.glob(os.path.join(ROOT, "hearth", "*.json"))
missing = [os.path.basename(p) for p in want if os.path.normcase(p) not in bundled]
ok = bool(want) and not missing
print(("ok    " if ok else "FAIL  ") + f"hearth/*.json in the bundle ({len(want)} files, missing {missing})")
sys.exit(0 if ok else 1)
