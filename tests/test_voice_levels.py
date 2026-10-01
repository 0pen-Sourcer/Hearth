"""The voice visuals move when you talk, not only when Hearth does.

Both the window's dot grid and the floating overlay were driven by Hearth's
own speech alone; while listening, neither had any sign of the microphone,
so talking to it looked like talking to nothing. The recorder now reports the
loudness of every chunk it hears.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_levels.py
"""
from __future__ import annotations

import inspect
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


import numpy as np                                                  # noqa: E402
from hearth import voice, realtime_voice as rv                      # noqa: E402

try:
    from RealtimeSTT import AudioToTextRecorder
    check("the recorder takes a per-chunk callback",
          "on_recorded_chunk" in inspect.signature(AudioToTextRecorder.__init__).parameters)
except ImportError:
    print("skip  RealtimeSTT is not installed")

# The recorder Hearth builds is handed the callback.
seen = {}


class Capture:
    def __init__(self, **kw):
        seen.update(kw)


import types                                                        # noqa: E402
real_mod = sys.modules.get("RealtimeSTT")
sys.modules["RealtimeSTT"] = types.SimpleNamespace(AudioToTextRecorder=Capture)
real_dev, real_mic = rv._stt_device, rv._mic_index
rv._stt_device = lambda: ("cpu", "int8")
rv._mic_index = lambda: None          # this machine's own microphone choice is not under test
try:
    rv._build_recorder()
except Exception as e:
    print(f"      (build raised {type(e).__name__}: {e})")
finally:
    rv._stt_device, rv._mic_index = real_dev, real_mic
    if real_mod is not None:
        sys.modules["RealtimeSTT"] = real_mod
check("Hearth's recorder reports what it hears", seen.get("on_recorded_chunk") is rv._on_mic_chunk)

got = []
voice.set_mic_sink(got.append)
t = np.arange(512) / 16000.0
loud = (np.sin(2 * np.pi * 220 * t) * 9000).astype(np.int16).tobytes()
quiet = np.zeros(512, dtype=np.int16).tobytes()
for _ in range(6):
    rv._on_mic_chunk(loud)
    time.sleep(0.07)
check(f"talking raises the level ({voice.current_mic_level():.2f})", voice.current_mic_level() > 0.3)
check("the window is sent the level", got and max(got) > 0.3)
for _ in range(12):
    rv._on_mic_chunk(quiet)
check(f"silence brings it back down ({voice.current_mic_level():.3f})", voice.current_mic_level() < 0.05)
rv._on_mic_chunk(loud)
time.sleep(0.4)
check("a mic that stops reporting reads as silent", voice.current_mic_level() == 0.0)
voice.set_mic_sink(None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice level checks passed")
