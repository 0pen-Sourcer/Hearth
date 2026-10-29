"""The agent is called Inglen until the person names it, and a new install keeps
its files in ~/Inglen; an install from before keeps ~/Jarvis and the name it had.
The prompt has no content stance of its own: how to talk and what is off limits
come from the person's house rules.

Owner, 2026-10-29: JARVIS is Marvel's character (agreed: the app's own name), and
the "no refusing edgy, profane, dark or adult topics" section was his own setting,
"I don't want it to get to everyone as everyone is different".

    .\\.venv\\Scripts\\python.exe tests\\test_default_name.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from hearth import default_agent_name, default_workspace, persona, wake, tools
sp = persona.system_prompt()
print(json.dumps({"ws": tools.WORKSPACE, "default_ws": default_workspace(), "name": default_agent_name(),
                  "persona": persona.NAME, "first": sp.splitlines()[0], "edgy": "edgy" in sp or "adult" in sp,
                  "house": "house rules" in sp, "wake": wake._wake_phrases()[:2]}))
"""


def probe(home, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("HEARTH_", "JARVIS_"))}
    e.update(HOME=home, USERPROFILE=home, HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1", **env)
    out = subprocess.run([sys.executable, "-c", PROBE, ROOT], env=e, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(out.stderr[-1500:])
        return {}


new = tempfile.mkdtemp(prefix="hearth-new-install-")
got = probe(new)
check(f"a new install keeps its files in ~/Inglen ({got.get('ws')})", got.get("ws") == os.path.join(new, "Inglen"))
check(f"and the agent is Inglen until named ({got.get('name')}, {got.get('first')!r})",
      got.get("name") == "Inglen" and got.get("persona") == "Inglen" and got.get("first", "").startswith("You are Inglen."))
check(f"it wakes on its name ({got.get('wake')})", got.get("wake") == ["inglen", "hey inglen"])
check("the prompt takes no content stance; how to talk comes from house rules", not got.get("edgy") and got.get("house"))

old = tempfile.mkdtemp(prefix="hearth-old-install-")
os.makedirs(os.path.join(old, "Jarvis"))
got = probe(old)
check(f"an install from before keeps ~/Jarvis and its name ({got.get('ws')}, {got.get('name')})",
      got.get("ws") == os.path.join(old, "Jarvis") and got.get("persona") == "JARVIS")

named = tempfile.mkdtemp(prefix="hearth-named-")
got = probe(named, HEARTH_PERSONA_NAME="Nova")
check(f"a name they picked wins ({got.get('persona')}, {got.get('first')!r})",
      got.get("persona") == "Nova" and got.get("first", "").startswith("You are Nova.") and got.get("wake") == ["nova", "hey nova"])

print("\n" + ("all default name checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
