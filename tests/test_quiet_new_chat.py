"""A new chat is quiet, like ChatGPT's: the mark, one line, and by the box at
most three one-line suggestions (chats still going on first, then reminder
ideas), nothing else. It said "Inglen is ready." with a sentence under it
whatever the assistant was called. Chromium, temp HOME and workspace.

    python tests/test_quiet_new_chat.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-quiet-home-")
WS = os.path.join(HOME, "Inglen")
os.makedirs(os.path.join(WS, "logs"))
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "tour_done": True, "llm_provider": "cursor-agent", "agent_name": "Friday"}, f)
with open(os.path.join(WS, "logs", "reminder_ideas.json"), "w", encoding="utf-8") as f:
    json.dump({"ideas": [{"title": "Weekly plan", "ask": "Every Monday at 9am, remind me to plan the week"},
                         {"title": "Water the plants", "ask": "Every Sunday, remind me to water the plants"}]}, f)
sys.path.insert(0, ROOT)

from hearth import memory_cards, offers, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


memory_cards.open_cards = lambda limit=3: [{"goal": "Trip to Rome in May", "chat": "c_rome", "ago": "yesterday"},
                                           {"goal": "Tax form", "chat": "c_tax", "ago": "today"},
                                           {"goal": "Garden plan", "chat": "c_garden", "ago": "monday"}]
rows = offers.rows()
check(f"three rows at most, chats to carry on first, then an idea {[r['text'] for r in rows]}",
      [r["kind"] for r in rows] == ["chat", "chat", "idea"] and rows[2]["text"] == "Weekly plan")

srv = web.serve("127.0.0.1", 0)
port = srv.server_address[1]
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(f"http://127.0.0.1:{port}/")
    pg.wait_for_selector(".empty .empty-offer", timeout=30000)
    time.sleep(0.5)
    title = pg.inner_text(".empty .empty-title")
    check(f"one line, in the name it goes by here ({title!r})", title == "What can Friday do for you?")
    check("nothing under it", pg.inner_text(".empty .empty-sub").strip() == "")
    texts = pg.locator(".empty .empty-offer").all_inner_texts()
    check(f"the suggestions are one-line rows, not cards {texts}",
          [t.strip() for t in texts] == ["Trip to Rome in May", "Tax form", "Weekly plan"]
          and pg.locator(".empty .empty-offer svg").count() == 3)
    near = pg.evaluate("""() => { const o = document.querySelector('.empty-offers').getBoundingClientRect(),
      i = document.getElementById('input').getBoundingClientRect(), m = document.querySelector('.empty-mark').getBoundingClientRect(),
      c = document.getElementById('chat-body').getBoundingClientRect();
      return { gap: i.top - o.bottom, mid: Math.abs((m.top + m.bottom) / 2 - (c.top + c.bottom) / 2) }; }""")
    check(f"they sit by the box, the mark in the middle {near}", 0 <= near["gap"] < 60 and near["mid"] < 120)
    pg.click(".empty-offer >> text=Weekly plan")
    check("an idea puts its words in the box", pg.input_value("#input") == "Every Monday at 9am, remind me to plan the week")
    check("no welcome card", not pg.is_visible("#welcome-card"))
    b.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all quiet new chat checks passed")
