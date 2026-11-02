"""Long tool results keep their end, and a missed edit_file says what is there.

A result over its cap kept only its start, so a traceback or exit code at the
end of a long output never reached the model. An edit_file whose old_text was
not in the file said only to re-read it, even when the edit was already in, or
the text was one character off.

    .\\.venv\\Scripts\\python.exe tests\\test_result_cut_and_edit_miss.py
"""
from __future__ import annotations

import os
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="hearth-cut-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T                                        # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# --- cutting long results ---------------------------------------------------

build = "\n".join(f"compiling module {i:04d} ... done" for i in range(600))
tb = ("Traceback (most recent call last):\n"
      '  File "setup.py", line 12, in <module>\n'
      "    main()\n"
      "ModuleNotFoundError: No module named 'wheel'")
out = f"$ python setup.py build\n[exit 1]\n{build}\n{tb}"
cut = T._trunc(out, 6000)
check("a cut result fits its cap", len(cut) <= 6000)
check("the start is kept", cut.startswith("$ python setup.py build\n[exit 1]"))
check("a traceback at the end is kept whole", cut.endswith(tb))
check("the marker says what was dropped", "chars cut from the middle]" in cut)
head, rest = cut.split("\n…[", 1)
n_dropped = int(rest.split(" ", 1)[0])
tail = rest.split("]…\n", 1)[1]
check("the dropped count matches what is missing",
      n_dropped == len(out) - len(head) - len(tail))
check("cuts land on line ends", head.rstrip().endswith("done") and tail.startswith(("compiling", "Traceback")))

plain = "\n".join(f"row {i}" for i in range(3000))
c2 = T._trunc(plain, 4000)
check("an output without errors still keeps its end", c2.endswith("row 2999"))
check("and still keeps most of its start", c2.startswith("row 0\nrow 1") and c2.index("…[") > 2500)

npm = "\n".join(f"added package-{i}" for i in range(900)) + "\nnpm ERR! code ERESOLVE\nnpm ERR! peer dep missing"
c3 = T._trunc(npm, 2000)
check("an error line near the end is kept", "npm ERR! code ERESOLVE" in c3)

short = "fine"
check("a short result is untouched", T._trunc(short, 100) == short)
check("cutting twice changes nothing", T._trunc(cut, 6000) == cut)

big = {"role": "tool", "tool_call_id": "a", "content": out}
kept = T._truncate_kept_tool_results([big], max_chars=600)[0]["content"]
check("old results in history keep their end too", kept.endswith("No module named 'wheel'") and len(kept) <= 600)

# execute_tool applies the run_command cap the same way.
_orig = T._HANDLERS["run_command"]
T._HANDLERS["run_command"] = lambda p: out
try:
    via = T.execute_tool("run_command", {"command": "python setup.py build"})
finally:
    T._HANDLERS["run_command"] = _orig
check("run_command results through execute_tool keep the traceback", "ModuleNotFoundError" in via)

# --- edit_file misses -------------------------------------------------------

path = os.path.join(WS, "app.py")
src = ("import os\n\n"
       "def load(path):\n"
       "    with open(path) as f:\n"
       "        return f.read()\n\n"
       "def save(path, text):\n"
       "    with open(path, 'w') as f:\n"
       "        f.write(text)\n")
with open(path, "w", encoding="utf-8", newline="\n") as f:
    f.write(src)
T.execute_tool("read_file", {"path": path})

r = T.execute_tool("edit_file", {"path": path, "edits": [{"old_text": "def save(path, txt):\n    with open(path, 'w') as f:",
                                 "new_text": "def save(path, text, mode='w'):\n    with open(path, mode) as f:"}]})
check("a near miss names the closest lines", "Closest text is at lines 7-8" in r)
check("with their numbers and the file's text", "7\tdef save(path, text):" in r and "8\t    with open(path, 'w') as f:" in r)
check("the file is unchanged after a miss", open(path, encoding="utf-8").read() == src)

done = src.replace("return f.read()", "return f.read().strip()")
with open(path, "w", encoding="utf-8", newline="\n") as f:
    f.write(done)
T.execute_tool("read_file", {"path": path})
r2 = T.execute_tool("edit_file", {"path": path, "edits": [{"old_text": "        return f.read()\n\ndef save",
                                  "new_text": "        return f.read().strip()\n\ndef save"}]})
check("an edit already in the file says so", "already applied" in r2)

r3 = T.execute_tool("edit_file", {"path": path, "edits": [{"old_text": "class Totally(Unrelated):\n    zzz = 42",
                                  "new_text": "class Other:\n    pass"}]})
check("text nowhere near anything says nothing is close", "nothing in the file comes close" in r3)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
