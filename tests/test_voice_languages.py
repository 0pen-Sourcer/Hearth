"""A reply that changes language is read in each language's own voice.

The voice was picked once per chunk of about 60 characters, so an English reply
with a Hindi line in it went to a Hindi voice whole, English included, or to
the English voice whole. Now each sentence gets its voice and it switches back.
Stand-in Kokoro engine; no model, no speakers.

    python tests/test_voice_languages.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-voicelang-home-")
WS = tempfile.mkdtemp(prefix="inglen-voicelang-ws-")
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
for k in ("HEARTH_VOICE", "JARVIS_VOICE", "HEARTH_STT_LANGUAGE", "JARVIS_STT_LANGUAGE"):
    os.environ.pop(k, None)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from hearth import voice  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class StandInKokoro:
    """Records what it was asked to say, in which voice and language."""

    def __init__(self):
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, lang))
        return np.full(240 * max(1, len(text)), 0.1, dtype=np.float32), 24000


engine = StandInKokoro()
voice._load_engine = lambda allow_download=False: ("kokoro", engine)


def said(text, chosen, first_chunk=60):
    engine.calls.clear()
    got = []
    sp = voice.Speaker(sink=lambda frame, sr: got.append(len(frame)), lead=10.0, first_chunk=first_chunk)
    sp.speak(text, blocking=True, voice=chosen)
    return list(engine.calls), sum(got)


calls, frames = said("Sure, here it is. नमस्ते, आप कैसे हैं? That's all for now.", "am_michael")
voices = [v for _, v, _ in calls]
check(f"English, then Hindi, then English again ({voices})",
      len(voices) == 3 and voices[0] == "am_michael" and voices[1][:1] == "h" and voices[2] == "am_michael")
check("each part goes with its own language", [lang for _, _, lang in calls] == ["en-us", "hi", "en-us"])
check("the Hindi part is the Hindi sentence alone", len(calls) == 3 and calls[1][0] == "नमस्ते, आप कैसे हैं?")
check("a woman's voice switches to a Hindi woman's voice",
      [v[:2] for _, v, _ in said("Okay. ठीक है। Done.", "af_heart")[0]] == ["af", "hf", "af"])
check("all of it reaches the far end", frames == 240 * sum(len(t) for t, _, _ in calls))

calls, _ = said("The total is 3.5 kg, e.g. two bags. Nothing else.", "bf_emma")
check("an English reply stays in one voice, numbers whole", [v for _, v, _ in calls] == ["bf_emma"])

calls, _ = said("Here you go. 千里之行，始于足下。 That's the proverb.", "am_adam")
check("a Mandarin line gets a Mandarin voice and English comes back",
      [v[:1] for _, v, _ in calls] == ["a", "z", "a"] and calls[1][2] == "cmn")
calls, _ = said("In Japanese it goes like this. 千里の道も一歩から。 Nice, right?", "am_adam")
check("a Japanese line gets a Japanese voice", [v[:1] for _, v, _ in calls] == ["a", "j", "a"])

calls, _ = said("今日は晴れです。東京大学。", "jf_alpha")
check("kanji alone in a Japanese reply stays Japanese", [v for _, v, _ in calls] == ["jf_alpha"])

calls, _ = said("मैं ठीक हूँ। Thank you.", "hm_omega")
check("with a Hindi voice chosen, one voice reads it all",
      [v for _, v, _ in calls] == ["hm_omega"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice language checks passed")
