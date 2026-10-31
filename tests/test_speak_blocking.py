"""A blocking speak() into a sink (the phone's Read aloud) returns only once its audio
has gone out: the phone ends the reading when the PC says it's done."""
import os
import sys
import tempfile
import time

HOME = tempfile.mkdtemp(prefix="hearth-speak-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from hearth import voice  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok    " if ok else "FAIL  ") + name)
    fails += 0 if ok else 1


def slow_synth(kind, obj, chunk, v, speed):
    time.sleep(0.6)                      # a first chunk takes a while on a cold engine
    return np.zeros(2400, dtype=np.int16), 24000


voice._load_engine = lambda allow_download=False: ("kokoro", object())
voice._synth_chunk = slow_synth

got = []


class Sink:
    def __call__(self, samples, sr):
        got.append((time.time(), len(samples)))

    def flush(self):
        pass


sp = voice.Speaker(sink=Sink(), first_chunk=40)
t0 = time.time()
said = sp.speak("Fact number two. Octopuses have three hearts.", blocking=True)
done = time.time()
check(f"it says ok ({said})", said == "ok")
check(f"it waits for the audio to be made and sent ({len(got)} frames, {done - t0:.2f}s)",
      bool(got) and got[-1][0] <= done and done - t0 >= 0.6)
check("and isn't busy after", not sp.busy())

sp2 = voice.Speaker(sink=Sink(), first_chunk=40)
sp2.speak("One more thing to say here.", blocking=False)
sp2.stop()
time.sleep(0.8)
check("a stop leaves nothing counted as waiting", sp2._pending == 0)

print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
