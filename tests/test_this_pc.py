"""What the app finds out about the PC isn't one of the person's memories.

The first-run scan saved three memories ("Hardware and VRAM budget", "Local LLM
setup", "Where things live", the last a list of every drive's folders), which
read as clutter on the Memory page of a product. Now the facts are kept in
~/.hearth/this_pc.json, the system prompt carries one short line from them,
the learn_environment tool gives the whole drive map when asked, and the old
memories are taken out unless the person changed them.

    .\\.venv\\Scripts\\python.exe tests\\test_this_pc.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-this-pc-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import environment as E, memory, persona                # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


E.detect_gpu = lambda: {"name": "NVIDIA GeForce RTX 5060", "vram_gb": 8.0}
E.detect_models = lambda endpoint=None: ["qwen3-8b"]
E.detect_drives = lambda: {"C:\\": ["Users", "Program Files", "Games"], "E:\\": ["Game dev", "Movies"]}

# What an older version left: the three machine memories, one of them changed by the person.
memory.save("Hardware and VRAM budget", "user", "NVIDIA GeForce RTX 5060 (8GB VRAM), 31.6GB RAM, 16 cores", "x")
memory.save("Local LLM setup", "reference", "LLM server at http://localhost:1234/v1; use list_models, never disk-scan", "x")
memory.save("Where things live", "reference", "My game projects are on E:, films too", "the person's own words")
memory.save("Favourite editor", "user", "Uses Godot for games", "x")

said = E.learn_environment(endpoint="http://localhost:1234/v1")
index = memory.list_index()
check(f"the scan saves no memories, and takes out the old machine ones {index!r}",
      "Hardware and VRAM budget" not in index and "Local LLM setup" not in index)
check("one the person rewrote stays, and so does everything else they have",
      "Where things live" in index and "Favourite editor" in index)
check(f"the boot line is short {said!r}", said.startswith("Learned ") and "\n" not in said)

line = E.this_pc_line()
check(f"the system prompt says the machine in one line, with no folder list {line!r}",
      "RTX 5060 (8 GB VRAM)" in line and "Game dev" not in line and "Drives" not in line)
check("and it is in the prompt", line in persona.system_prompt())
full = E.learn_environment(endpoint="http://localhost:1234/v1", detail=True)
check("the learn_environment tool says the machine and its models, and lists no one's folders",
      "qwen3-8b" in full and "RTX 5060" in full and "Game dev" not in full)

# The number of helpers at once: the old default of 1 in every settings file isn't a choice.
import json
from hearth import llmserver as L
L.detect_gpu_vram_gb = lambda: 16.0
sp = os.path.join(os.environ["HEARTH_WORKSPACE"], "settings.json")
json.dump({"server_parallel_slots": 1}, open(sp, "w"))
check(f"a 1 left from the old default doesn't hold a 16 GB card at one ({L.get_parallel_slots()})", L.get_parallel_slots() == 2)
json.dump({"server_parallel_slots": 1, "server_parallel_slots_chosen": True}, open(sp, "w"))
check("a 1 the person picked is kept", L.get_parallel_slots() == 1)
json.dump({"server_parallel_slots": 0, "server_parallel_slots_chosen": False}, open(sp, "w"))
check("Automatic is from graphics memory", L.get_parallel_slots() == 2)

print("\n" + ("all this-PC checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
