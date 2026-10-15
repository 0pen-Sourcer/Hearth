"""A skill switched off on the Skills page stays on disk but out of the model's way:
it is not in the prompt's skill list, list_skills leaves it out, and load_skill
refuses it. The page still sees it, marked off.

    .\\.venv\\Scripts\\python.exe tests\\test_skills_off.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-skillsoff-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
sys.path.insert(0, ROOT)

from hearth import skills_loader as SL  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


names = [s["name"] for s in SL.list_skills()]
check(f"the bundled skills are there to switch {names}", "make-pdf" in names)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"skills_off": ["make-pdf"]}, f)

check("a switched-off skill is not in the prompt's list", "make-pdf" not in SL.skills_for_prompt())
check("list_skills leaves it out for the model", "make-pdf" not in [s["name"] for s in SL.list_skills()])
r = SL.load_skill("make-pdf")
check(f"load_skill refuses it and says why ({r.get('error')})", not r.get("ok") and "switched off" in r.get("error", ""))
page = {s["name"]: s for s in SL.list_skills(include_off=True)}
check("the page still lists it, marked off", "make-pdf" in page and page["make-pdf"]["enabled"] is False
      and page["make-ascii"]["enabled"] is True)
check("the others still load", SL.load_skill("make-ascii").get("ok"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all switched-off skill checks passed")
