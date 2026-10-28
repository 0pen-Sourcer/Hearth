"""Where each chat stands, and what a reply had in mind.

The memory pass writes one card per chat (goal, open/done/dropped, values,
what's left, names), replacing the old one; a chat with no goal gets none. A new
chat's first message carries the newest open cards, on every brain; later turns
don't. The pass runs once a chat goes quiet, on any brain, and the old "lately"
list is turned into cards once. Stand-in model calls; a throwaway workspace.

    .\\.venv\\Scripts\\python.exe tests\\test_memory_cards.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-cards-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1", HEARTH_MEMORY_IDLE="0.6")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import headless, memory, memory_cards, memory_extract, memory_pass  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def chat_file(cid, title, pairs, at=None):
    at = at or time.time()
    msgs = []
    for i, (u, a) in enumerate(pairs):
        msgs += [{"role": "user", "content": u, "at": int((at + i) * 1000)},
                 {"role": "assistant", "content": a, "at": int((at + i) * 1000)}]
    os.makedirs(os.path.join(work, "conversations"), exist_ok=True)
    with open(os.path.join(work, "conversations", cid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": cid, "title": title, "updated": int(at * 1000), "messages": msgs}, f)
    return msgs


DAY = 86400
now = time.time()

# ---- the pass writes the card and the facts
memory.save("laptop-os", "user", "Runs Windows 10", "Windows 10", force=True)
convo = chat_file("c_phone", "Phone under 30k", [
    ("I want a phone under 30k, mostly for the camera. Pixel 9a or Nothing Phone 3?", "Both shoot well."),
    ("Also I moved the laptop to Linux Mint", "Mint it is.")])
asked = []
reply = {"card": {"goal": "Buy a phone under 30k for the camera", "status": "open", "where": "Down to two",
                  "slots": {"budget": "30k", "shortlist": ["Pixel 9a", "Nothing Phone 3"]},
                  "left": ["Hasn't bought it yet"], "names": ["Pixel 9a", "Nothing Phone 3"]},
         "facts": [{"op": "update", "target": "laptop-os", "title": "laptop-os", "category": "user",
                    "description": "Runs Linux Mint", "body": "Moved from Windows.", "confidence": 5,
                    "volatility": "seasonal", "importance": 3}]}
saved, warns = memory_extract.extract_and_save(convo, lambda s, u: asked.append(u) or json.dumps(reply), chat_id="c_phone")
card = memory_cards.get("c_phone")
check(f"the pass keeps the chat's card, values and what is left {card}",
      card and card["goal"].startswith("Buy a phone") and card["slots"]["shortlist"] == "Pixel 9a, Nothing Phone 3"
      and card["left"] == ["Hasn't bought it yet"] and card["title"] == "Phone under 30k")
fm, _ = memory._read_body(os.path.join(memory.MEM_DIR, memory._slug("laptop-os") + ".md"))
check(f"an update rewrites the fact with where it came from and how much it matters {fm}",
      "Linux Mint" in fm.get("description", "") and fm.get("source_chat") == "c_phone" and fm.get("importance") == "3"
      and fm.get("valid_from"))
check("the pass is shown the related saved facts and the card it had",
      "- laptop-os: Runs Windows 10" in asked[0] and "# Card before (this chat)\nnull" in asked[0])

# The card is replaced, not added to; the next pass sees it.
reply2 = {"card": {"goal": "Buy a phone under 30k for the camera", "status": "done", "where": "Bought the Pixel 9a",
                   "slots": {"budget": "30k", "bought": "Pixel 9a"}, "left": []}, "facts": []}
memory_extract.extract_and_save(convo, lambda s, u: asked.append(u) or json.dumps(reply2), chat_id="c_phone")
card = memory_cards.get("c_phone")
check(f"a later pass replaces the card {card}", card["status"] == "done" and card["left"] == []
      and "shortlist" not in card["slots"] and '"goal": "Buy a phone' in asked[1])

# Banter has no card, and a broken answer changes nothing.
chat_file("c_chat", "hey", [("lol you're funny", "I try.")])
memory_extract.extract_and_save([{"role": "user", "content": "lol"}, {"role": "assistant", "content": "ha"}],
                                lambda s, u: json.dumps({"card": {"goal": "Playful banter", "where": "nothing substantive",
                                                                  "status": "open"}, "facts": []}), chat_id="c_chat")
check("a chat of banter gets no card", memory_cards.get("c_chat") is None)
memory_extract.extract_and_save(convo, lambda s, u: '{"card": {"goal": "x", ', chat_id="c_phone")
check("an answer that isn't JSON leaves the card as it was", memory_cards.get("c_phone")["status"] == "done")
memory_extract.extract_and_save(convo, lambda s, u: json.dumps({"card": None, "facts": []}), chat_id="c_phone")
check("a null card keeps the one there", memory_cards.get("c_phone") is not None)

# ---- facts keep their history
memory.save("school-grade", "user", "In class 12", "Class 12", volatility="seasonal", force=True)
memory.save("college-year", "user", "First year of college", "College", supersedes="school-grade", force=True)
old = os.path.join(memory._ARCHIVE_DIR, memory._slug("school-grade") + ".md")
ofm, _ = memory._read_body(old)
check(f"a superseded fact is kept with valid_to set {ofm.get('valid_to')}", ofm.get("valid_to") and ofm.get("superseded_by"))
memory.save("broken-earbud", "user", "Left earbud died", "Left one", volatility="transient", force=True)
memory_extract.extract_and_save([{"role": "user", "content": "fixed my earbud"}, {"role": "assistant", "content": "nice"}],
                                lambda s, u: json.dumps({"card": None, "facts": [{"op": "invalidate", "target": "broken-earbud",
                                                                                   "confidence": 5}]}), chat_id="c_ear")
efm, _ = memory._read_body(os.path.join(memory._ARCHIVE_DIR, memory._slug("broken-earbud") + ".md"))
check("an invalidated fact leaves the index and keeps valid_to",
      efm.get("valid_to") and "broken-earbud" not in " ".join(memory.fact_titles()))

# ---- the old list becomes cards, once, with a backup
with open(os.path.join(work, "memory", "threads.json"), "w", encoding="utf-8") as f:
    json.dump({"threads": {
        "c_cbse": {"topic": "CBSE private form", "where": "Form submitted.", "open": ["Fee payment"], "updated": now - DAY},
        "c_joke": {"topic": "Playful banter", "where": "Nothing substantive.", "open": [], "updated": now},
        "rem_1": {"topic": "Reminder: stand up", "where": "Said it.", "open": [], "updated": now},
    }}, f)
memory_cards.everything()
check("the old list's real entries became cards and its junk didn't",
      memory_cards.get("c_cbse") and memory_cards.get("c_cbse")["left"] == ["Fee payment"]
      and memory_cards.get("c_joke") is None and memory_cards.get("rem_1") is None)
check("the old file is kept beside, renamed", os.path.isfile(os.path.join(work, "memory", "threads.json.old"))
      and not os.path.isfile(os.path.join(work, "memory", "threads.json")))
check("and running it again does nothing", memory_cards.migrate() == 0)

# ---- a new chat's first message carries the open cards
memory_cards.put("c_trip", {"goal": "Plan the Goa trip", "status": "open", "slots": {"dates": "Dec 20-24"},
                            "left": ["Book the hotel"]})
memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"


def chunk(content=None, finish=None):
    return NS(choices=[NS(index=0, delta=NS(content=content, reasoning_content=None, model_extra={},
                                             tool_calls=None), finish_reason=finish)], usage=None, model_extra={})


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()


SENT = []


async def fake(client, **kw):
    SENT.append([dict(m) for m in kw.get("messages") or []])
    return Stream([chunk(content="The hotel?"), chunk(finish="stop")])
headless.chat_create = fake
noted = []
real_note = headless.note_turn_async
headless.note_turn_async = lambda msgs, model, chat_id, emit=None: noted.append((msgs[-1].get("content"), chat_id))
got = {}
asyncio.run(headless.run_once("yo I got something, guess", model="m", history=[], chat_id="c_new",
                              permission_check=lambda *a: "allow", emit=lambda k, **f: got.__setitem__(k, f)))
last = SENT[0][-1]["content"] if SENT else ""
check("a new chat's first message goes with the open cards in front of it",
      last.startswith("<lately>") and "Plan the Goa trip" in last and "CBSE private form" in last
      and "Buy a phone" not in last and "yo I got something" in last)
items = (got.get("context_used") or {}).get("threads") or []
check(f"the surface hears each card, its chat and its kind {items[:1]}",
      items and items[0]["kind"] == "card" and items[0]["chat"] in ("c_trip", "c_cbse") and items[0]["topic"]
      and "where" in items[0] and "ago" in items[0])
check("and the turn's pass is waiting for the chat to go quiet", noted and noted[-1][1] == "c_new")
SENT.clear()
got.clear()
asyncio.run(headless.run_once("and?", model="m", history=[{"role": "user", "content": "hey"}, {"role": "assistant", "content": "hey"}],
                              chat_id="c_new", permission_check=lambda *a: "allow", emit=lambda k, **f: got.__setitem__(k, f)))
check("a later turn gets nothing in front", SENT and "<lately>" not in SENT[0][-1]["content"]
      and "context_used" not in got)

# ---- an agent brain gets the same, and a turn with no reply in "done" still gets its pass
from hearth import claude_brain  # noqa: E402
seen = {}


async def fake_turn(prompt, **kw):
    seen["context"] = kw.get("context")
    kw["emit"]("assistant_chunk", content="Book the ")
    kw["emit"]("assistant_chunk", content="hotel?")
    kw["emit"]("done", iterations=1)
    return 0
claude_brain.active = lambda: True
claude_brain.run_turn = fake_turn
noted.clear()
asyncio.run(headless.run_once("I got something", chat_id="c_new2", history=[], emit=lambda k, **f: None))
check(f"an agent brain is handed the cards on a first message {str(seen.get('context'))[:50]!r}",
      "Plan the Goa trip" in (seen.get("context") or ""))
check(f"and a turn whose done has no reply runs the pass on what it said {noted}",
      noted == [("Book the hotel?", "c_new2")])
asyncio.run(headless.run_once("go on", chat_id="c_new2", history=[{"role": "user", "content": "x"}], emit=lambda k, **f: None))
check("a later agent turn gets no cards", not seen.get("context"))
headless.note_turn_async = real_note

# ---- once per chat, when it goes quiet, on whatever model is there
runs = []
memory_pass.choose_llm = lambda model="": ((lambda s, u: runs.append(u) or json.dumps(
    {"card": {"goal": "Fix the router", "status": "open", "left": ["Call the ISP"]}, "facts": []})), "stand-in")
import importlib  # noqa: E402
memory_extract = importlib.reload(memory_extract)           # the real pass again, on the stand-in model
memory_pass._WORKER["caught_up"] = True                   # the catch-up sweep is checked below
msgs = chat_file("c_router", "Router", [("my router keeps dropping", "Try channel 6.")])
memory_pass.turn_ended("c_router", msgs, "m")
check("the pass doesn't run right after the turn", not runs)
memory_pass.turn_started("c_router")
time.sleep(0.3)
check("a new turn in the chat starts the wait again", not runs)
deadline = time.time() + 8
while time.time() < deadline and not runs:
    time.sleep(0.2)
check("once the chat is quiet, the pass runs", len(runs) == 1 and memory_cards.get("c_router")["goal"] == "Fix the router")
memory_pass.turn_ended("c_router", msgs, "m")
time.sleep(2.5)
check("with nothing new in the chat, it doesn't run again", len(runs) == 1)
memory_pass.now("rem_9", [{"role": "user", "content": "Reminder: stand up"}, {"role": "assistant", "content": "Up!"}])
check("a reminder's chat gets no card", memory_cards.get("rem_9") is None)
# A run that ended before its chat went quiet: the next one catches it up.
chat_file("c_left", "Left over", [("help me pick a laptop bag", "Sure.")], at=time.time() - 60)
runs.clear()
memory_pass._catch_up()
check(f"a recent chat that never had its pass is caught up {sorted(memory_pass.pending())}",
      "c_left" in memory_pass.pending() and "c_router" not in memory_pass.pending())
memory_pass.flush()
check("and gets it", len(runs) >= 1 and memory_cards.get("c_left") is not None)

# ---- which model: one on this PC first, else the agent's one-shot
import importlib  # noqa: E402
memory_pass = importlib.reload(memory_pass)
from hearth import llmserver  # noqa: E402
llmserver.helper_status = lambda: {"running": True, "url": "http://127.0.0.1:9/v1"}
check("a model loaded on this PC does the pass", memory_pass.choose_llm("x")[1] == "local")
llmserver.helper_status = lambda: {"running": False}
import hearth.subagents as _sa  # noqa: E402
_sa._local_server_has_model = lambda base: False
claude_brain.current = lambda: "copilot"
check("with none, an agent brain does it", memory_pass.choose_llm("x")[1] == "copilot")
importlib.reload(claude_brain)
claude_brain.current = lambda: "cursor-agent"
claude_brain.exe_for = lambda p: ["cursor-agent.exe"]
claude_brain.chosen_model = lambda p: "auto"
ran = []
import subprocess  # noqa: E402
claude_brain.subprocess.run = lambda args, **kw: (ran.append(args), cwds.append(kw.get("cwd"))) and NS(returncode=0, stdout=b'{"card": null}')
cwds = []
check("a chat's title doesn't spend a Cursor request", claude_brain.quick("title?") == "" and not ran)
check(f"the memory pass does, with no tools {ran}", claude_brain.quick("pass", any_agent=True) == '{"card": null}'
      and ran and ran[0][:4] == ["cursor-agent.exe", "-p", "--output-format", "text"])
claude_brain.current = lambda: "codex"
claude_brain.quick("title?")
check(f"a one-shot runs in a folder of its own, gone after ({cwds[-1:]})",
      len(cwds) == 2 and os.path.basename(cwds[-1] or "").startswith("hearth-once-") and not os.path.exists(cwds[-1]))


async def fake_once(provider, prompt):
    return "acp:" + provider
claude_brain._acp_once = fake_once
claude_brain.current = lambda: "opencode"
check("an ACP agent answers the pass once, on its own", claude_brain.quick("pass", any_agent=True) == "acp:opencode")

# ---- what a reply had in mind, as each memory tool brings it
memory.save("favourite-editor", "user", "Uses VS Code with Vim keys", "VS Code", force=True, source_chat="c_phone")
out = []
trail = memory_cards.Trail(lambda k, **f: out.append((k, f)))
trail("tool_call", name="memory_recall", args={"query": "editor"})
trail("tool_result", name="memory_recall", content=memory.recall("editor"))
trail("tool_call", name="search_chats", args={"query": "pixel"})
trail("tool_result", name="search_chats", content='2 found:\n\n[1] Phone under 30k  (2026-10-27 10:00, message #0)\n    convo_id=c_phone')
used = [f for k, f in out if k == "context_used"]
last_items = used[-1]["threads"] if used else []
check(f"the list grows with each fact and search, with the chat each came from {last_items}",
      [i["kind"] for i in last_items] == ["fact", "search"] and last_items[0]["chat"] == "c_phone"
      and last_items[0]["chat_title"] == "Phone under 30k" and "pixel" in last_items[1]["topic"])
fe, _ = memory._read_body(os.path.join(memory.MEM_DIR, memory._slug("favourite-editor") + ".md"))
check("a fact that went into a reply has last_used", bool(fe.get("last_used")))
check("done with a card puts it away", memory_cards.act("card", "c_trip", "done")["ok"]
      and memory_cards.get("c_trip")["status"] == "done")
check("wrong / forget on a fact removes it", memory_cards.act("fact", "favourite-editor", "forget")["ok"]
      and "favourite-editor" not in " ".join(memory.fact_titles()))

# The terminal says each only once as the list grows.
from hearth import term_render  # noqa: E402
lines = []
turn = term_render.Turn(out=lines.append, show_thinking=False, model="m")
turn.emit("context_used", threads=[{"topic": "Goa trip"}])
turn.emit("context_used", threads=[{"topic": "Goa trip"}, {"topic": "Searched \"pixel\""}])
check(f"the terminal names what it had in mind, once each {lines}",
      sum("Goa trip" in l for l in lines) == 1 and any("pixel" in l for l in lines))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all memory card checks passed")
