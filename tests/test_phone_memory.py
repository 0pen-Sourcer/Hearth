"""Memory on the phone, as the window's Memory tab has it: what is saved (who saved it,
from which chat), where chats stand ("Going on"), forget and done. Through the relay
while the PC is on.

Owner, 2026-10-29 (C24): "Memory on the phone like the window's Memory tab".

    .\\.venv\\Scripts\\python.exe tests\\test_phone_memory.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="hearth-phone-memory-")
WS = os.path.join(HOME, "Inglen")
os.environ.update(HOME=HOME, USERPROFILE=HOME, JARVIS_WORKSPACE=WS, HEARTH_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
os.makedirs(os.path.join(WS, "memory"))
sys.path.insert(0, ROOT)

from hearth import chat_sync, memory, memory_cards, relay_bridge as RB  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


chat_sync.post("c_trip", "Goa trip", "relay", [{"role": "user", "content": "plan Goa", "at": 1}])
memory.save("Prefers window seats", "user", "They ask for a window seat on every flight.",
            tags=["auto-extracted"], source_chat="c_trip")
memory.save("Home city", "user", "Lives in Pune.")
memory_cards.put("c_trip", {"goal": "Plan the Goa trip", "status": "open", "slots": {"dates": "Dec 20-24"}})

got = RB.answer({"what": "memory"})
facts = {f["name"]: f for f in got.get("facts") or []}
check(f"the phone gets what is saved ({sorted(facts)})", {"Prefers window seats", "Home city"} <= set(facts))
seat = facts.get("Prefers window seats") or {}
check(f"each says who saved it ({seat.get('noticed')}, {(facts.get('Home city') or {}).get('noticed')})",
      seat.get("noticed") is True and (facts.get("Home city") or {}).get("noticed") is False)
check(f"and what it says, from which chat ({seat.get('text')!r}, {seat.get('chat_title')!r})",
      "window seat" in (seat.get("text") or "").lower() and seat.get("chat") == "c_trip" and seat.get("chat_title") == "Goa trip")
cards = got.get("cards") or []
check(f"and where chats stand, Going on ({cards})", len(cards) == 1 and cards[0]["goal"] == "Plan the Goa trip"
      and cards[0]["chat"] == "c_trip")

r = RB.answer({"what": "memory", "action": "forget", "name": "Home city"})
names = [f["name"] for f in RB.answer({"what": "memory"}).get("facts") or []]
check(f"forget takes a fact away ({r}, {names})", r.get("ok") and "Home city" not in names)
r = RB.answer({"what": "memory", "action": "done", "chat": "c_trip"})
check(f"Done closes a Going on card ({r})", r.get("ok") and not RB.answer({"what": "memory"}).get("cards"))
memory_cards.put("c_trip", {"goal": "Plan the Goa trip", "status": "open"})
r = RB.answer({"what": "memory", "action": "drop", "chat": "c_trip"})
check("and Forget removes it", r.get("ok") and memory_cards.get("c_trip") is None)
check("a fact that isn't there says so", RB.answer({"what": "memory", "action": "forget", "name": "Nope"}).get("ok") is False)

print("\n" + ("all phone memory checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
