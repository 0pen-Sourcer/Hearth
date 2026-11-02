"""The Kokoro files a new install fetches can be spoken with by the bundled
kokoro_onnx: fetched from the pinned release into an empty home (sha256 checked
by the download), loaded, and a sentence comes out as audible samples.

HEARTH_KOKORO_CACHE=<folder> copies files from there instead of downloading,
but only when they hash to the pinned sha256.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_kokoro_pin.py
"""
from __future__ import annotations

import os
import shutil
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401

os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
os.environ.pop("HEARTH_NO_AUTODOWNLOAD", None)

from hearth import voice as V  # noqa: E402
from hearth import llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


check("voices folder is in the temp home", V.VOICES_DIR.startswith(_home.HOME))
os.makedirs(V.VOICES_DIR, exist_ok=True)

cache = os.environ.get("HEARTH_KOKORO_CACHE", "")
names = {V.KOKORO_MODEL_URL: os.path.basename(V.KOKORO_MODEL_URL),
         V.KOKORO_VOICES_URL: os.path.basename(V.KOKORO_VOICES_URL)}
if cache:
    for url, name in names.items():
        src = os.path.join(cache, name)
        if os.path.isfile(src) and llmserver.file_sha256(src) == V.KOKORO_SHA256[url]:
            shutil.copyfile(src, os.path.join(V.VOICES_DIR, name))

check("the pinned files download and match their sha256", V.download_models())
for url, name in names.items():
    path = os.path.join(V.VOICES_DIR, name)
    check(f"{name} is the pinned file", os.path.isfile(path)
          and llmserver.file_sha256(path) == V.KOKORO_SHA256[url])

engine = V._load_engine(allow_download=False)
check("the engine loads: " + str(V._last_load_error), engine is not None and engine[0] == "kokoro")
if engine:
    import numpy as np  # type: ignore
    try:
        samples, sr = V._kokoro_say(engine[1], "Hello there. This is a short test of the voice.",
                                    "am_michael", 1.0)
        err = ""
    except Exception as e:
        samples, sr, err = np.zeros(0), 24000, f"{type(e).__name__}: {e}"
    a = np.asarray(samples, dtype=np.float32)
    peak = float(np.abs(a).max()) if a.size else 0.0
    rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
    print(f"      {a.size / max(1, sr):.2f} s, peak {peak:.3f}, rms {rms:.4f} {err}")
    check("a sentence synthesizes", not err)
    check("over a second of audio", a.size > sr)
    check("the audio is not silent", peak > 0.1 and rms > 0.01)
    out = os.path.join(_home.HOME, "kokoro_pin.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(V._to_int16(a).tobytes())
    check("the wav is written", os.path.getsize(out) > sr)


class _Sess:
    def __init__(self, names):
        self._n = names

    def get_inputs(self):
        return [type("I", (), {"name": n})() for n in self._n]


check("a model taking input_ids is refused", V._undrivable(type("K", (), {"sess": _Sess(["input_ids", "style", "speed"])})()))
check("a model taking tokens is kept", not V._undrivable(type("K", (), {"sess": _Sess(["tokens", "style", "speed"])})()))

# A model-files-v1.1 model fetched by an earlier version is swapped for the pinned one.
old = os.path.join(cache, "kokoro-v1.0.onnx") if cache else ""
if old and os.path.isfile(old) and not FAILS:
    pinned = os.path.join(_home.HOME, "pinned.onnx")
    shutil.move(os.path.join(V.VOICES_DIR, names[V.KOKORO_MODEL_URL]), pinned)
    shutil.copyfile(old, os.path.join(V.VOICES_DIR, "kokoro-v1.0.onnx"))
    fetched = []
    V._download = lambda url, dest, *a, **k: (fetched.append(url), shutil.copyfile(pinned, dest))
    V._engine = None
    engine = V._load_engine(allow_download=True)
    check("an undrivable model is replaced: " + str(V._last_load_error),
          engine is not None and fetched == [V.KOKORO_MODEL_URL]
          and not os.path.exists(os.path.join(V.VOICES_DIR, "kokoro-v1.0.onnx")))
    if engine:
        s, _sr = V._kokoro_say(engine[1], "Swapped.", "am_michael", 1.0)
        check("and speaks", float(np.abs(np.asarray(s)).max()) > 0.1)

shutil.rmtree(_home.HOME, ignore_errors=True)
print("PASS" if not FAILS else f"FAILED: {len(FAILS)}")
sys.exit(1 if FAILS else 0)
