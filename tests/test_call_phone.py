"""A call from the phone, the parts the call screen leans on, with a stand-in
voice and recognizer so it runs anywhere: an approval says whether a spoken yes
will do, a yes that needs the button asks for the tap out loud, one that counts
is shown answered, the call goes back to listening once the reply has been
heard, a tap on the orb stops the voice, a reply typed during the call is
spoken, voice and language change from the call, the question's frame carries
the tap bit, call frames never wait in the mailbox, and a phone that drops
off for too long ends the call.

    .\\.venv\\Scripts\\python.exe tests\\test_call_phone.py
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-call-phone-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

import numpy as np  # noqa: E402

from hearth import call as C, channel_core as cc, env as E, realtime_voice as rv, relay_bridge as RB, voice as V  # noqa: E402

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


# A voice that makes 50 ms of quiet per ten letters, and notes which voice read what.
read_by: list = []
V._load_engine = lambda allow_download=False: ("kokoro", None)


def _synth(kind, obj, chunk, voice, speed):
    read_by.append((V.current_voice(), chunk))
    return np.zeros(int(24000 * 0.05 * max(1, len(chunk) / 10)), dtype=np.int16), 24000


V._synth_chunk = _synth
built: list = []


class Recorder:
    """Stands in for the speech recognizer: says what the test hands it."""
    def __init__(self):
        self.q: list = []
        self.go = threading.Event()

    def feed_audio(self, data, original_sample_rate=16000):
        pass

    def text(self):
        self.go.wait()
        self.go.clear()
        return self.q.pop(0) if self.q else ""

    def clear_audio_queue(self):
        pass

    def shutdown(self):
        self.go.set()


rv._REALTIME_AVAILABLE = True
rv._build_recorder = lambda sess=None: built.append(Recorder()) or built[-1]


def hear(text):
    built[-1].q.append(text)
    built[-1].go.set()


def states(sent):
    return [f.get("state") for f in sent if f.get("type") == "call.state"]


answers = {"yes please": "needs_tap"}
sent, said, saved = [], [], []


def say(text):
    said.append(text)
    return answers.get(text, "answered" if text in ("yeah go ahead", "no") else "ran")


call = C.Call(sent.append, say, cid="c_call", save=saved.append, ref="r1")
call.start()
check("the call listens once its recognizer is up", wait_for(lambda: "listening" in states(sent)))
check("and tells the phone its voice, the voices and the languages",
      any(f.get("type") == "call.voice" and f.get("voice") and {"id": "bf_emma", "name": "Emma", "about": "English (UK), female"} in f.get("voices", [])
          and {"code": "", "name": "Detect it"} in f.get("languages", []) for f in sent))

# ---------------------------------------------------------------- approvals
sent.clear(); read_by.clear()
call.ask("Before I go on: may I delete old.log?", tap=True, aid="a1")
asking = [f for f in sent if f.get("state") == "asking"]
check("an approval reaches the call screen with its words, its id and that it needs a tap",
      asking and asking[0].get("tap") is True and asking[0].get("ask") == "a1" and "delete old.log" in asking[0]["words"])
check("and is said aloud, with the ask for a tap", wait_for(lambda: any(C.TAP_LINE in c for _, c in read_by)))
wait_for(lambda: states(sent)[-1:] == ["listening"], 15)   # the yes comes once it has finished saying so
sent.clear(); read_by.clear()
hear("yes please")
check("a spoken yes to it asks for the tap again, on screen",
      wait_for(lambda: any(f.get("state") == "asking" and f.get("again") and f.get("tap") for f in sent)))
check("and out loud", wait_for(lambda: any(C.TAP_LINE in c for _, c in read_by)))
check("and isn't shown as an answer", not any(f.get("type") == "asked" for f in sent))
wait_for(lambda: states(sent)[-1:] == ["listening"])

call.ask("Before I go on: may I open Chrome?", tap=False, aid="a2")
check("an ordinary approval says no tap is needed", [f for f in sent if f.get("ask") == "a2"][0].get("tap") is False)
sent.clear()
hear("yeah go ahead")
got = wait_for(lambda: any(f.get("type") == "asked" for f in sent)) and [f for f in sent if f.get("type") == "asked"][0]
check(f"a spoken yes that counts is shown answered, for that question, in the call's chat ({got})",
      got and got["ask"] == "a2" and got["yes"] is True and got.get("c") == "c_call")

# ---------------------------------------------------------------- back to listening, and stopping
wait_for(lambda: states(sent)[-1:] == ["listening"])
sent.clear()
call.begin()
call.live("Sure, I can open that for you right now. ")
call.done("Sure, I can open that for you right now. It is open.")
check("while the reply plays the call isn't back to listening",
      wait_for(lambda: any(f.get("type") == "call.down" for f in sent)) and states(sent)[-1:] != ["listening"])
check("once it has been heard, it is", wait_for(lambda: states(sent)[-1:] == ["listening"], 8))

sent.clear()
call.begin()
call.live("Here is a long answer, with many sentences. ")
wait_for(lambda: any(f.get("type") == "call.down" for f in sent))
call.hush()
check("a tap on the orb stops the voice and clears the phone's audio",
      any(f.get("type") == "call.flush" for f in sent) and states(sent)[-1:] == ["listening"])
time.sleep(0.3)
n = sum(1 for f in sent if f.get("type") == "call.down")
call.live("Here is a long answer, with many sentences. And this one comes after the tap. ")
call.done("Here is a long answer, with many sentences. And this one comes after the tap. ")
time.sleep(0.8)
check("and the rest of that reply stays unsaid", sum(1 for f in sent if f.get("type") == "call.down") <= n + 1)

# A message typed during the call: its turn starts in the call's chat, so the reply is spoken.
sent.clear()
call.begin()
call.done("Typed and answered.")
check("a reply to a message typed during the call is spoken",
      wait_for(lambda: any(f.get("type") == "call.down" for f in sent)))

# ---------------------------------------------------------------- settings from the call
wait_for(lambda: states(sent)[-1:] == ["listening"])
sent.clear(); read_by.clear()
call.set(voice="bf_emma")
check(f"a voice picked in the call is kept on the PC ({saved})", {"voice_name": "bf_emma"} in saved)
check("and the phone is told", any(f.get("type") == "call.voice" and f.get("voice") == "bf_emma" for f in sent))
call.begin()
call.done("Hello there.")
check("and the next words are in it", wait_for(lambda: read_by) and read_by[0][0] == "bf_emma")
call.set(voice="not_a_voice")
check("a voice that doesn't exist is refused", V.current_voice() == "bf_emma" and len(saved) == 1)
n_built = len(built)
call.set(language="hi")
check("a language picked in the call is kept on the PC", {"stt_language": "hi"} in saved and E.get("STT_LANGUAGE") == "hi")
check("and the recognizer listens again in it", wait_for(lambda: len(built) == n_built + 1))
call.set(language="xx")
check("a language it can't listen in is refused", E.get("STT_LANGUAGE") == "hi")
call.end()
check("hanging up ends the session", call.ended and rv.session_for(call.lease) is None)
check("and its end names the call it ends", any(f.get("type") == "call.ended" and f.get("ref") == "r1" for f in sent))

# ---------------------------------------------------------------- the relay side
pushed: list = []


class FakeCall:
    def __init__(self):
        self.asked = None

    def ask(self, text, tap=False, aid=""):
        self.asked = (text, tap, aid)


ad = RB.Relay(asyncio.new_event_loop())
ad._push = lambda chat, obj: pushed.append(obj) or "m1"
ad.notify = lambda *a, **k: None
fake = FakeCall()
ad._call_for = lambda chat: fake
hub = cc.Hub(ad, configured_senders=["phone"])
ad.hub = hub
cc._TURN.ctx = {"c": "c_call", "t": "t1"}
hub._busy["c_call"] = {"steer": [], "question": threading.Event(), "tap": True}
ad.ask("phone1", "Before I go on: may I delete old.log?")
ask_frame = next((f for f in pushed if f.get("type") == "ask"), {})
check("the question's frame says it needs a tap", ask_frame.get("tap") is True)
check("and the call is told so, with the question's id",
      fake.asked and fake.asked[1] is True and fake.asked[2] == ask_frame.get("ask"))
pushed.clear()
hub._busy["c_call"]["tap"] = False
ad.ask("phone1", "Before I go on: may I open Chrome?")
check("an ordinary one doesn't", "tap" not in next(f for f in pushed if f.get("type") == "ask") and fake.asked[1] is False)
cc._TURN.ctx = None
hub._busy.clear()


async def relay_room():
    sent_out: list = []
    radapter = types.SimpleNamespace(_push=lambda chat, obj: sent_out.append(obj), rooms={})
    bridge = types.SimpleNamespace(adapter=radapter, hub=hub, pc=None)
    room = RB.Room(bridge, "room1", b"", b"p" * 32)
    room.peer_up, room.chan = False, None
    delivered: list = []

    async def deliver(obj):
        delivered.append(obj)
    room.deliver = deliver
    room.on_call({"type": "call.start", "chat": "c_call"})
    await asyncio.sleep(0.6)
    check(f"call frames aren't kept for a phone that isn't there ({len(sent_out)} sent)", not sent_out)
    room.peer_up, room.chan = True, object()
    room.on_call({"type": "call.hush"})
    await asyncio.sleep(0.3)
    check("a tap on the orb reaches the call", any(f.get("type") == "call.state" for f in sent_out))
    RB.CALL_AWAY_S = 0.3
    the_call = room.call
    await room.on_frame({"t": "peer", "up": False})
    await asyncio.sleep(0.8)
    check("a phone gone for too long ends the call", room.call is None and the_call.ended)
    room.peer_up = True
    room.on_call({"type": "call.up", "data": base64.b64encode(b"\0\0" * 960).decode()})
    room.on_call({"type": "call.up", "data": base64.b64encode(b"\0\0" * 960).decode()})
    await asyncio.sleep(0.1)
    told = [f for f in delivered if f.get("type") == "call.ended"]
    check(f"and a phone still talking into it is told so, once ({len(told)})", len(told) == 1 and told[0].get("gone"))

    # A phone whose page stops ticking (app closed, page frozen with the screen off) while its
    # connection stays up: the call ends on the PC, not left half open. Owner, 2026-10-29.
    RB.CALL_QUIET_S = 0.4
    room.on_call({"type": "call.start", "chat": "c_call", "ref": "r2"})
    await asyncio.sleep(0.3)
    quiet_call = room.call
    room.on_call({"type": "call.alive"})
    await asyncio.sleep(0.25)
    room.on_call({"type": "call.alive"})
    await asyncio.sleep(0.25)
    check("a phone that keeps ticking keeps its call", room.call is quiet_call and not quiet_call.ended)
    await asyncio.sleep(0.6)
    check("one that stops ticking, with its connection still up, has its call ended",
          room.call is None and quiet_call.ended)
    delivered.clear()
    room.on_call({"type": "call.alive"})
    room.on_call({"type": "call.alive"})
    await asyncio.sleep(0.1)
    told = [f for f in delivered if f.get("type") == "call.ended"]
    check(f"and when it's back (muted, so no audio) it's told once ({len(told)})", len(told) == 1 and told[0].get("gone"))

asyncio.run(relay_room())

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all call phone checks passed")
