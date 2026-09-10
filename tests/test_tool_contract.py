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

# check_rejection_path calls every tool with empty arguments, which would write
# a call and an error into the real activity log for each one. That log is read
# back to work out which tools actually get used, and a handful of runs is
# enough to drown the real numbers. Set before hearth is imported.
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"


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
    inside a plain function.

    The scan covers the top-level modules too. It did not, and hearth_cli.py is
    both the longest file and the one most often spliced into, so the check was
    blind to the place the break is most likely to recur."""
    out = []
    paths = sorted(glob.glob(os.path.join(ROOT, "hearth", "*.py")))
    paths += [p for p in sorted(glob.glob(os.path.join(ROOT, "*.py")))
              if os.path.basename(p) != "conftest.py"]
    for path in paths:
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
    """Tool names are repeated as bare literals across several modules, and a
    rename or a typo leaves one pointing at a tool that does not exist. A stale
    name in a risk list is the dangerous direction: the tool stops being gated
    and nothing says so."""
    hearth, tools = _reg()
    known = {t["name"] for t in hearth.TOOL_DEFINITIONS}
    out = []
    lists = [("tools." + label, getattr(tools, label, set()) or set())
             for label in ("_RISKY_TOOLS", "_DEFERRED_TOOLS", "_NEVER_PERM")]
    # The same names are also listed in the modules that decide what to show,
    # what to re-confirm, and what a sub-agent may not touch.
    for mod, label in (("activity_hud", "DESKTOP_TOOLS"),
                       ("reminders", "_CONFIRM_ON_FIRE"),
                       ("subagents", "_DANGEROUS_FOR_SUBAGENTS")):
        try:
            m = __import__(f"hearth.{mod}", fromlist=[mod])
            lists.append((f"{mod}.{label}", getattr(m, label, set()) or set()))
        except Exception as e:
            out.append(f"could not read {mod}.{label}: {type(e).__name__}: {e}")
    for label, names in lists:
        for n in sorted(names):
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


def check_dryrun_covers_side_effects() -> list:
    """A benchmark runs in dry-run so it can measure which tool a model picks
    without moving the real mouse or writing real files. Any tool that acts on
    the machine has to be in that set, or a benchmark drives the desktop for
    real. A batch tool is the easy one to forget, since it looks like a wrapper
    rather than an action."""
    hearth, tools = _reg()
    guarded = getattr(tools, "_DRYRUN_GUARDED", set()) or set()
    known = {t["name"] for t in hearth.TOOL_DEFINITIONS}
    out = [f"_DRYRUN_GUARDED names '{n}', which is not a registered tool"
           for n in sorted(guarded) if n not in known]
    # Everything gated for permission acts on the machine by definition. The
    # exceptions read rather than write.
    read_only = {"read_inbox", "disk_usage", "launch_team"}
    for n in sorted(getattr(tools, "_RISKY_TOOLS", set()) or set()):
        if n in known and n not in guarded and n not in read_only:
            out.append(f"{n} is gated for permission but not dry-run guarded")
    return out


CHECKS = [
    ("every advertised tool has a callable handler", check_handlers),
    ("acting tools are dry-run guarded", check_dryrun_covers_side_effects),
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
