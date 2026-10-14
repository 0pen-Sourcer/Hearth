"""Memories attached to a message have to be about it.

"you still here?" pulled in a history-chapter note (it contained "still" and
"here"), and the block read as if the person had pasted it, so the model
told them their message carried an odd memory block.

    .\\.venv\\Scripts\\python.exe tests\\test_recall_relevance.py
"""
from __future__ import annotations

import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-recall-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import memory                                            # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


memory.save("History chapter notes", "project", "notes for the history chapter",
            "Minoans, Shang China, cuneiform. Still need the bronze age part; the timeline is here.")
memory.save("GPU", "user", "his graphics card", "RTX 5060 with 8 GB of VRAM.")

block = memory.recall_for_prompt("you still here?")
check(f"a casual message pulls in no memory {block[:80]!r}", block == "")
gpu = memory.recall_for_prompt("what GPU do I have?")
check("a message about something saved still gets it", "RTX 5060" in gpu)
check("and the block says Hearth attached it, not the person",
      "did not write or paste" in gpu and "Hearth attached" in gpu)

# The background saver kept noting facts after "don't save anything".
from hearth import memory_extract as mx                              # noqa: E402
chat = [{"role": "user", "content": "I'm testing, don't save anything from this chat"},
        {"role": "assistant", "content": "Got it."}, {"role": "user", "content": "my GPU is an RTX 5060"}]
check("after being told not to save, the chat's facts aren't noted in the background", mx.may_note(chat) is False)
check("an ordinary chat still is", mx.may_note([{"role": "user", "content": "my GPU is an RTX 5060"}]) is True)
import json                                                          # noqa: E402
json.dump({"auto_memory": False}, open(os.path.join(work, "settings.json"), "w"))
check("and nothing is noted when it's switched off", mx.may_note([{"role": "user", "content": "hi"}]) is False)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all recall relevance checks passed")
