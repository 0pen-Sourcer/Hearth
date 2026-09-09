"""A reply where some calls read and one does not.

When a model writes its tool calls as text, the fallback parser recovers what
it can. The branch that ran when anything at all parsed did not check whether
markup was LEFT OVER, so a reply carrying twenty calls where one closed a
bracket wrong ran the nineteen and dropped the twentieth without a word. The
turn then ended on work the model believed it had done, and the person
watching got their prompt back mid-task with no error anywhere.

    .\\.venv\\Scripts\\python.exe tests\\test_partial_tool_parse.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tool_call_parser as tcp                          # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


NAMES = ["memory_save", "read_file", "write_file"]


def block(payload):
    return "<tool_call>\n" + payload + "\n</tool_call>"


GOOD = block('{"name": "memory_save", "arguments": {"title": "a", "tags": ["x"]}}')
# The real shape: a closing bracket typed where a brace belonged, twice over,
# so the object never closes.
TYPO = block('{"name": "memory_save", "arguments": {"title": "b", "tags": ["y"]]')

# ------------------------------------------------- one good call on its own
cleaned, calls = tcp.parse("saving that now:\n" + GOOD, NAMES)
check("a well-formed call parses", len(calls) == 1)
check("and its markup is excised", not tcp.has_tool_call(cleaned))

# ------------------------------------------------------- only a broken call
cleaned, calls = tcp.parse("saving that now:\n" + TYPO, NAMES)
check("a call with the bracket typo yields nothing", not calls)
check("and its markup survives, which is what flags it", tcp.has_tool_call(cleaned))

# ------------------------------------------- the mix, which is the real bug
mixed = "doing several things:\n" + "\n".join([GOOD] * 3) + "\n" + TYPO
cleaned, calls = tcp.parse(mixed, NAMES)
check("the readable calls in a mixed reply still run", len(calls) == 3)
check("and the unreadable one leaves markup behind to be noticed",
      tcp.has_tool_call(cleaned))

# The old branch keyed only off `calls` being non-empty, so the mixed reply
# looked identical to a clean one. Leftover markup is what separates them.
check("a clean reply and a mixed one are now distinguishable",
      tcp.has_tool_call(tcp.parse(mixed, NAMES)[0])
      is not tcp.has_tool_call(tcp.parse("\n".join([GOOD] * 3), NAMES)[0]))

# ------------------------------------------------ the headless side wires it
_src = open(os.path.join(ROOT, "hearth", "headless.py"), encoding="utf-8").read()
_marker = "if _tcp.has_tool_call(cleaned):"
check("headless flags leftover markup even when some calls parsed",
      _marker in _src)
# It has to sit in the branch that RAN calls, not only the empty one.
_ran_branch = _src.split("if fallback_calls:", 1)[-1].split("else:", 1)[0]
check("and the flag is set inside the branch that ran the calls",
      _marker in _ran_branch)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all partial tool-parse checks passed")
