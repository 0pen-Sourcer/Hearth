"""Checks over the engine picker and the repair plan.

Both decide things the user never sees until they are wrong. The engine picker
chooses which llama.cpp build runs, and the repair plan promises which parts of
a broken install it can put back.

    .\\.venv\\Scripts\\python.exe tests\\test_install_contract.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------- engine build ordering
from hearth.llmserver import build_rank                             # noqa: E402

# llama.cpp build numbers crossed four digits into five. A string sort puts
# b9999 above b10488, which picks a stale engine and, worse, makes the pruner
# below keep the two oldest and delete the current one.
tags = ["b10068", "b10488", "b9999", "b9820"]
ordered = sorted(tags, key=build_rank, reverse=True)
check("a five-digit build outranks a four-digit one", ordered[0] == "b10488")
check("the whole ordering is numeric", ordered == ["b10488", "b10068", "b9999", "b9820"])
check("the pruner would keep the current builds",
      set(ordered[:2]) == {"b10488", "b10068"})
check("a tag that is not a build number sorts below every build",
      sorted(["b100", "nightly", "custom"], key=build_rank, reverse=True)[0] == "b100")
check("an empty tag does not raise", build_rank("") == (0, 0, ""))
check("case does not matter", build_rank("B123") == build_rank("b123"))

# ---------------------------------------------- repair promises match the patch
# The repair plan and the patch builder keep separate lists. If they drift, the
# plan either promises to restore something the patch never ships, or reports a
# part unfixable that it could have fixed.
from hearth import updater                                          # noqa: E402

_bp = open(os.path.join(ROOT, "build_patch.py"), encoding="utf-8").read()


def _list_from_patch(name):
    m = re.search(name + r"\s*=\s*\[(.*?)\]", _bp, re.S)
    return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()


shipped = _list_from_patch("_BINARY_INCLUDES") | _list_from_patch("_VENV_PURE_INCLUDES")
# Hearth's own files are checked against the packed list below; these are the
# bundled libraries taken from the build.
promised = {m for _, mods, _ in updater._REPAIRABLE for m in mods
            if "/" not in m and m != "hearth_cli.py"}
missing = sorted(promised - shipped)
check("every part repair promises is one the patch ships"
      + (", missing " + ", ".join(missing) if missing else ""), not missing)

plan_unpatchable = {m for _, mods, _ in updater._UNPATCHABLE for m in mods}
patch_unpatchable = _list_from_patch("_UNPATCHABLE")
check("the two unpatchable lists agree", plan_unpatchable == patch_unpatchable)
check("nothing is called both repairable and unpatchable",
      not (promised & plan_unpatchable))

# ------------------------------------------- the patch carries Hearth's own files
# It used to pack only hearth/*.py and ui.html, so the skills, the helper
# agents' instructions and every icon and piece of art never updated and never
# came back after a repair, while the plan called the app intact.
import build_patch                                                  # noqa: E402

packed = {arc for _, arc in build_patch.code_members()}
for want in ("hearth/ui.html", "hearth_cli.py", "hearth/subagents/coder.md",
             "hearth/skills/make-pdf/SKILL.md", "assets/icon.png", "assets/mark.svg"):
    check(f"the patch packs {want}", want in packed)
if os.path.isdir(os.path.join(ROOT, "assets", "team")):
    check("the patch packs the Team room art", "assets/team/manifest.json" in packed)
check("no compiled or backup files are packed",
      not any(a.endswith((".pyc", ".bak")) or "__pycache__" in a for a in packed))
own = [m for _, mods, _ in updater._REPAIRABLE for m in mods if "/" in m or m == "hearth_cli.py"]
unpacked = [m for m in own if not any(a == m or a.startswith(m + "/") for a in packed)]
check("every file repair checks for is one the patch restores"
      + (", missing " + ", ".join(unpacked) if unpacked else ""), not unpacked)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all install contract checks passed")
