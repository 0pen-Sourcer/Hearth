"""House rules are the one layer for how the assistant should act: an old
soul.md folds into rules.md once (its own lines, not the template) and the
prompt carries them as house rules. No soul tools are left, and none
replaces them: a standing order is a memory_save of type feedback.

    .\\.venv\\Scripts\\python.exe tests\\test_house_rules.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-rules-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS

from hearth import memory as M  # noqa: E402
from hearth import persona as P  # noqa: E402
from hearth import tools as T  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


check("the workspace is the test one", os.path.dirname(M.RULES_PATH) == WS)
with open(M.SOUL_PATH, "w", encoding="utf-8") as f:
    f.write("# Soul\n\nThings I've locked in about myself — written by me, for me, persisted\n"
            "across every session and restart. Anything in here is treated as part\n"
            "of my identity. Use `edit_soul` to add / refine / drop entries.\n\n"
            "- Tone: casual, bro energy, zero formality.\n- Never call the user \"JARVIS\".\n")

prompt = P.system_prompt()
rules = open(M.RULES_PATH, encoding="utf-8").read()
check("the old soul's lines are house rules now", "- Tone: casual, bro energy" in rules
      and 'Never call the user "JARVIS"' in rules)
check("its template text is not", "locked in about myself" not in rules)
check("soul.md is kept beside as soul.md.old", not os.path.exists(M.SOUL_PATH)
      and os.path.exists(M.SOUL_PATH + ".old"))
check("the prompt carries them under house rules", "# House rules" in prompt and "bro energy" in prompt)
check("and has no soul section", "# Soul" not in prompt and "soul" not in prompt.lower())

M.read_rules()
check("folding again changes nothing", open(M.RULES_PATH, encoding="utf-8").read() == rules)

names = [t["name"] for t in T.TOOL_DEFINITIONS]
check("no soul tools are left", not [n for n in names if "soul" in n])
check("and no rule tool stands in for them", not [n for n in names if "rule" in n and "reminder" not in n])
check("the prompt sends standing orders to memory as feedback", "save it with type feedback" in prompt)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all house rules checks passed")
