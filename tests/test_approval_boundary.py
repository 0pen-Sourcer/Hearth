"""The model must not be able to approve its own call.

Tool arguments arrive as a dict the model fills in, and the tool layer reads
one key out of that same dict as control rather than as an argument. While
approval was a truthiness test, a model could clear the destructive guard by
adding one key, and text it read from a page, a file or a tool result could
tell it to. That only has to work once, on the surface where nobody can see a
prompt to say no.

    .\\.venv\\Scripts\\python.exe tests\\test_approval_boundary.py
"""
from __future__ import annotations

import os
# What these calls do is a test, not something the agent did: keep it out of
# the real activity log.
os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T                                       # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


REFUSED = "refused to run a destructive command"
CMD = r"Remove-Item C:\hearth-test-path-that-does-not-exist -Recurse -Force"


def _run(extra):
    return T.execute_tool("run_command", dict({"command": CMD}, **extra))


# Anything the model could plausibly write must not clear the guard.
for _guess in (True, 1, "1", "yes", "true", "approved", T.APPROVAL_TOKEN[:16],
               "hearth-approved-", "hearth-approved-x"):
    check(f"a model-supplied approval of {_guess!r} does not clear the guard",
          REFUSED in _run({"_approved": _guess}))

check("with no approval at all it is still refused", REFUSED in _run({}))

# The refusal only reaches the model where nobody can approve (a phone bridge,
# MCP), so a yes typed there can't clear it. Telling it to wait for one made it
# ask, hear yes, retry and get refused again.
_said = _run({})
check(f"the refusal says a yes here can't run it, and where it can ({_said[-160:]!r})",
      "even if they say yes" in _said and "window or terminal" in _said
      and "yes do it" not in _said and "AUTO_APPROVE" not in _said)

# The trusted path has to keep working, or the guard just blocks everyone.
check("the in-process token does clear it", REFUSED not in _run({"_approved": T.APPROVAL_TOKEN}))

check("the token is not a constant an attacker could read from the source",
      len(T.APPROVAL_TOKEN) > 24 and T.APPROVAL_TOKEN.startswith("hearth-approved-"))

# The strip is the second line: control keys never reach the tool layer.
check("control keys are dropped from model arguments",
      T.strip_control_args({"command": "x", "_approved": True}) == {"command": "x"})
check("ordinary arguments survive the strip",
      T.strip_control_args({"command": "x", "cwd": "y"}) == {"command": "x", "cwd": "y"})
check("a non-dict passes through untouched", T.strip_control_args("nope") == "nope")

# No advertised parameter starts with an underscore, so the strip cannot eat a
# real argument.
_named = [(t["name"], k) for t in T.TOOL_DEFINITIONS
          for k in ((t.get("parameters") or {}).get("properties") or {})
          if k.startswith("_")]
check("no advertised parameter starts with an underscore, " + (str(_named) or ""), not _named)

# Both surfaces that turn model output into arguments must scrub it, and both
# that mark a call approved must use the token rather than a literal.
for _f in ("hearth/headless.py", "hearth_cli.py"):
    _src = open(os.path.join(ROOT, _f), encoding="utf-8").read()
    check(f"{_f} scrubs model arguments", "strip_control_args" in _src)
    check(f"{_f} does not approve with a literal",
          not re.search(r"_approved\s*=\s*True", _src))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all approval boundary checks passed")
