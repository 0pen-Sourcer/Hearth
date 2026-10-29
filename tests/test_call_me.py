"""The agent calling the phone by itself: never by default (each call runs the
brain), only for the reasons switched on, never in quiet hours (a span past
midnight counts on both sides of it), a question only once it has waited the
set minutes, not while someone is at the PC, one ring at a time, the same thing
once, a gap after a declined or missed call. A reminder coming due and work
that finished ask for a call; the phone bridge rings (a notification with the
agent's name from settings, and the in-app ring), turns an unanswered ring into
a missed call, and starts a call that says why it called; the agent's goodbye
hangs up from the PC.

    .\\.venv\\Scripts\\python.exe tests\\test_call_me.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-call-me-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HOME"] = os.environ["USERPROFILE"] = WS

import numpy as np  # noqa: E402

from hearth import call as C, call_me as CM, channel_core as cc, live_bus, realtime_voice as rv  # noqa: E402
from hearth import relay_bridge as RB, reminders, subagents, voice as V  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def wait_for(cond, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return cond()


def settings(**kv):
    with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(kv, f)


published: list = []
live_bus.publish = lambda ev: published.append(ev)
away = 9999.0                                      # nobody at the PC
reminders.idle_seconds = lambda: away
at = lambda h, m=0: datetime(2026, 10, 29, h, m)   # noqa: E731

# ---------------------------------------------------------------- nothing by default
settings(agent_name="FRIDAY")
r = CM.rules()
check("by default the agent never calls", not r["call_me"]
      and all(CM.may_call(x, r, at(12))[0] is False for x in CM.REASONS))
check("and says why: calls are off", CM.may_call("reminder", r, at(12)) == (False, "off"))
check("a reminder asks for nothing", CM.request("reminder", "stretch", chat="rem_1") is False and not published)
check("nor a question that has waited an hour", not CM.question_due(time.time() - 3600, r))
check("and a question waits as long as it always has", CM.ask_wait(300, r) == 300)

# ---------------------------------------------------------------- the switches
on = dict(call_me=True, call_me_quiet=False)
r = CM.rules(on)
check("with calls on, each reason may call", all(CM.may_call(x, r, at(12)) == (True, "") for x in CM.REASONS))
check("and nothing that isn't a reason does", CM.may_call("whim", r, at(12)) == (False, "unknown"))
for reason, key in CM.REASONS.items():
    r = CM.rules(dict(on, **{key: False}))
    others = [x for x in CM.REASONS if x != reason]
    check(f"{reason} switched off: no call for it, the others still may",
          CM.may_call(reason, r, at(12)) == (False, "reason") and all(CM.may_call(x, r, at(12))[0] for x in others))

# ---------------------------------------------------------------- quiet hours
r = CM.rules(dict(call_me=True))                    # quiet hours on by default, 22:00 to 08:00
check("quiet hours are on by default, 22:00 to 08:00",
      r["call_me_quiet"] and (r["call_me_quiet_from"], r["call_me_quiet_to"]) == ("22:00", "08:00"))
check("quiet at 23:30 and at 03:00", CM.may_call("reminder", r, at(23, 30)) == (False, "quiet")
      and CM.may_call("reminder", r, at(3)) == (False, "quiet"))
check("not at 08:00 or at noon, and quiet again from 22:00",
      CM.may_call("reminder", r, at(8))[0] and CM.may_call("reminder", r, at(12))[0]
      and not CM.may_call("reminder", r, at(22))[0] and CM.may_call("reminder", r, at(21, 59))[0])
r = CM.rules(dict(call_me=True, call_me_quiet_from="13:00", call_me_quiet_to="14:30"))
check("a span within the day: quiet inside it only", not CM.may_call("job", r, at(13, 45))[0]
      and CM.may_call("job", r, at(14, 30))[0] and CM.may_call("job", r, at(12, 59))[0])
r = CM.rules(dict(call_me=True, call_me_quiet=False))
check("quiet hours switched off: it may call at 03:00", CM.may_call("job", r, at(3))[0])
r = CM.rules(dict(call_me=True, call_me_quiet_from="nonsense"))
check("a time it can't read is no quiet hours, not no calls", CM.may_call("job", r, at(3))[0])

# ---------------------------------------------------------------- how long a question waits
r = CM.rules(dict(on, call_me_wait_min=5))
now = time.time()
check("a question 4 minutes old doesn't call yet", not CM.question_due(now - 240, r, now, at(12)))
check("at 5 minutes it does", CM.question_due(now - 300, r, now, at(12)))
check("but not in quiet hours", not CM.question_due(now - 3600, CM.rules(dict(call_me=True, call_me_wait_min=5)), now, at(23)))
check("nor with questions switched off", not CM.question_due(now - 3600, CM.rules(dict(on, call_me_questions=False)), now, at(12)))
check(f"and a question waits long enough to be called about and answered ({CM.ask_wait(300, r)} s)",
      CM.ask_wait(300, r) >= 5 * 60 + CM.RING_S + 60)
check("a wait it can't read falls back to the default", CM.rules(dict(on, call_me_wait_min="x"))["call_me_wait_min"] == 3)

# ---------------------------------------------------------------- at the PC
r = CM.rules(on)
check("someone at the PC: no call", CM.want("job", "the backup finished", r=r, now=at(12), idle=30) is None)
ev = CM.want("job", "The backup finished", r=r, now=at(12), idle=600)
check("away from it: a call", ev is not None and ev["reason"] == "job")
check(f"which says why it called ({ev['say']!r})", ev["say"] == "Hi, I'm calling because the backup finished.")
check("a reminder's call says the reminder", CM.opener("reminder", "Call mom") == "Hi, it's about your reminder: Call mom.")

# ---------------------------------------------------------------- one ring at a time
rings = CM.Rings()
a = rings.start({"key": "k1"}, "r1", at=100)
check("a ring starts", a is not None and rings.now["id"] == "r1")
check("another can't while it rings", rings.start({"key": "k2"}, "r2", at=101) is None)
check("answered, it stops and says what it was", rings.take("r1")["key"] == "k1" and rings.now is None)
check("the same thing never rings twice", rings.start({"key": "k1"}, "r3", at=200) is None)
rings.start({"key": "k4"}, "r4", at=300)
rings.drop("r4", at=300)
check("after a declined or missed ring, nothing for a while",
      rings.start({"key": "k5"}, "r5", at=300 + CM.GAP_S - 1) is None)
check("then it may again", rings.start({"key": "k5"}, "r6", at=300 + CM.GAP_S + 1) is not None)

# ---------------------------------------------------------------- what asks for a call
reminders.deliver = lambda r, note, wait_s=600: None


class Fast:
    def __init__(self):
        self.stop = False

    def wait(self, t):
        time.sleep(0.15)
        return self.stop

    def is_set(self):
        return self.stop

    def clear(self):
        pass

    def set(self):
        self.stop = True


def due_now(what):
    rem = reminders.set_reminder("in 1 hour", what)["reminder"]
    with reminders._store_lock():
        items = reminders._load_all()
        for x in items:
            if x.get("id") == rem.get("id"):
                x["due_iso"] = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        reminders._save_all(items)
    return rem


settings(agent_name="FRIDAY", call_me=True, call_me_quiet=False)
due_now("water the plants")
reminders._watcher_stop = Fast()
reminders.start_watcher(lambda t, b: None)
time.sleep(0.6)
check("a reminder missed while Inglen was closed doesn't call, even with calls on", not published)
rem = due_now("stretch your back")
check("a reminder coming due, with calls on, asks for a call",
      wait_for(lambda: any(e.get("reason") == "reminder" for e in published), 3))
ev = next(e for e in published if e.get("reason") == "reminder")
check(f"in the reminder's own chat, saying what it is ({ev['c']}, {ev['why']!r})",
      ev["c"] == reminders.chat_id_for(rem) and ev["why"] == "stretch your back" and ev["kind"] == "call_me")
published.clear()
settings(agent_name="FRIDAY", call_me=True, call_me_quiet=False, call_me_reminders=False)
due_now("drink water")
time.sleep(0.6)
check("with reminders switched off, it doesn't", not published)
reminders._watcher_stop.set()
time.sleep(0.3)

settings(agent_name="FRIDAY", call_me=True, call_me_quiet=False)
subagents.enqueue_notification(source="command", name="backup", status="completed", result_text="done",
                               summary="The backup finished")
check("background work that finished asks for a call",
      any(e.get("reason") == "job" and e["why"] == "The backup finished" for e in published))
published.clear()
settings(agent_name="FRIDAY", call_me=True, call_me_quiet=False, call_me_jobs=False)
subagents.enqueue_notification(source="command", name="build", status="failed", result_text="x", summary="The build failed")
check("with jobs switched off, it doesn't", not published)
settings(agent_name="FRIDAY", call_me=True, call_me_quiet=False)
away = 10.0
subagents.enqueue_notification(source="command", name="tests", status="completed", result_text="y", summary="Tests passed")
check("nor while someone is at the PC", not published)
away = 9999.0

# ---------------------------------------------------------------- the bridge rings
os.environ["HEARTH_RELAY_URL"] = "http://127.0.0.1:9"
bridge = RB.Bridge()
pushed, notes = [], []
bridge.adapter._push = lambda chat, obj: pushed.append((chat, dict(obj))) or "m"
bridge.adapter.notify = lambda chat, obj, urgent=False: notes.append((chat, dict(obj), urgent))
room = types.SimpleNamespace(phone=b"p" * 32, chat="phone1", peer_up=True, chan=object(), call=None)
bridge.adapter.rooms["phone1"] = room
CM.RING_S = 0.6
settings(agent_name="FRIDAY")
bridge.on_window_event({"kind": "call_me", "reason": "job", "why": "The backup finished", "say": "Hi.", "key": "a"})
time.sleep(0.3)
check("calls turned off after it was asked for: the bridge doesn't ring", not pushed and not notes)
settings(agent_name="FRIDAY", call_me=True, call_me_quiet=False)
bridge.on_window_event(CM.want("job", "The backup finished", key="b", idle=999))
check("allowed: the phone rings", wait_for(lambda: notes, 2))
chat, note, urgent = notes[0]
check(f"a notification named for the agent in settings, saying why ({note['title']!r}: {note['body']!r})",
      note["kind"] == "ring" and note["title"] == "FRIDAY is calling" and note["body"] == "The backup finished" and urgent)
frame = next((o for _, o in pushed if o.get("type") == "call.ring"), {})
check("and the app, open, gets the call on its own screen", frame.get("ring") == note["ring"] and frame.get("name") == "FRIDAY"
      and frame.get("c", "").startswith("call_"))
rid = note["ring"]
bridge.on_window_event(CM.want("job", "Another thing finished", key="c", idle=999))
time.sleep(0.3)
check("one ring at a time", len(notes) == 1)
check("unanswered, it becomes a missed call", wait_for(lambda: any(n[1]["kind"] == "missed" for n in notes), 3))
missed = next(n[1] for n in notes if n[1]["kind"] == "missed")
check(f"in the same notification's place, opening the chat ({missed['title']!r})",
      missed["ring"] == rid and missed["title"] == "Missed call from FRIDAY" and missed["c"] == frame["c"])
check("and the app's ring stops", any(o.get("type") == "call.unring" and o.get("missed") for _, o in pushed))
n_notes = len(notes)
bridge.on_window_event(CM.want("job", "Yet another", key="d", idle=999))
time.sleep(0.3)
check("right after a missed call, it doesn't ring again", len(notes) == n_notes)

# Answered: the call starts by saying why, in that chat.
bridge.rings = CM.Rings()
CM.RING_S = 30
rid = bridge.ring(CM.want("reminder", "Call mom", chat="rem_9", key="e", idle=999))
check("a ring while a call is on: none", rid and (setattr(room, "call", types.SimpleNamespace(ended=False)) or True)
      and bridge.ring(CM.want("job", "x", key="f", idle=999)) is None)
room.call = None
ring = bridge.answered(rid)
check("answering it hands the call what to say first", ring and ring["say"] == "Hi, it's about your reminder: Call mom."
      and ring["c"] == "rem_9")
check("and the ring stops", bridge.rings.now is None)
bridge.decline("nothing")
check("a decline for no ring does nothing", bridge.rings.now is None)
rid = bridge.ring(CM.want("job", "z", key="g", idle=999))
bridge.decline(rid)
check("declined: it stops, the app is told", bridge.rings.now is None
      and any(o.get("type") == "call.unring" and o.get("ring") == rid and not o.get("missed") for _, o in pushed))

# A question that has waited.
bridge.rings = CM.Rings()
settings(agent_name="FRIDAY", call_me=True, call_me_quiet=False, call_me_wait_min=2)
bridge.adapter.asks["c_q"] = "q1"
bridge.adapter.asked["q1"] = {"at": time.time() - 60, "key": "c_q", "c": "c_q", "chat": "phone1",
                              "text": "May I delete old.log?", "tap": True}
notes.clear()
bridge.check_questions(idle=999)
check("a question waiting 1 minute of 2 doesn't call", not notes)
bridge.adapter.asked["q1"]["at"] = time.time() - 125
bridge.check_questions(idle=999)
check("at 2 minutes it does, in the question's chat",
      notes and notes[-1][1]["kind"] == "ring" and notes[-1][1]["c"] == "c_q")
ring = bridge.answered(bridge.rings.now["id"]) if bridge.rings.now else {}
check("and the call will ask it, with its id and that it needs a tap",
      ring.get("ask") == "q1" and ring.get("tap") is True and "old.log" in ring.get("question", ""))
notes.clear()
bridge.check_questions(idle=999)
check("once only", not notes)
bridge.adapter.asks["c_q"] = "q2"
bridge.adapter.asked["q2"] = dict(bridge.adapter.asked["q1"], at=time.time() - 999, rang=False)
bridge.adapter.asks.pop("c_q")
bridge.check_questions(idle=999)
check("an answered question never calls", not notes)

# ---------------------------------------------------------------- the call it made, and hanging up
V._load_engine = lambda allow_download=False: ("kokoro", None)
read: list = []
V._synth_chunk = lambda kind, obj, chunk, voice, speed: (read.append(chunk) or np.zeros(1200, dtype=np.int16), 24000)


class Recorder:
    def __init__(self):
        self.go = threading.Event()

    def feed_audio(self, data, original_sample_rate=16000):
        pass

    def text(self):
        self.go.wait()
        self.go.clear()
        return ""

    def clear_audio_queue(self):
        pass

    def shutdown(self):
        self.go.set()


rv._REALTIME_AVAILABLE = True
rv._build_recorder = lambda sess=None: Recorder()
sent, posted = [], []
call = C.Call(sent.append, lambda t: "ran", cid="c_q", ref="r9", post=posted.append,
              opener={"say": "Hi, I need your answer on something.", "ask": "q1", "question": "May I delete old.log?", "tap": True})
call.start()
check("a call it made says why first, kept in the chat", wait_for(lambda: posted == ["Hi, I need your answer on something."]))
check("and out loud", wait_for(lambda: any("answer on something" in c for c in read)))
check("then asks the question it called about, with the tap it needs",
      wait_for(lambda: any(f.get("state") == "asking" and f.get("ask") == "q1" and f.get("tap") for f in sent)))
check("and listens once that has been heard",
      wait_for(lambda: [f.get("state") for f in sent if f.get("type") == "call.state"][-1:] == ["listening"], 10))

adapter = RB.Relay(None)
adapter._push = lambda chat, obj: "m"
adapter.rooms["phone1"] = types.SimpleNamespace(call=call)
cc._TURN.ctx = {"c": "c_q", "t": "t1"}
check("a turn in the call is told it is one", adapter.call_note("phone1", "c_q") == RB.CALL_NOTE
      and "end_session" in RB.CALL_NOTE)
check("a turn in another chat isn't", adapter.call_note("phone1", "c_other") == "")
sent.clear()
call.begin()
adapter.step("phone1", "Ending the session", tool="end_session")
check("its goodbye doesn't end the call before it is said", not call.ended)
call.done("Talk soon, bye.")
check("once said, the PC hangs up", wait_for(lambda: call.ended, 8)
      and any(f.get("type") == "call.ended" and f.get("by") == "pc" and f.get("ref") == "r9" for f in sent))
check("and the goodbye was heard first", any("bye" in c for c in read))
cc._TURN.ctx = None

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all call-me checks passed")
