"""Finding what was said in earlier chats.

Each exchange (what was said and the reply) is one row, with when and the names
in it. A search is ranked by words, shared names, how recent, and whether the
chat is still open; time words become a date range by rule; with only a time it
lists the chats from then. Reading a chat to carry it on shows where it stood.
A throwaway workspace.

    .\\.venv\\Scripts\\python.exe tests\\test_chat_recall.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import time

work = tempfile.mkdtemp(prefix="hearth-recall-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import memory_cards, session_search as S, tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


DAY = 86400
now = time.time()


def chat(cid, title, pairs, at):
    msgs = []
    for u, a in pairs:
        msgs += [{"role": "user", "content": u, "at": int(at * 1000)},
                 {"role": "assistant", "content": a, "at": int(at * 1000)}]
    os.makedirs(os.path.join(work, "conversations"), exist_ok=True)
    with open(os.path.join(work, "conversations", cid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": cid, "title": title, "updated": int(at * 1000), "messages": msgs}, f)


chat("c_old", "Phones last year", [("Should I get the Pixel 7?", "It's fine for the price.")], now - 200 * DAY)
chat("c_phone", "Phone under 30k", [
    ("I want a phone under 30k for the camera", "Look at the Pixel 9a and the Nothing Phone 3."),
    ("<lately>\nStill going on: Goa trip\n</lately>\n\nwhich has the better zoom?\n\n[Current local time: 2026-10-27 10:00 (Tuesday, tz IST).]",
     "The Pixel 9a, by a little.")], now - 1 * DAY)
chat("c_cook", "Dinner ideas", [("what can I cook with paneer", "Paneer bhurji or tikka.")], now - 3 * DAY)

# ---- one row per exchange
n = S.rebuild_index()
check(f"each exchange is one row ({n})", n == 4)
hits = S.search("pixel zoom")
check(f"a search finds the exchange, best first {[(h.conversation_id, h.message_index) for h in hits]}",
      hits and hits[0].conversation_id == "c_phone" and hits[0].message_index == 2 and "better zoom" in hits[0].content
      and "Pixel 9a" in hits[0].reply)
check("what Hearth put in front of a message isn't indexed as said",
      "<lately>" not in hits[0].content and "Current local time" not in hits[0].content)

# Names and recency: the newer chat with the same words wins, an open one more so.
hits = S.search("pixel")
check(f"the newer chat comes ahead of last year's {[h.conversation_id for h in hits]}",
      [h.conversation_id for h in hits if h.kind == "round"][:2] == ["c_phone", "c_old"])
memory_cards.put("c_old", {"goal": "Pick a Pixel", "status": "open"})
s_old = S.score(1.0, 0, 200, 1, True)
s_new = S.score(1.0, 0, 1, 1, False)
check("an open chat gets a lift, and recency still counts", s_old > S.score(1.0, 0, 200, 1, False) and s_new > S.score(1.0, 0, 200, 1, False))
memory_cards.forget("c_old")

# ---- time words, by rule
base = dt.datetime(2026, 10, 28, 15, 0).timestamp()
a, b, rest = S.time_range("what did we talk about yesterday about paneer", base)
check(f"'yesterday' is the day before, and leaves the rest {rest!r}",
      dt.datetime.fromtimestamp(a) == dt.datetime(2026, 10, 27) and dt.datetime.fromtimestamp(b) == dt.datetime(2026, 10, 28)
      and "yesterday" not in rest and "paneer" in rest)
a, b, _ = S.time_range("last week", base)
check("'last week' is the week before this one",
      dt.datetime.fromtimestamp(a) == dt.datetime(2026, 10, 19) and dt.datetime.fromtimestamp(b) == dt.datetime(2026, 10, 26))
a, b, _ = S.time_range("that thing in June", base)
check("'in June' is June", dt.datetime.fromtimestamp(a) == dt.datetime(2026, 6, 1) and dt.datetime.fromtimestamp(b) == dt.datetime(2026, 7, 1))
a, b, _ = S.time_range("3 days ago", base)
check("'3 days ago' is around that day", a <= dt.datetime(2026, 10, 25).timestamp() < b)
check("'may' as a word is not a month", S.time_range("you may be right", base)[0] is None)
check("no time word, no range", S.time_range("pixel zoom", base)[0] is None)

hits = S.search("pixel yesterday")
check(f"a time word keeps the search to then {[h.conversation_id for h in hits]}",
      hits and all(h.conversation_id == "c_phone" for h in hits))
hits = S.search("", after=dt.datetime.fromtimestamp(now - 5 * DAY).strftime("%Y-%m-%d"))
check(f"with only a time, it lists the exchanges from then, newest first {[h.conversation_id for h in hits]}",
      [h.conversation_id for h in hits] == ["c_phone", "c_phone", "c_cook"])
out = tools.execute_tool("search_chats", {"query": "paneer", "before": "today"})
check("the tool reads after/before and says what was said and replied",
      "They said: what can I cook with paneer" in str(out) and "Reply: Paneer bhurji" in str(out) and "convo_id=c_cook" in str(out))

# ---- a card is found too, and reading a chat shows where it stood
memory_cards.put("c_phone", {"goal": "Buy a phone under 30k", "status": "open", "slots": {"shortlist": "Pixel 9a, Nothing Phone 3"},
                             "left": ["Hasn't bought it yet"]})
hits = S.search("shortlist phone")
check(f"a chat's card answers a search {[(h.kind, h.conversation_id) for h in hits]}",
      any(h.kind == "card" and h.conversation_id == "c_phone" for h in hits))
text = S.read_chat("c_phone")
check("carrying a chat on reads it in order, with where it stood first",
      text.startswith("Chat: Phone under 30k") and "Where it stood: Buy a phone under 30k" in text
      and text.index("for the camera") < text.index("better zoom"))

# ---- the sidebar box still wants every word typed
check("the sidebar's search needs every word", not S.search("paneer pixel", match_all=True)
      and S.search("paneer bhurji", match_all=True))

# ---- the index follows the chats: a changed chat is indexed again, a deleted one goes
chat("c_cook", "Dinner ideas", [("what can I cook with paneer", "Paneer bhurji or tikka."),
                                ("and with tofu?", "Tofu stir fry.")], now)
check("a chat that grew is found by what it says now", any(h.conversation_id == "c_cook" for h in S.search("tofu")))
os.remove(os.path.join(work, "conversations", "c_old.json"))
check("a deleted chat is gone from it", not any(h.conversation_id == "c_old" for h in S.search("pixel 7")))

# ---- facts are indexed too, for the memory pass to check against
from hearth import memory  # noqa: E402
memory.save("laptop-os", "user", "Runs Windows 10 on the ThinkPad", "Windows 10", force=True)
memory.save("favourite-food", "user", "Loves paneer tikka", "Paneer", force=True)
c = S.fact_candidates("USER: I wiped the ThinkPad and put Linux on it")
check(f"the facts a conversation touches are found by words and names {c}", c and c[0]["title"] == "laptop-os")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat recall checks passed")
