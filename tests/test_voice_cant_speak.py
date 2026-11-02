"""speak() answers 'error' when the voice is there but can't say anything,
instead of 'ok' with nothing played: a model that fails every sentence is
found by a trial word at load, and a blocking call reports a sentence that
failed on the way. Stand-in engines; nothing reaches the speakers.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_voice_cant_speak.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401

os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
os.environ["HEARTH_NO_AUTODOWNLOAD"] = "1"

import numpy as np  # noqa: E402
from hearth import voice as V  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


os.makedirs(V.VOICES_DIR, exist_ok=True)
for name in ("kokoro-v1.0.fp16.onnx", "voices-v1.0.bin"):
    with open(os.path.join(V.VOICES_DIR, name), "wb") as f:
        f.write(b"0")


class Broken:
    def create(self, text, **kw):
        raise RuntimeError("[ONNXRuntimeError] : 2 : INVALID_ARGUMENT : Unexpected input data type")


class Fragile:
    """Says the trial word, fails on anything longer."""
    def create(self, text, **kw):
        if len(text) > 4:
            raise RuntimeError("synthesis broke mid-reply")
        return np.full(2400, 0.3, dtype=np.float32), 24000


loads = []
engine = [Broken]
V._try_kokoro = lambda allow_download=False: (loads.append(1), engine[0]())[1]
V.reload()

said = []
sink = lambda samples, sr: said.append(len(samples))  # noqa: E731
spk = V.Speaker(sink=sink)
r = spk.speak("Hello there, this is a test.", blocking=True)
check(f"a model that can't speak gives 'error' (got {r!r})", r == "error")
check("status says why", "can't speak" in str(V.status().get("last_load_error")))
check("nothing was sent", not said)
r2 = V.speak("And again.")
check(f"the module speak() says so too (got {r2!r})", r2 == "error")
check("the same broken files aren't loaded again on every call", len(loads) == 1)

V.reload()
engine[0] = Fragile
spk = V.Speaker(sink=sink)
r = spk.speak("This reply is long enough to fail.", blocking=True)
check(f"a sentence that fails in a blocking call gives 'error' (got {r!r})", r == "error")

V.reload()
engine[0] = lambda: type("Fine", (), {"create": lambda self, t, **k: (np.full(2400, 0.3, dtype=np.float32), 24000)})()
spk = V.Speaker(sink=sink)
r = spk.speak("All good here.", blocking=True)
check(f"a working voice still gives 'ok' (got {r!r})", r == "ok")

print("PASS" if not FAILS else f"FAILED: {len(FAILS)}")
sys.exit(1 if FAILS else 0)
