"""Listening on the graphics card is really on the graphics card. RealtimeSTT
asks torch whether CUDA is there, and the torch bundled with Inglen is the
processor-only build, so both speech models silently ran on the processor and
a sentence took about 4 s to finish (more on a busy PC). The recognizer is now
told about the card when the speech engine itself (ctranslate2) sees one.

With a card here, also: a real recorder built for "Listening runs on: graphics
card" puts its models on it, and a spoken sentence (made by the voice) is
written out within 1.5 s of its end, in English and in Hindi.

    .\\.venv\\Scripts\\python.exe tests\\test_stt_speed.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-stt-speed-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WORK
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)



def main() -> None:
    # The recognizer starts processes of its own, which import this file again.
    from hearth import realtime_voice as rv  # noqa: E402

    # ---------------------------------------------------------------- the decision, with stand-ins
    import RealtimeSTT.audio_recorder as ar  # noqa: E402

    real_torch = ar.torch
    fake_cuda = types.SimpleNamespace(is_available=lambda: False, device_count=lambda: 0)
    ar.torch = types.SimpleNamespace(cuda=fake_cuda, float32="f32")
    saved_ct2 = sys.modules.get("ctranslate2")
    sys.modules["ctranslate2"] = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    try:
        fix = getattr(rv, "_let_recorder_use_gpu", None)
        if fix:
            fix()
        check("a card the speech engine sees counts, even with a processor-only torch",
              ar.torch.cuda.is_available() is True)
        check("and everything else still comes from torch", getattr(ar.torch, "float32", None) == "f32"
              and ar.torch.cuda.device_count() == 0)
        ar.torch = types.SimpleNamespace(cuda=fake_cuda)
        sys.modules["ctranslate2"] = types.SimpleNamespace(get_cuda_device_count=lambda: 0)
        if fix:
            fix()
        check("no card: the processor, as before", ar.torch.cuda.is_available() is False)
    finally:
        ar.torch = real_torch
        if saved_ct2 is not None:
            sys.modules["ctranslate2"] = saved_ct2
        else:
            sys.modules.pop("ctranslate2", None)

    # ---------------------------------------------------------------- the real thing, where there is a card
    try:
        import ctranslate2  # noqa: E402
        card = ctranslate2.get_cuda_device_count() > 0 and rv._cudnn_present()
    except Exception:
        card = False
    if not card:
        print("skipped the real recorder: no graphics card for the speech engine here")
    else:
        import numpy as np  # noqa: E402
        from hearth import env as E, voice as V  # noqa: E402
        voices = os.path.expanduser("~/Jarvis/voices")
        for f in os.listdir(voices) if os.path.isdir(voices) else []:
            if os.path.isfile(os.path.join(voices, f)) and not f.endswith(".part"):
                try:
                    os.link(os.path.join(voices, f), os.path.join(V.VOICES_DIR, f))
                except OSError:
                    pass
        eng = V._load_engine(allow_download=False)
        if eng is None:
            print("skipped the real recorder: no voice here to speak the test sentences")
        else:
            E.set("STT_DEVICE", "cuda")
            E.set("STT_MODEL", "small")
            heard: list = []
            sess = rv._Session(1, lambda t: heard.append((time.time(), t)), None, None, None, None, fed=True,
                               speaker=V.Speaker(sink=lambda *a: None))
            rec = rv._build_recorder(sess)
            check(f"the recorder's models are on the graphics card ({rec.device})", rec.device == "cuda")
            sess.recorder = rec

            def loop():
                while True:
                    t = rec.text()
                    if t:
                        sess.on_utterance(t)
            threading.Thread(target=loop, daemon=True).start()
            _, obj = eng
            for lang, voice, text, want in (("en", "af_heart", "What's on my calendar this afternoon?", "calendar"),
                                            ("hi", "hf_alpha", "आज शाम मेरे कैलेंडर में क्या है?", "शाम")):
                a, sr = obj.create(text, voice=voice, speed=1.0, lang="en-us" if lang == "en" else "hi")
                pcm = (np.interp(np.arange(0, len(a), sr / 16000.0), np.arange(len(a)), a) * 32767).astype("<i2").tobytes()
                heard.clear()
                frame = 1920
                for i in range(0, 16000, frame):                 # a moment of quiet first
                    sess.feed(b"\0" * frame, 16000); time.sleep(0.06)
                for i in range(0, len(pcm), frame):
                    sess.feed(pcm[i:i + frame], 16000); time.sleep(0.06)
                end = time.time()
                while time.time() - end < 8 and not heard:
                    sess.feed(b"\0" * frame, 16000); time.sleep(0.06)
                took = heard[0][0] - end if heard else 99
                said = heard[0][1] if heard else ""
                check(f"{lang}: written out {took:.2f} s after the end of the sentence ({said!r})", took < 1.5 and want in said)
            sess.stop.set()
            rv._shutdown_recorder(rec)

    print("\n" + ("FAILED: " + "; ".join(FAILS) if FAILS else "all listening speed checks passed"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
