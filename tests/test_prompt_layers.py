"""The layer files go into the prompt as what the person wrote, nothing else.

A fresh rules file is a template: a heading, an "Edit this file freely" line
and example rules. All of it went into every prompt, so a new user's assistant
was told "never run pip without asking first" and that "the project" meant
~/Documents/MyApp. Headings came through twice (the prompt's and the file's).

    .\\.venv\\Scripts\\python.exe tests\\test_prompt_layers.py
"""
from __future__ import annotations

import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-layers-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import memory, persona                                   # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


memory.ensure_rules_exist()
sp = persona.system_prompt()
check("a fresh rules file adds no rules: its examples are not the person's",
      "never run pip without asking first" not in sp and "~/Documents/MyApp" not in sp
      and "Edit this file freely" not in sp)

with open(memory.RULES_PATH, "a", encoding="utf-8") as f:
    f.write("- call me boss\n")
memory.upsert_profile_line("Prefers short replies")
sp = persona.system_prompt()
check("a rule the person added is in", "- call me boss" in sp)
check("each layer has one heading, not the file's own as well",
      sp.count("# House rules") == 1 and "# Profile" not in sp and "Prefers short replies" in sp)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all prompt layer checks passed")
