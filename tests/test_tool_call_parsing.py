"""Tool-call parsing cases that cost real work when they broke.

Each one came from a live run or from replaying saved conversations, not from
imagination. Run standalone or under pytest:

    .\.venv\Scripts\python.exe tests\test_tool_call_parsing.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import hearth                                    # noqa: E402
from hearth.tool_call_parser import parse        # noqa: E402

NAMES = [t["name"] for t in hearth.TOOL_DEFINITIONS]
Q = chr(34)
A = chr(39)
B = chr(92)


def _one(text):
    _, calls = parse(text, NAMES)
    if not calls:
        return None, None
    fn = calls[0]["function"]
    return fn["name"], json.loads(fn["arguments"])


def case_plain():
    n, a = _one('<tool_call>{"name":"read_file","arguments":{"path":"a.py"}}</tool_call>')
    return [] if (n == "read_file" and a == {"path": "a.py"}) else [f"got {n} {a}"]


def case_two_calls_stay_two():
    _, calls = parse('<tool_call>{"name":"read_file","arguments":{"path":"a"}}</tool_call>'
                     '<tool_call>{"name":"read_file","arguments":{"path":"b"}}</tool_call>', NAMES)
    paths = [json.loads(c["function"]["arguments"]).get("path") for c in calls]
    return [] if paths == ["a", "b"] else [f"expected both calls, got {paths}"]


def case_unclosed_tag():
    """An unclosed tag used to swallow the call that followed it."""
    _, calls = parse('<tool_call>{"name":"read_file","arguments":{"path":"a"}}'
                     '<tool_call>{"name":"read_file","arguments":{"path":"b"}}</tool_call>', NAMES)
    paths = [json.loads(c["function"]["arguments"]).get("path") for c in calls]
    return [] if paths == ["a", "b"] else [f"expected both calls, got {paths}"]


def case_flattened_arguments():
    """Some models skip the arguments wrapper; running with none is worse."""
    n, a = _one('<tool_call>{"name":"write_file","path":"a.py","content":"hi"}</tool_call>')
    return [] if a == {"path": "a.py", "content": "hi"} else [f"got {a}"]


def case_truncated_tail():
    """A long generation cut a brace short. The file was written, then thrown away."""
    n, a = _one('<tool_call>{"name":"write_file","arguments":{"path":"a.py","content":"print(1)"}'
                '</tool_call>')
    if n != "write_file":
        return [f"expected write_file, got {n}"]
    return [] if a.get("content") == "print(1)" else [f"content lost: {a!r}"]


def case_content_with_braces_is_byte_exact():
    code = 'def f():\n    d = {"a": {"b": [1, {"c": 2}]}}\n    return {}\n'
    full = json.dumps({"name": "write_file",
                       "arguments": {"path": "c.py", "content": code}})
    n, a = _one("<tool_call>" + full + "</tool_call>")
    return [] if a.get("content") == code else ["brace-heavy content was altered"]


def case_content_containing_the_closing_tag():
    """A file documenting this format cut its own call short, and the stub was
    then repaired into a confident, truncated write."""
    doc = "Shape:" + chr(10) + "<tool_call>" + chr(10) + "{}" + chr(10) + "</tool_call>" + chr(10) + "done"
    full = json.dumps({"name": "write_file", "arguments": {"path": "d.md", "content": doc}})
    n, a = _one("<tool_call>" + full + "</tool_call>")
    return [] if a.get("content") == doc else [f"content truncated to {a.get('content')!r}"]


def case_apostrophe_and_python_literals():
    """A blanket quote/None replace used to drop or corrupt these."""
    body = "{" + A + "path" + A + ": " + A + "a.py" + A + ", " + A + "content" + A + ": " + \
           Q + "Don" + A + "t use None or True as text." + Q + "}"
    from hearth.tool_call_parser import loads_lenient
    got = loads_lenient(body)
    want = "Don" + A + "t use None or True as text."
    return [] if got and got.get("content") == want else [f"got {got!r}"]


def case_windows_path_backslashes():
    from hearth.tool_call_parser import loads_lenient
    raw = '{"path": "C:' + B + 'Users' + B + 'me' + B + 'a.py"}'
    got = loads_lenient(raw)
    want = "C:" + B + "Users" + B + "me" + B + "a.py"
    return [] if got and got.get("path") == want else [f"got {got!r}"]


def case_garbage_is_not_invented_into_a_call():
    _, calls = parse("<tool_call>not json at all</tool_call>", NAMES)
    return [] if not calls else [f"invented {calls}"]


def _paths(text):
    _, calls = parse(text, NAMES)
    return [(c["function"]["name"], json.loads(c["function"]["arguments"]).get("path"))
            for c in calls]


def case_mistral_array_keeps_every_call():
    got = _paths('Reading both. [TOOL_CALLS][{"name": "read_file", "arguments": {"path": "a"}}, '
                 '{"name": "read_file", "arguments": {"path": "b"}}, '
                 '{"name": "list_directory", "arguments": {"path": "c"}}]')
    want = [("read_file", "a"), ("read_file", "b"), ("list_directory", "c")]
    return [] if got == want else [f"expected {want}, got {got}"]


def case_command_r_array_keeps_every_call():
    got = _paths('Action: ```json\n[{"tool_name": "read_file", "parameters": {"path": "a"}}, '
                 '{"tool_name": "read_file", "parameters": {"path": "b"}}]\n```')
    want = [("read_file", "a"), ("read_file", "b")]
    return [] if got == want else [f"expected {want}, got {got}"]


CHECKS = [
    ("a Mistral block keeps every call in it", case_mistral_array_keeps_every_call),
    ("a Command-R block keeps every call in it", case_command_r_array_keeps_every_call),
    ("a plain call parses", case_plain),
    ("two calls stay two", case_two_calls_stay_two),
    ("an unclosed tag does not swallow the next call", case_unclosed_tag),
    ("flattened arguments are read, not run empty", case_flattened_arguments),
    ("a call cut one brace short still writes the file", case_truncated_tail),
    ("brace-heavy file content is byte exact", case_content_with_braces_is_byte_exact),
    ("content containing the closing tag survives", case_content_containing_the_closing_tag),
    ("apostrophes and None/True in content survive", case_apostrophe_and_python_literals),
    ("windows backslash paths survive", case_windows_path_backslashes),
    ("garbage is not invented into a call", case_garbage_is_not_invented_into_a_call),
]


def main() -> int:
    failed = 0
    for label, fn in CHECKS:
        try:
            problems = fn()
        except Exception as e:
            problems = [f"{type(e).__name__}: {e}"]
        if problems:
            failed += 1
            print(f"FAIL  {label}")
            for p in problems:
                print(f"        {p}")
        else:
            print(f"ok    {label}")
    print()
    print(f"{failed} check(s) failed" if failed else "all tool-call parsing checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
