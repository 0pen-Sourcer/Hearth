"""When a coding agent's finished turn calls the phone. Only for work handed off from the
phone or Inglen, never for every turn of a session someone is working in at the PC; never
for a turn with nothing to say, a bare "OK", or a usage limit (a notification says that);
one ring per session per stretch of work, and longer gaps after each call missed in a row.

    .\\.venv\\Scripts\\python.exe tests\\test_agent_calls.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-agent-calls-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HOME"] = os.environ["USERPROFILE"] = WS
json.dump({"call_me": True, "call_me_quiet": False}, open(os.path.join(WS, "settings.json"), "w"))

from hearth import agent_sessions as S, call_me as CM, relay_bridge as RB  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


AWAY = 10 ** 6                       # nobody at the PC
AT_DESK = S.chat_id("claude-code", "11111111-1111-1111-1111-111111111111")
FROM_PHONE = S.chat_id("claude-code", "22222222-2222-2222-2222-222222222222")
LONG = "I fixed the failing test and pushed the branch; the suite passes now."


class Adapter:
    rooms: dict = {}


bridge = RB.Bridge.__new__(RB.Bridge)
bridge.adapter, bridge.phone_sessions = Adapter(), {}
bridge.phone_sessions[FROM_PHONE] = __import__("time").time()


def done(c, body, **k):
    return dict({"kind": "done", "title": "Claude Code · Plum", "body": body, "c": c}, **k)


# The watcher itself, one look: three turns end, one session at the desk and one sent from the phone.
class Phone:
    phone, peer_up, call = b"p", False, None


rang: list = []
Adapter.rooms = {"p1": Phone()}
Adapter.notify = lambda self, chat, n, urgent=False: None
Adapter._push = lambda self, chat, obj: None
bridge.ring, bridge.agent_runs, bridge.stopping = (lambda ev: rang.append(ev) or "rid"), {}, False
notes = [done(AT_DESK, LONG), done(FROM_PHONE, LONG),
         done(FROM_PHONE, RB.stopped_note("Claude Code", "You've hit your 5-hour limit · resets 5:30pm"), stopped=True)]


def one_look(self, rows, now_ms, busy):
    bridge.stopping = True
    self.moved, self.quiet = [], []
    return notes


RB.AgentWatch.EVERY_S, RB.AgentWatch.look, S.listing = 0, one_look, (lambda *a, **k: [])
real_at_pc, CM.at_pc = CM.at_pc, (lambda idle=None: False)
bridge.watch_agents()
CM.at_pc = real_at_pc
check(f"of a desk session's turn, phone work finishing and a usage limit, only the phone work rings ({[e.get('c') for e in rang]})",
      [e.get("c") for e in rang] == [FROM_PHONE])
Adapter.rooms = {}


check("a session being worked in at the PC never rings when a turn ends",
      bridge.agent_call(done(AT_DESK, LONG), idle=AWAY) is None)
ev = bridge.agent_call(done(FROM_PHONE, LONG), idle=AWAY)
check(f"work sent from the phone rings when it finishes, in that session ({(ev or {}).get('c')})",
      ev is not None and ev["c"] == FROM_PHONE)
check("not for a bare OK", bridge.agent_call(done(FROM_PHONE, "OK."), idle=AWAY) is None)
limit = RB.stopped_note("Claude Code", "You've hit your 5-hour limit · resets 5:30pm")
check("not for a usage limit", bridge.agent_call(done(FROM_PHONE, limit, stopped=True), idle=AWAY) is None)
check("an error that isn't a limit still rings",
      bridge.agent_call(done(FROM_PHONE, "Claude Code stopped: it couldn't open the project folder.", stopped=True),
                        idle=AWAY) is not None)
check("nor while they're at the PC", bridge.agent_call(done(FROM_PHONE, LONG), idle=5) is None)


# What a finished turn tells the phone at all (AgentWatch._note), from the session's own messages.
def note_for(text, error=False):
    real = S.read
    S.read = lambda agent, sid: {"messages": [{"role": "user", "text": "hi"},
                                              {"role": "assistant", "text": text, "parts": [{"text": text}],
                                               **({"error": True} if error else {})}]}
    try:
        return RB.AgentWatch._note({"agent": "claude-code", "id": "x", "label": "Claude Code", "title": "Plum",
                                    "chat": FROM_PHONE})
    finally:
        S.read = real


check("a turn whose whole reply is \"No response requested.\" tells the phone nothing",
      note_for("No response requested.") is None)
lim = note_for("You've hit your 5-hour limit · resets 5:30pm", error=True)
check(f"a usage limit is a notification, in plain words, and no call ({(lim or {}).get('body')})",
      lim is not None and lim["body"] == "Claude Code stopped: its usage limit resets at 5:30 pm"
      and bridge.agent_call(lim, idle=AWAY) is None)

# One ring per session per stretch of work, however many turns end in it.
rings = CM.Rings()
first = rings.start(ev, "r1", at=1000)
rings.take("r1")
again = bridge.agent_call(done(FROM_PHONE, LONG + " And the docs too."), idle=AWAY)
check("the next turn in the same stretch doesn't ring again",
      first is not None and rings.start(again, "r2", at=1000 + 600) is None)
check("a later stretch may", rings.start(again, "r3", at=1000 + RB.STRETCH_S + 1) is not None)

# A missed call backs off, longer each time in a row; an answered one resets it.
rings = CM.Rings()
rings.start({"key": "a"}, "m1", at=0)
rings.drop("m1", at=0, missed=True)
first_gap = rings.quiet_until
rings.start({"key": "b"}, "m2", at=first_gap + 1)
rings.drop("m2", at=first_gap + 1, missed=True)
second_gap = rings.quiet_until - (first_gap + 1)
check(f"a missed call backs off, and the next miss longer ({first_gap:.0f} s, then {second_gap:.0f} s)",
      first_gap == CM.GAP_S and second_gap == 2 * CM.GAP_S)
rings.start({"key": "c"}, "m3", at=10 ** 5)
rings.take("m3")
check("an answered call resets it", rings.missed == 0)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent call checks passed")
