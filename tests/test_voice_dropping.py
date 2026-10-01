"""Which heard words reach the chat right after Hearth has spoken.

On headphones the mic cannot hear the earpieces, so everything said while or
just after Hearth talks is the person's and has to be sent; only a repeat of
Hearth's own words counts as echo. The old rule dropped anything heard within
a couple of seconds of Hearth speaking, headphones or not, so a quick reply
vanished without a trace. In speakers mode the mic is deaf while Hearth talks
and for a moment after, on purpose.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_dropping.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import realtime_voice as rv                              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Recorder:
    """Hands out queued phrases, then blocks until shut down."""
    def __init__(self, phrases):
        self.q = list(phrases)
        self.down = threading.Event()

    def text(self):
        if self.q:
            time.sleep(0.05)
            return self.q.pop(0)
        self.down.wait()
        raise RuntimeError("shut down")

    def clear_audio_queue(self):
        pass

    def stop(self):
        pass

    def shutdown(self):
        self.down.set()


class FakeTTS:
    """Hearth finished saying `last` a moment ago."""
    last = "the weather in Delhi is sunny today"
    def is_speaking(self): return False
    def seconds_since_spoke(self): return 0.4
    def set_voice_state(self, s): pass
    def last_spoken_text(self): return self.last
    def recently_spoke_text(self): return self.last.lower()
    def stop(self): self.stopped = True


rv._REALTIME_AVAILABLE = True
rv._tts = FakeTTS()
REAL_HALF_DUPLEX = rv._half_duplex


def heard_with(phrases, speakers_mode):
    rv._half_duplex = lambda: speakers_mode
    got = []
    rec = Recorder(phrases)
    rv._build_recorder = lambda sess=None: rec
    lease = rv.start_continuous(got.append)
    end = time.time() + 3
    while time.time() < end and rec.q:
        time.sleep(0.02)
    time.sleep(0.3)
    rv.stop_continuous(lease)
    time.sleep(0.2)
    return got


got = heard_with(["wait, what about tomorrow?"], speakers_mode=False)
check(f"on headphones a quick reply right after Hearth speaks is sent {got}", got == ["wait, what about tomorrow?"])
got = heard_with(["wait, what about tomorrow?"], speakers_mode=True)
check(f"in speakers mode the same words are held back as possible echo {got}", got == [])

from hearth.persona import NAME                                      # noqa: E402
rv._tts.stopped = False
got = heard_with([f"{NAME}, stop", "the weather in Delhi is sunny today"], speakers_mode=True)
check(f"in speakers mode calling {NAME} by name cuts in and is sent {got}", got == [f"{NAME}, stop"])
check("and it stops Hearth talking", rv._tts.stopped)

# Mute: nothing said while it is on is sent, including a sentence that was
# still being recorded when it went off.
def muted_run(phrases, before_each):
    rv._half_duplex = lambda: False
    got = []
    rec = Recorder(phrases)
    real_text = rec.text
    step = iter(before_each)

    def text():
        next(step)()
        return real_text()
    rec.text = text
    rv._build_recorder = lambda sess=None: rec
    lease = rv.start_continuous(got.append)
    end = time.time() + 3
    while time.time() < end and rec.q:
        time.sleep(0.02)
    time.sleep(0.3)
    rv.stop_continuous(lease)
    time.sleep(0.2)
    return got


def began_now():
    rv._rec_started_at = time.time()


def mute():
    began_now()
    rv.set_muted(True)


def unmute_mid_sentence():
    began_now()                 # the sentence began while still muted...
    time.sleep(0.02)
    rv.set_muted(False)         # ...and the button was pressed before it ended


def fresh():
    time.sleep(0.02)
    began_now()


got = muted_run(["my card number is one two three", "and the code is nine", "okay, what's the weather?"],
                [mute, unmute_mid_sentence, fresh, lambda: None])
check(f"what was said while muted is never sent, even after unmuting {got}", got == ["okay, what's the weather?"])

# Headphones setting, but the mic hears the speakers: Hearth's own words come
# back. From then on the session behaves as speakers, without being told to.
os.environ.pop("HEARTH_VOICE_HALF_DUPLEX", None)
from hearth import voice as _voice                                   # noqa: E402
_voice.half_duplex_enabled = lambda: False
rv._half_duplex = REAL_HALF_DUPLEX
notices = []
got = []
rec = Recorder([FakeTTS.last, "wait, what about tomorrow?"])
rv._build_recorder = lambda sess=None: rec
lease = rv.start_continuous(got.append, on_notice=notices.append)
end = time.time() + 3
while time.time() < end and rec.q:
    time.sleep(0.02)
time.sleep(0.3)
check(f"hearing its own words back turns speakers mode on for the session {got}",
      got == [] and rv._half_duplex() is True)
check("and it says so, once", len(notices) == 1 and "name" in notices[0])
rv.stop_continuous(lease)
time.sleep(0.2)
rec2 = Recorder([])
rv._build_recorder = lambda sess=None: rec2
lease = rv.start_continuous(got.append)
check("a new session starts without that assumption", rv._half_duplex() is False)
rv.stop_continuous(lease)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice dropping checks passed")
