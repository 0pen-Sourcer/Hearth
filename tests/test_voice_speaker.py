"""A speaker of its own for a phone call: what it says goes to its sink in
small frames paced to real time, never to this PC's speakers; its speaking
state is its own (the PC's stays idle, so the PC's echo guards don't fire for
a call), and stopping one leaves the other alone.

Uses the real Kokoro voice when its files are on this PC.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_speaker.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")

from hearth import voice as V  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


if V._load_engine() is None:
    print("skipped: no voice model on this PC")
    sys.exit(0)

# This PC's speakers must not be touched by a call.
import sounddevice as sd  # noqa: E402


class Refuse:
    def __init__(self, *a, **k):
        raise AssertionError("a call's speaker opened this PC's speakers")


sd.OutputStream = Refuse

frames, flushed = [], []


class Sink:
    def __call__(self, samples, sr):
        frames.append((time.time(), len(samples), sr))

    def flush(self):
        flushed.append(time.time())


call = V.Speaker(sink=Sink(), first_chunk=20)
t0 = time.time()
call.speak("Sure, opening Chrome now. It should be up in a second.", voice="am_michael")
while not frames and time.time() - t0 < 30:
    time.sleep(0.02)
check(f"a call's speaker sends audio to its sink ({len(frames)} frames so far)", bool(frames))
check("while it speaks, its own state says so and the PC's doesn't", call.is_speaking() and not V.is_speaking())
first = frames[0][0] - t0 if frames else 99
check(f"the first clause goes out quickly ({first:.2f} s to the first frame)", first < 8)
while call.is_speaking() and time.time() - t0 < 60:
    time.sleep(0.05)
audio_s = sum(n / sr for _, n, sr in frames)
wall = frames[-1][0] - frames[0][0] if len(frames) > 1 else 0
check(f"frames are small ({max(n for _, n, _ in frames)} samples at most)", max(n for _, n, _ in frames) <= 0.061 * frames[0][2])
check(f"and paced to real time, not dumped at once ({audio_s:.1f} s of audio over {wall:.1f} s)",
      wall >= audio_s - call.lead - 0.4)
check("its spoken words are its own, for its echo guard", "opening chrome" in call.recently_spoke_text()
      and "opening chrome" not in V.recently_spoke_text())

# Cutting a call's speech flushes the far end and doesn't touch the PC's speaker.
frames.clear()
call.reset_abort()
call.speak("This is a long answer that will be cut off part of the way through, so nothing after the cut is sent at all.",
           voice="am_michael")
while not frames and time.time() - t0 < 90:
    time.sleep(0.02)
time.sleep(0.3)
before_pc_abort = V.DEFAULT.abort.is_set()
call.stop()
n_at_stop = len(frames)
time.sleep(1.0)
check(f"a stop flushes the far end and sends nothing more ({n_at_stop} -> {len(frames)} frames)",
      bool(flushed) and len(frames) <= n_at_stop + 1 and not call.is_speaking())
check("and leaves the PC's own speaker alone", V.DEFAULT.abort.is_set() == before_pc_abort)
call.reset_abort()
V.stop()
check("a stop on the PC doesn't cut a call", V.DEFAULT.abort.is_set() and not call.abort.is_set())
V.reset_abort()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice speaker checks passed")
