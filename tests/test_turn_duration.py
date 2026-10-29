"""A turn's duration is the agent's working time: the time it sat waiting on the person's
OK (or an answer to its question) isn't counted. The window, the terminal and the phone
show the same number, taken off once where each writes the turn's events.

Owner, 2026-10-29: the phone said 34.5s for a reply that waited on him for most of it.

    .\\.venv\\Scripts\\python.exe tests\\test_turn_duration.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="hearth-turn-duration-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
WS = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(WS)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "approval_mode": "ask"}, f)
sys.path.insert(0, ROOT)

from hearth import channel_core as cc, chat_sync, headless  # noqa: E402
from hearth.waited import Waited  # noqa: E402

cc.ACCESS_FILE = os.path.join(WS, "channel_access.json")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# The helper itself: plain and async checks, only "done" changes.
w = Waited()
w.timing(lambda: time.sleep(0.2))()


async def slow():
    await asyncio.sleep(0.2)
    return "allow"
check("an async check is still async and its answer comes back", asyncio.run(w.timing(slow)()) == "allow")
got = w.fix("done", {"duration_ms": 1000, "iterations": 1})
check(f"both waits come off a turn's duration ({got})", 520 <= got["duration_ms"] <= 650 and got["iterations"] == 1)
check("other events pass as they are", w.fix("tool_call", {"duration_ms": 1000}) == {"duration_ms": 1000})


class Phone(cc.Adapter):
    name, label, max_len = "phone", "the phone app", 4000

    def send(self, chat, text, reply_to=None):
        return 1

    def ask(self, chat, text):
        pass

    def edit(self, chat, msg_id, text):
        return True


async def model(prompt, *, emit, permission_check=None, **k):
    t0 = time.time()
    await asyncio.sleep(0.3)                                  # working
    await asyncio.to_thread(permission_check, "move_path", {"source": "a.txt", "destination": "b.txt"})
    await asyncio.sleep(0.2)                                  # working again
    emit("assistant", content="Moved it.")
    emit("done", duration_ms=int((time.time() - t0) * 1000), iterations=1)
    return 0

headless.run_once = model
app = Phone()
hub = cc.Hub(app, configured_senders=["me"], persona="Inglen")
hub.receive("me", "me", "move a.txt to b.txt")
time.sleep(0.3 + 1.5)                                         # they take a while to tap Allow
hub.receive("me", "me", "yes")
end = time.time() + 10
while hub._busy and time.time() < end:
    time.sleep(0.05)
for t in hub.threads:
    t.join(3)
saved = (chat_sync.load(cc.chat_id_for(app.name, "me")) or {}).get("messages") or []
ms = (saved[-1].get("stats") or {}).get("duration_ms") if saved else None
check(f"a phone turn that waited on their OK counts only its working time ({ms} ms, about 500 working)",
      ms is not None and 400 <= ms <= 1100)

src = open(os.path.join(ROOT, "hearth", "web.py"), encoding="utf-8").read()
cli = open(os.path.join(ROOT, "hearth_cli.py"), encoding="utf-8").read()
check("the window times its approvals and questions the same way",
      "_waited.timing(_make_permission_check(emit))" in src and "_waited.timing(_make_ask_user_bridge(emit))" in src
      and "_waited.fix(event_type, fields)" in src)
check("and so does the terminal", cli.count("waited.fix(") == 2 and "waited.timing(self._ask_permission)" in cli)

print("\n" + ("all turn duration checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
