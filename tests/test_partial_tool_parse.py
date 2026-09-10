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

# ---------------------------------------------- a call cut off mid-generation
# Completing a severed tail is right when the arguments are a reference and
# wrong when they are content. A write cut mid-string completes to a stub, and
# running it replaces the file with the part that arrived, which reports
# success and loses the rest.
CUT_WRITE = ('writing that now:\n<tool_call>\n'
             '{"name": "write_file", "arguments": {"path": "a.txt", "content": "hel')
CUT_CMD = ('ok:\n<tool_call>\n'
           '{"name": "run_command", "arguments": {"command": "Remove-Item C:/a -Recur')
CUT_READ = ('ok:\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.tx')

_names = ["write_file", "read_file", "run_command"]
cleaned, calls = tcp.parse(CUT_WRITE, _names)
check("a truncated write is not run with the stub it arrived as", not calls)
check("and it is flagged unreadable so the model resends", tcp.has_tool_call(cleaned))

cleaned, calls = tcp.parse(CUT_CMD, _names)
check("a truncated command is not run half-written", not calls)

cleaned, calls = tcp.parse(CUT_READ, _names)
check("a truncated path still recovers, since it fails harmlessly", len(calls) == 1)

_, calls = tcp.parse('ok:\n<tool_call>\n{"name": "write_file", "arguments": '
                     '{"path": "a.txt", "content": "hi"}}\n</tool_call>', _names)
check("a complete write is untouched", len(calls) == 1)

_, calls = tcp.parse('ok:\n<tool_call>\n{"name": "write_file", "arguments": '
                     '{"path": "a.txt", "content": "hi"}\n</tool_call>', _names)
check("a write one brace short still recovers, it is closed not severed",
      len(calls) == 1)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all partial tool-parse checks passed")
