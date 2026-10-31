"""The wake word: what audio reaches the recognizer, and who holds the mic.

The listener kept only the blocks over the energy threshold, so consonants and
the gaps between words were cut out; it started at the first loud block, so a
soft first syllable was lost; and it kept the last 2.5 s of speech, so in
"Jarvis, open Spotify and play..." the wake word itself was dropped. On the
same Whisper and synthesized speech at mic level, the old cut woke on 9 of 15
phrases with exact matching and the new one on 15 of 15.

It also held the mic through voice mode, transcribing every word a second
time, on a headset that opens for one capture at a time; and when the picked
mic would not open it took the first device that did, a virtual mic in a real
log. Stand-in audio devices; no mic needed.

    .\\.venv\\Scripts\\python.exe tests\\test_wake.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import os
import sys
import tempfile
import threading
import time
import types

import numpy as np

os.environ["JARVIS_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-wake-")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


SR, N = 16000, 1600


def block(level):
    """100 ms of tone whose RMS is `level`."""
    t = np.arange(N) / SR
    return (np.sin(2 * np.pi * 220 * t) * level * np.sqrt(2)).astype(np.float32)


from hearth import listen as L, wake as W                            # noqa: E402

# ---------------------------------------------------------------- the audio
seg = W._Segmenter()
pre = [block(0.004) for _ in range(5)]                  # a soft onset, under threshold
talk = [block(0.05), block(0.003), block(0.05), block(0.004), block(0.05)]  # words, gaps
out = None
for b in pre + talk + [block(0.001)] * 10:
    out = seg.feed(b) if out is None else out
check("an utterance comes out after the pause", out is not None)
secs = len(out) / SR if out is not None else 0
check(f"with the quiet gaps between words kept, and the lead-in before the "
      f"onset ({secs:.1f}s, of which 0.5s is speech)",
      out is not None and secs >= 0.5 + W.PREROLL_S)
check("the soft first sound is in it",
      out is not None and np.allclose(out[:N], pre[-3]))

seg = W._Segmenter()
first = block(0.07)
long_talk = [first] + [block(0.05)] * 80                   # 8 s of speech
out = None
for b in long_talk + [block(0.001)] * 10:
    out = seg.feed(b) if out is None else out
check("from a long stretch of speech the beginning is kept, where a wake word is",
      out is not None and any(np.allclose(out[i:i + N], first) for i in range(0, 4 * N, N)))
check(f"capped near {W.MAX_UTTERANCE_S:.0f}s", out is not None
      and len(out) / SR <= W.MAX_UTTERANCE_S + W.PREROLL_S + 0.2)

# --------------------------------------------------------------- the mic
DEVICES = [
    {"name": "Stereo Mix (Realtek(R) Audio)", "max_input_channels": 2, "hostapi": 0},
    {"name": "Microphone (Steam Streaming Microphone)", "max_input_channels": 1, "hostapi": 0},
    {"name": "Headset (boAt Rockerz)", "max_input_channels": 1, "hostapi": 0},
]
STATE = {"default": 0, "open": 0, "opened": []}


class _Stream:
    def __init__(self, device):
        self.device = device

    def start(self):
        STATE["open"] += 1
        STATE["opened"].append(self.device)

    def read(self, n):
        time.sleep(0.02)
        return np.zeros((n, 1), np.float32), False

    def stop(self):
        STATE["open"] -= 1

    def close(self):
        pass


def _query(i=None, kind=None):
    if kind == "input":
        return DEVICES[STATE["default"]]
    return DEVICES if i is None else DEVICES[i]


sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=lambda device=None, **k: _Stream(STATE["default"] if device is None else device),
    query_devices=_query, query_hostapis=lambda: [{"name": "Windows WASAPI"}])


class _PaStream:
    """PyAudio's stream, which is what wake opens: a fresh device table each time."""
    def __init__(self, device):
        self.device = device
        STATE["open"] += 1
        STATE["opened"].append(device)

    def read(self, n, exception_on_overflow=True):
        time.sleep(0.02)
        return bytes(2 * n)

    def stop_stream(self):
        pass

    def close(self):
        STATE["open"] -= 1


class _PyAudio:
    def get_device_count(self):
        return len(DEVICES)

    def get_device_info_by_index(self, i):
        d = DEVICES[i]
        return {"name": d["name"], "maxInputChannels": d["max_input_channels"], "hostApi": 0}

    def get_host_api_info_by_index(self, i):
        return {"name": "Windows WASAPI"}

    def get_default_input_device_info(self):
        return {"index": STATE["default"]}

    def open(self, input_device_index=None, **k):
        return _PaStream(input_device_index)

    def terminate(self):
        pass


sys.modules["pyaudio"] = types.SimpleNamespace(PyAudio=_PyAudio, paInt16=8)
import hearth.web as _web                                          # noqa: E402

SETTINGS = {}
_web._load_settings = lambda: dict(SETTINGS)
wl = W.WakeListener(on_wake=lambda p: None)

s, why = wl._open_mic()
check(f"with Stereo Mix as default and no pick, wake does not listen or guess: {why!r}",
      s is None and "not a microphone" in why)
SETTINGS["voice_input_name"] = "Headset (boAt Rockerz)"
s, why = wl._open_mic()
check("a mic picked by name is the one opened", s is not None and s._st.device == 2)
if s is not None:
    s.stop(); s.close()
SETTINGS["voice_input_name"] = "Headset (Not Here)"
s, why = wl._open_mic()
check(f"a picked mic that is gone pauses wake rather than opening another: {why!r}",
      s is None and "not connected" in why)

DEVICES.append({"name": "Headset (Plugged In Later)", "max_input_channels": 1, "hostapi": 0})
SETTINGS["voice_input_name"] = "Headset (Plugged In Later)"
s, why = wl._open_mic()
check("a mic plugged in after launch is found without a restart", s is not None and s._st.device == 3)
if s is not None:
    s.stop(); s.close()
DEVICES.pop()

# ------------------------------------------------ handing the mic to voice mode
SETTINGS["voice_input_name"] = "Headset (boAt Rockerz)"
STATE["opened"].clear()
t = threading.Thread(target=wl._loop, daemon=True)
t.start()
deadline = time.time() + 3
while time.time() < deadline and STATE["open"] == 0:
    time.sleep(0.02)
check("wake opens the mic", STATE["open"] == 1)
t0 = time.time()
L.claim_mic(wait_s=2.0)
check(f"when voice mode claims the mic, wake lets go first ({time.time() - t0:.2f}s)",
      STATE["open"] == 0 and L.wake_released.is_set())
time.sleep(0.5)
check("and stays off it while voice mode runs", STATE["open"] == 0)
L.release_mic()
deadline = time.time() + 3
while time.time() < deadline and STATE["open"] == 0:
    time.sleep(0.02)
check("and takes it back when voice mode ends", STATE["open"] == 1)
wl._stop.set()
t.join(3)
check("stopping wake closes the mic", STATE["open"] == 0)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all wake checks passed")
