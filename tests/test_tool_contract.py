"""Contract checks for the tool layer.

A tool's schema and its implementation live in different places with nothing
binding them, so the package imports cleanly even when a tool is unusable. The
break then only appears when a user calls it. These checks close that gap.

    .\.venv\Scripts\python.exe tests\test_tool_contract.py
"""
from __future__ import annotations

import ast
import glob
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _reg():
    import hearth
    from hearth import tools
    return hearth, tools


def check_handlers() -> list:
    """Every advertised tool resolves to something callable."""
    hearth, tools = _reg()
    out = []
    for td in hearth.TOOL_DEFINITIONS:
        name = td.get("name")
        if not name:
            out.append("a tool definition has no name")
            continue
        fn = tools._HANDLERS.get(name)
        if fn is None:
            out.append(f"{name}: advertised but no handler registered")
        elif not callable(fn):
            out.append(f"{name}: handler is {type(fn).__name__}, not callable")
    return out


def check_no_orphaned_methods() -> list:
    """Catch a method that fell out of its class.

    Inserting a function into the middle of a class silently re-parents every
    method below it, which is how the browser worker lost run/call/stop while
    still importing fine. A def whose first parameter is self has no business
    inside a plain function."""
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "hearth", "*.py"))):
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except SyntaxError:
            continue

        def walk(node, inside_class=False, owner="module"):
            for child in getattr(node, "body", []):
                if isinstance(child, ast.ClassDef):
                    walk(child, True, child.name)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = child.args.args
                    first = args[0].arg if args else ""
                    if first == "self" and not inside_class:
                        out.append(
                            f"{os.path.basename(path)}:{child.lineno} "
                            f"{child.name}(self, ...) is defined inside {owner}(), "
                            f"not in a class")
                    walk(child, False, child.name)

        walk(tree)
    return out


def check_name_lists() -> list:
    """The risk/defer/never lists are separate literals; a rename leaves them
    pointing at tools that no longer exist."""
    hearth, tools = _reg()
    known = {t["name"] for t in hearth.TOOL_DEFINITIONS}
    out = []
    for label in ("_RISKY_TOOLS", "_DEFERRED_TOOLS", "_NEVER_PERM"):
        for n in sorted(getattr(tools, label, set()) or set()):
            if n not in known:
                out.append(f"{label} names '{n}', which is not a registered tool")
    return out


def check_required_described() -> list:
    hearth, _ = _reg()
    out = []
    for td in hearth.TOOL_DEFINITIONS:
        params = td.get("parameters") or {}
        props = params.get("properties") or {}
        for req in params.get("required") or []:
            spec = props.get(req)
            if spec is None:
                out.append(f"{td['name']}: '{req}' is required but absent from properties")
            elif not (spec.get("description") or "").strip():
                out.append(f"{td['name']}: required '{req}' has no description")
    return out


def check_rejection_path() -> list:
    """A call missing a required field must come back as a plain refusal, not a
    raised exception the model reads as an internal fault."""
    hearth, tools = _reg()
    out = []
    for td in hearth.TOOL_DEFINITIONS:
        name = td["name"]
        if not ((td.get("parameters") or {}).get("required")):
            continue
        try:
            res = tools.execute_tool(name, {})
        except Exception as e:
            out.append(f"{name}: raised {type(e).__name__} instead of refusing: {e}")
            continue
        if not isinstance(res, str) or not res.strip():
            out.append(f"{name}: returned {type(res).__name__}, expected a message")
    return out


def check_serialisable() -> list:
    hearth, _ = _reg()
    try:
        json.dumps(hearth.TOOL_DEFINITIONS)
        return []
    except Exception as e:
        return [f"TOOL_DEFINITIONS is not JSON-serialisable: {e}"]


CHECKS = [
    ("every advertised tool has a callable handler", check_handlers),
    ("no method orphaned outside its class", check_no_orphaned_methods),
    ("risk/defer lists only name real tools", check_name_lists),
    ("required parameters are described", check_required_described),
    ("a missing required field is refused, not raised", check_rejection_path),
    ("schemas serialise", check_serialisable),
]


def main() -> int:
    import hearth
    print(f"{len(hearth.TOOL_DEFINITIONS)} tools advertised\n")
    failed = 0
    for label, fn in CHECKS:
        problems = fn()
        if problems:
            failed += 1
            print(f"FAIL  {label}")
            for p in problems[:12]:
                print(f"        {p}")
            if len(problems) > 12:
                print(f"        ... and {len(problems) - 12} more")
        else:
            print(f"ok    {label}")
    print()
    print(f"{failed} check(s) failed" if failed else "all tool-contract checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
