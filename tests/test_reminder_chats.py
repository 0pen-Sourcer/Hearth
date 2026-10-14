"""A reminder that comes due is said in a chat of its own.

It used to be queued for the window, which read it out in whichever chat was
open, so a stretch reminder landed in the middle of a chat about something
else. Now each reminder has its own chat, marked unread until it is opened.

Uses a throwaway workspace and a stand-in brain, so no model runs.

    .\\.venv\\Scripts\\python.exe tests\\test_reminder_chats.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

work = tempfile.mkdtemp(prefix="hearth-remchat-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1",
                  LOCAL_API_BASE="http://127.0.0.1:9/v1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import chat_sync, headless, reminders, subagents, web   # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


asked = []


async def stand_in(prompt, *, emit, history=None, permission_check=None, **_kw):
    asked.append({"prompt": prompt, "history": list(history or []),
                  "denies": permission_check("run_command", {}) if permission_check else None})
    emit("assistant", content=f"Time to stretch ({len(asked)}).")
    return 0


headless.run_once = stand_in

r = reminders.set_reminder("1m", "Stand up and stretch", recurring="every 2 hours")["reminder"]
# Due now: the watcher's first pass takes it.
items = reminders._load_all()
items[0]["due_iso"] = "2000-01-01T00:00:00"
reminders._save_all(items)
toasts = []
reminders.start_watcher(lambda t, b: toasts.append((t, b)))
cid = reminders.chat_id_for(r)
for _ in range(100):
    if chat_sync.load(cid):
        break
    time.sleep(0.1)
reminders.stop_watcher()
d = chat_sync.load(cid) or {}
check(f"a due reminder gets a chat of its own, with JARVIS's words in it {d.get('messages')}",
      d.get("source") == "reminder" and d.get("title") == "Stand up and stretch"
      and [m["content"] for m in d.get("messages") or []] == ["Time to stretch (1)."])
check("nothing is left queued for whichever chat is open", subagents.drain_pending_notifications() == [])
check("the turn can't run anything that needs a yes, since nobody is there to give one",
      asked and asked[0]["denies"] == "deny")
meta = next((c for c in web._list_convos() if c["id"] == cid), {})
check(f"it is listed as a reminder's chat, unread {meta}", meta.get("source") == "reminder" and meta.get("unread") is True)

# Comes due again: the same chat, with what was said before.
reminders.deliver(r, "due again").join(10)
d = chat_sync.load(cid) or {}
check("a repeating reminder keeps to its one chat, and sees what it said last time",
      len(d.get("messages") or []) == 2 and asked[-1]["history"] == [{"role": "assistant", "content": "Time to stretch (1)."}])

# Ideas on the Reminders page are made from what was talked about, not a fixed list.
chat_sync.post("c_plain", "Exam form plans", "window", [{"role": "user", "content": "help me with the exam form"}])
seen = []
web.one_shot = lambda prompt, **_k: (seen.append(prompt), 'Sure: [{"title": "Exam form", '
                                     '"ask": "Remind me at 10am to finish the exam form"}]')[1]
ideas = web.reminder_ideas()
check(f"ideas are asked of the brain with the chats so far (not reminders' own) and what is already set {ideas}",
      ideas == [{"title": "Exam form", "ask": "Remind me at 10am to finish the exam form", "repeats": False}]
      and "Exam form plans" in seen[0] and "Stand up and stretch" in seen[0])
web.reminder_ideas()
check("and kept instead of asked on every visit", len(seen) == 1)
from hearth import memory                                            # noqa: E402
memory.save("Board exams", "project", "his board exams", "Writes the CBSE board exams in March; revises maths every evening.")
web.reminder_ideas(fresh=True)
check("what JARVIS remembers about the person is in what the ideas are made from, not just memory titles",
      "revises maths every evening" in seen[-1])
check("and the prompt knows today's date, so something coming up in memory can be set ahead of it",
      time.strftime("%Y") in seen[-1] and "coming up" in seen[-1])

# Showing a reminder on the desktop crashed before it showed anything (a name
# left over from renaming the environment settings).
try:
    reminders._push_phone("Hearth reminder", "check")
    pushed = "ran"
except NameError as e:
    pushed = f"crashed: {e}"
check(f"a reminder's phone push runs without crashing the desktop notice ({pushed})", pushed == "ran")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all reminder chat checks passed")
