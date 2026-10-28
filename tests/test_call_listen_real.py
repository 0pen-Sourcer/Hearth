"""The real speech recognizer, fed audio the way a phone call feeds it (16 kHz
16-bit frames, no microphone), hears a spoken sentence. The sentence is spoken
by Hearth's own voice. Skipped when the voice or speech models aren't on this PC.

    .\\.venv\\Scripts\\python.exe tests\\test_call_listen_real.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")

from hearth import realtime_voice as rv, voice as V  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)



def main():
    eng = V._load_engine()
    if eng is None or not rv.is_available() or not rv.model_downloaded():
        print("skipped: voice or speech models are not on this PC")
        sys.exit(0)

    import numpy as np  # noqa: E402
    from scipy.signal import resample_poly  # noqa: E402

    kind, obj = eng
    samples, sr = obj.create("Open the downloads folder for me please.", voice="am_michael", speed=1.0, lang="en-us")
    pcm = resample_poly(np.asarray(samples, dtype=np.float32), 16000, sr)
    pcm16 = (np.clip(pcm, -1, 1) * 32767).astype("<i2")
    silence = np.zeros(16000, dtype="<i2")

    heard, ready = [], threading.Event()
    lease = rv.start_continuous(heard.append, on_ready=ready.set, fed=True, speaker=V.Speaker(sink=lambda s, r: None))
    check("a fed session starts without this PC's mic", lease is not None and ready.wait(120))
    sess = rv.session_for(lease)
    t0 = time.time()
    for chunk in (silence, pcm16, silence, silence):
        for off in range(0, len(chunk), 960):                       # 60 ms frames, at the pace a phone sends them
            sess.feed(chunk[off:off + 960].tobytes(), 16000)
            time.sleep(0.06)
    while not heard and time.time() - t0 < 30:
        time.sleep(0.1)
    rv.stop_continuous(lease)
    words = " ".join(heard).lower()
    check(f"it hears the sentence ({heard})", "downloads" in words and "folder" in words)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all real call listening checks passed")


if __name__ == "__main__":
    main()
