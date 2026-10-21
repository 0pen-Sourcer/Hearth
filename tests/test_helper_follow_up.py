"""A finished helper takes a follow-up.

Telling a helper something after it finished only said "that helper has already
finished". Now it starts again on the message under the same name and role,
knowing what it was asked and what it reported. Stand-in spawn; a real
transcript file.

    .\\.venv\\Scripts\\python.exe tests\\test_helper_follow_up.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-followup-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import subagents  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


aid = "sub_1700000000_abc123"
path = subagents._transcript_path(aid)
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("w", encoding="utf-8") as f:
    for rec in [{"kind": "start", "persona": "researcher", "name": "Alex", "prompt": "Find the three cheapest Edge 60 Pro listings.", "by": ""},
                {"kind": "assistant_final", "text": "Cheapest: A 34k, B 35k, C 36k."}]:
        f.write(json.dumps(rec) + "\n")

spawned = []
subagents.spawn_subagent = lambda persona, prompt, **kw: (spawned.append((persona, prompt, kw)) or
                                                          {"ok": True, "status": "launched", "agent_id": "sub_1700000001_def456"})
r = subagents.tell_subagent(aid, "now check which of them ship to Dhanbad", sender="the user")
persona, prompt, kw = spawned[0] if spawned else ("", "", {})
check(f"a finished helper starts again on the follow-up {r.get('note', r.get('error'))!r}",
      r.get("ok") and r.get("follows") == aid and persona == "researcher" and kw.get("name") == "Alex"
      and kw.get("mode") == "background")
check("knowing what it was asked, what it reported, and the follow-up",
      "three cheapest" in prompt and "Cheapest: A 34k" in prompt and "ship to Dhanbad" in prompt)

spawned.clear()
r = subagents.tell_subagent("sub_1700000002_nope00", "hello")
check(f"one with no record left says it can't be picked up {r}", not r.get("ok") and not spawned)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all follow-up checks passed")
