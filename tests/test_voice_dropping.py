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
    def stop(self): self.stopped = True


rv._REALTIME_AVAILABLE = True
rv._tts = FakeTTS()


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

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice dropping checks passed")
