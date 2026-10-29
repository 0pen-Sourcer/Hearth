"""The wake word is light while it waits, and hears over a fan.

It loaded dictation's model at start and held it for as long as Inglen ran
(base.en ~300 MB, large GBs); a steady fan or hum kept one "utterance" open
forever, so it never checked anything and went deaf; and each check
transcribed the whole utterance. Now nothing is loaded until there is
something to check, the model is let go after a quiet while, a fan counts as
the room's level, a long run of sound (a TV) is checked once, and a check reads
the first seconds and words only. Stand-in model and mic; no audio hardware.

    python tests/test_wake_light.py
"""
from __future__ import annotations

import importlib.machinery
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-wake-home-")
WS = os.path.join(HOME, "Inglen")
os.makedirs(WS)
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1", HEARTH_WAKE_DEBUG="0",
                  HEARTH_WAKE_IDLE_UNLOAD_S="60")
with open(os.path.join(WS, "settings.json"), "w") as f:
    json.dump({"onboarded": True}, f)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

loads, checks = [], []


class StandInModel:
    def __init__(self, size, **kw):
        loads.append(size)

    def transcribe(self, audio, **kw):
        checks.append({"seconds": len(audio) / 16000, **{k: kw.get(k) for k in ("max_new_tokens", "language")}})
        loud = float(np.max(np.abs(audio))) > 0.3
        return iter([types.SimpleNamespace(text="Hey Jarvis" if loud else "the weather today")]), None


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__spec__ = importlib.machinery.ModuleSpec(name, None)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


fw = stub("faster_whisper", WhisperModel=StandInModel)
stub("faster_whisper.utils", download_model=lambda *a, **k: None)
fw.utils = sys.modules["faster_whisper.utils"]
stub("sounddevice")

from hearth import listen, wake  # noqa: E402

listen.mic_claimed = lambda: False
listen.is_available = lambda: True

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


SR = 16000
rng = np.random.default_rng(3)


def noise(sec, rms):
    return (rng.standard_normal(int(SR * sec)) * rms).astype(np.float32)


def speech(sec, amp=0.5):
    t = np.arange(int(SR * sec)) / SR
    # syllable-like bursts: a tone switched on and off 4 times a second
    return (amp * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 4 * t) > -0.3)).astype(np.float32)


def run(audio):
    """Run a listener over the audio as its mic; (wakes, listener)."""
    wakes = []
    L = wake.WakeListener(on_wake=lambda p: wakes.append(p), phrases=["jarvis", "hey jarvis"])

    class Stream:
        i = 0

        def read(self, n):
            if self.i >= len(audio):
                L._stop.set()
                return np.zeros((n, 1), np.float32), False
            b = audio[self.i:self.i + n]
            self.i += n
            return np.pad(b, (0, n - len(b))).reshape(-1, 1), False

        def stop(self):
            pass

        def close(self):
            pass
    L._open_mic = lambda: (Stream(), "")
    wake.COOLDOWN_S = 0
    assert L.start()
    L._thread.join(60)
    return wakes, L


wake._OWN = None
listen._model = None
wakes, _ = run(noise(30, 0.003))
check(f"starting and a quiet room load no model ({loads})", loads == [])

checks.clear()
wakes, _ = run(np.concatenate([noise(20, 0.02), noise(1.2, 0.02) + speech(1.2), noise(3, 0.02)]))
check(f"a steady fan doesn't make it deaf: the name said over it is heard ({wakes})", len(wakes) == 1)
check(f"and the fan alone is checked once at most, when it starts ({len(checks)} checks)", len(checks) <= 2)
check(f"a check reads the first seconds and words only {checks[-1:]}",
      checks and checks[-1]["seconds"] <= 3.0 and checks[-1]["max_new_tokens"] and checks[-1]["language"] == "en")

checks.clear()
tv = np.concatenate([np.concatenate([speech(3.5, 0.2), noise(0.4, 0.003)]) for _ in range(30)])
wakes, _ = run(tv)
check(f"two minutes of a TV talking is checked once or twice, not every sentence ({len(checks)})", 1 <= len(checks) <= 2)

loads.clear()
wake._OWN = None
wakes, L = run(np.concatenate([noise(2, 0.003), speech(1.2), noise(70, 0.003)]))
check(f"the model is loaded when there is something to check ({loads})", loads == [wake.WAKE_MODEL])
check("and let go after a quiet minute", wake._OWN is None)

loads.clear()
listen._model = StandInModel("dictation")
loads.clear()
wakes, _ = run(np.concatenate([noise(2, 0.003), speech(1.2), noise(2, 0.003)]))
check("with voice mode's model loaded, the wake check uses it and loads nothing", loads == [] and len(wakes) == 1)
listen._model = None

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all light wake checks passed")
