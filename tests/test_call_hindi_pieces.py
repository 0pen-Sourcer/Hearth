"""A Hindi reply in a call is spoken sentence by sentence as it is written.

Hindi ends a sentence with a danda (।), which the call didn't count as an end:
after the opening piece, nothing more was spoken until the whole reply had been
written, and then all of it went to the voice at once.

    .\\.venv\\Scripts\\python.exe tests\\test_call_hindi_pieces.py
"""
from __future__ import annotations

import os
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="hearth-call-hi-")
os.environ.update(HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hearth import call as C  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def pieces(stream):
    call = C.Call(send=lambda f: None, say=lambda t: None)
    said = []
    call._speak = said.append
    text = ""
    for part in stream:
        text += part
        call.live(text)
    before_done = list(said)
    call.done(text)
    return before_done, said


S1 = "ठीक है, मैं अभी आपके लिए यह काम कर देता हूँ।"
S2 = " कल सुबह सात बजे मैं आपको याद दिला दूँगा।"
S3 = " और कुछ चाहिए तो बताइए।"
before, after = pieces([S1, S2, S3])
check(f"the second sentence is spoken while the third is still to come ({before})",
      any(S2.strip() in p for p in before))
check(f"every word is spoken once ({after})", "".join(after).replace(" ", "") == (S1 + S2 + S3).replace(" ", ""))

before, _ = pieces(["Sure, I can do that for you right now.", " I'll remind you at seven tomorrow.", " Anything else?"])
check(f"English still goes sentence by sentence ({before})", any("remind you at seven" in p for p in before))

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall call hindi piece checks passed")
