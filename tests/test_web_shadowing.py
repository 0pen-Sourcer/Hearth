"""No request handler binds a name the web module already has.

`from . import listen as _listen` inside do_POST (for the mic test) made
`_listen` local to the whole method, so every other voice setting that reached
`if _listen:` crashed with UnboundLocalError. It had happened before; this keeps
it from coming back under any name.

    .\\.venv\\Scripts\\python.exe tests\\test_web_shadowing.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


module = {n for n in vars(web) if n.startswith("_")}
for name in ("do_GET", "do_POST", "do_DELETE"):
    f = getattr(web.HearthHandler, name, None)
    if f is None:
        continue
    shadowed = sorted(set(f.__code__.co_varnames) & module)
    check(f"{name} doesn't bind a module-wide name locally {shadowed}", not shadowed)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all shadowing checks passed")
