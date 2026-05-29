"""Hearth dev_tools — fire-any-tool REPL for testing.

Run:
    python -m hearth.dev_tools

Inside:
    list                       show all tools
    info <name>                show a tool's parameter schema
    call <name> {json args}    invoke the tool, print result
    log                        tail the activity log
    workspace                  show sandbox path
    quit / exit                leave

Examples:
    call get_time
    call read_file {"path": "C:/Windows/win.ini"}
    call grep_search {"pattern": "TODO", "path": ".", "max_matches": 5}
    call note_write {"title": "test", "content": "hello"}
"""

from __future__ import annotations

import os
import sys
import json
import time
from typing import Any, Dict

# Make package importable when run via `python hearth/dev_tools.py` too.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from hearth.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    WORKSPACE,
    LOGS_DIR,
)

C_RESET = "\033[0m"
C_BRAND = "\033[38;5;111m"
C_OK = "\033[38;5;120m"
C_TOOL = "\033[38;5;220m"
C_DIM = "\033[90m"
C_ERR = "\033[1;31m"

ACTIVITY_LOG = os.path.join(LOGS_DIR, "activity.jsonl")


def _enable_ansi():
    if sys.platform == "win32":
        os.system("")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _by_name() -> Dict[str, Dict[str, Any]]:
    return {t["name"]: t for t in TOOL_DEFINITIONS}


def cmd_list() -> None:
    print(f"\n{C_BRAND}Tools ({len(TOOL_DEFINITIONS)}):{C_RESET}")
    by = _by_name()
    for name in sorted(by):
        desc = by[name]["description"]
        print(f"  {C_TOOL}{name:<22}{C_RESET}{C_DIM}{desc[:80]}{C_RESET}")
    print()


def cmd_info(name: str) -> None:
    by = _by_name()
    td = by.get(name)
    if not td:
        print(f"{C_ERR}no such tool: {name}{C_RESET}")
        return
    print(f"\n{C_TOOL}{name}{C_RESET}\n{td['description']}\n")
    print(f"{C_DIM}parameters:{C_RESET}")
    print(json.dumps(td["parameters"], indent=2))
    print()


def cmd_call(name: str, args_text: str) -> None:
    args: Dict[str, Any] = {}
    if args_text.strip():
        try:
            args = json.loads(args_text)
        except json.JSONDecodeError as e:
            print(f"{C_ERR}bad JSON args: {e}{C_RESET}")
            return
    if name not in _by_name():
        print(f"{C_ERR}no such tool: {name}{C_RESET}")
        return
    print(f"{C_TOOL}⚡ {name}{C_RESET} {C_DIM}{json.dumps(args, ensure_ascii=False)[:100]}{C_RESET}")
    t0 = time.time()
    result = execute_tool(name, args)
    dt = (time.time() - t0) * 1000
    print(f"{C_DIM}({dt:.0f}ms, {len(result)} chars){C_RESET}")
    print(result)
    print()


def cmd_log(n: int = 20) -> None:
    if not os.path.exists(ACTIVITY_LOG):
        print(f"{C_DIM}(no activity log yet at {ACTIVITY_LOG}){C_RESET}")
        return
    with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for ln in lines[-n:]:
        try:
            rec = json.loads(ln)
            ts = rec.get("ts", "")
            ev = rec.get("event", "")
            tool = rec.get("tool", "")
            extra = ""
            if ev == "call":
                extra = json.dumps(rec.get("args", {}), ensure_ascii=False)[:80]
            elif ev == "result":
                extra = f"{rec.get('chars', 0)} chars"
            print(f"{C_DIM}{ts}{C_RESET}  {C_TOOL}{ev:<7}{C_RESET}  {tool}  {C_DIM}{extra}{C_RESET}")
        except json.JSONDecodeError:
            print(ln.rstrip())


def repl() -> None:
    _enable_ansi()
    print(f"{C_BRAND}Jarvis dev_tools — REPL{C_RESET}")
    print(f"{C_DIM}workspace: {WORKSPACE}{C_RESET}")
    print(f"{C_DIM}log: {ACTIVITY_LOG}{C_RESET}")
    print(f"{C_DIM}commands: list | info <name> | call <name> {{json}} | log [n] | workspace | quit{C_RESET}\n")
    while True:
        try:
            line = input(f"{C_BRAND}>{C_RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not line:
            continue
        head, _, rest = line.partition(" ")
        head = head.lower()
        if head in ("quit", "exit", "q"):
            return
        if head == "list":
            cmd_list()
        elif head == "info":
            if not rest:
                print(f"{C_ERR}usage: info <tool_name>{C_RESET}")
            else:
                cmd_info(rest.strip())
        elif head == "call":
            tool, _, args_text = rest.partition(" ")
            if not tool:
                print(f"{C_ERR}usage: call <tool_name> {{json args}}{C_RESET}")
            else:
                cmd_call(tool.strip(), args_text)
        elif head == "log":
            n = int(rest.strip()) if rest.strip().isdigit() else 20
            cmd_log(n)
        elif head == "workspace":
            print(f"  {WORKSPACE}")
        else:
            print(f"{C_ERR}unknown: {head}. type 'list' or 'quit'.{C_RESET}")


def _run_argv(argv: list) -> int:
    """Allow one-shot invocation: python -m hearth.dev_tools call get_time"""
    _enable_ansi()
    if not argv:
        repl()
        return 0
    head = argv[0].lower()
    if head == "list":
        cmd_list()
    elif head == "info" and len(argv) >= 2:
        cmd_info(argv[1])
    elif head == "call" and len(argv) >= 2:
        args_text = " ".join(argv[2:]) if len(argv) > 2 else ""
        cmd_call(argv[1], args_text)
    elif head == "log":
        cmd_log(int(argv[1]) if len(argv) >= 2 and argv[1].isdigit() else 20)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_run_argv(sys.argv[1:]))
