"""A message from the phone to a Codex session open on the PC goes to it.

The phone refused ("open in Codex on your PC right now"). Codex has `codex queue
--thread <id> --message <text>`, which hands a message to the app that owns the
session (Shed does the same); the phone now uses it, and says so. A Codex
without that command still gets the old answer.

    .\\.venv\\Scripts\\python.exe tests\\test_codex_queue.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-codex-queue-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

from hearth import agent_sessions as S                              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


PROJ = os.path.join(HOME, "game")
os.makedirs(PROJ)
RAN = os.path.join(HOME, "ran.json")
fake = os.path.join(HOME, "fake_codex.py")
with open(fake, "w") as f:
    f.write(f"""import json, sys
json.dump(sys.argv[1:], open({RAN!r}, "w"))
sys.exit(0 if sys.argv[1:2] == ["queue"] and "--ok" not in sys.argv else 0)
""")
S.read = lambda agent, sid: {"cwd": PROJ, "path": os.path.join(HOME, "s.jsonl"), "messages": []}
S.live_state = lambda agent, sid, path="": "open"
S._exe = lambda agent: [sys.executable, fake]
said = []
rc = asyncio.run(S.say("as_codex_11111111-2222-3333-4444-555555555555", "keep it sealed",
                       lambda kind, **f: said.append((kind, f)), lambda q: False))
ran = json.load(open(RAN))
check(f"the message goes to the open session through codex queue ({ran})",
      ran == ["queue", "--thread", "11111111-2222-3333-4444-555555555555", "--message", "keep it sealed"] and rc == 0)
check(f"and the phone is told where it went, as Inglen's note, not as Codex speaking {said}", said and said[-1][0] == "note" and "Sent to Codex" in said[-1][1]["text"])

with open(fake, "w") as f:
    f.write("import sys\nsys.stderr.write(\"error: unrecognized subcommand 'queue'\")\nsys.exit(2)\n")
said.clear()
rc = asyncio.run(S.say("as_codex_11111111-2222-3333-4444-555555555555", "keep it sealed",
                       lambda kind, **f: said.append((kind, f)), lambda q: False))
check(f"a Codex without the command gets the old answer {said}",
      rc == 3 and said and said[-1][0] == "error" and "open in Codex" in said[-1][1]["message"])

print("\n" + ("all Codex queue checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
