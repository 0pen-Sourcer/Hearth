"""Which microphone voice opens.

The pick was saved as a device number. Numbers change whenever hardware comes
or goes, and the table the picker read was not the one the recorder opened
from, so a saved number could name another device or none ("mic 14 failed to
open", hundreds of times in one log). When that happened, or when the Windows
default input was Stereo Mix, the fallback took the first device that opened,
which on a real machine was Steam's virtual microphone: the session reported
ready and heard nothing. Hearth's own open check was also easier than the
recorder's, so a device could pass one and fail the other.

The pick is now kept by name and looked up in the opening library's table, the
check matches the recorder's, and with no usable pick voice says what to do
instead of guessing. A stand-in PyAudio holds the devices.

    .\\.venv\\Scripts\\python.exe tests\\test_mic_choice.py
"""
from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


APIS = ["MME", "Windows WASAPI", "Windows WDM-KS"]
# (name, input channels, host api, opens)
TABLE = [
    ("Stereo Mix (Realtek(R) Audio)", 2, 1, True),
    ("Microphone (Steam Streaming Mic", 1, 0, True),            # MME cuts at 31
    ("Microphone (Steam Streaming Microphone)", 1, 1, True),
    ("Headset (boAt Rockerz Hands-Free)", 1, 0, False),         # MME entry fails
    ("Headset (boAt Rockerz Hands-Free)", 1, 1, True),
]
STATE = {"default": 0, "opened": []}


class _Stream:
    def start_stream(self): pass
    def read(self, n, exception_on_overflow=False): return b"\0\0" * n
    def stop_stream(self): pass
    def close(self): pass


class _PyAudio:
    def get_device_count(self): return len(TABLE)

    def get_device_info_by_index(self, i):
        n, ch, api, _ = TABLE[i]
        return {"index": i, "name": n, "maxInputChannels": ch, "hostApi": api}

    def get_host_api_info_by_index(self, i): return {"name": APIS[i]}

    def get_default_input_device_info(self):
        return self.get_device_info_by_index(STATE["default"])

    def open(self, rate=None, input_device_index=None, **kw):
        STATE["opened"].append((input_device_index, rate))
        if not TABLE[input_device_index][3]:
            raise OSError("Invalid device")
        return _Stream()

    def terminate(self): pass


sys.modules["pyaudio"] = types.SimpleNamespace(PyAudio=_PyAudio, paInt16=8)
from hearth import listen as L, realtime_voice as rv                   # noqa: E402


def pick(name=""):
    os.environ["JARVIS_VOICE_INPUT_NAME"] = name
    os.environ["JARVIS_VOICE_INPUT_DEVICE"] = "-1"
    try:
        return rv._mic_index(), rv.mic_warning()
    except rv.NoUsableMic as e:
        return None, str(e)


def rows():
    return [(i, n, ch, APIS[a]) for i, (n, ch, a, _) in enumerate(TABLE)]


# ------------------------------------------------------------ finding by name
check("a name is found through WASAPI before MME",
      L.index_for_name("Microphone (Steam Streaming Microphone)", rows()) == 2)
check("MME's cut-off name still matches the full one",
      L.index_for_name("Microphone (Steam Streaming Microphone)",
                       [r for r in rows() if r[3] == "MME"]) == 1)
check("a different mic that starts the same way does not match",
      L.index_for_name("Microphone (Realtek HD Audio Mic input)", rows()) is None)

# ---------------------------------------------------------------- choosing
idx, msg = pick()
check(f"with Stereo Mix as default and nothing picked, it asks rather than "
      f"opening the Steam virtual mic: {msg[:70]!r}...",
      idx is None and "Stereo Mix" in msg and "Settings > Voice" in msg)
check("and names the microphones there are to pick from",
      "Headset (boAt Rockerz Hands-Free)" in msg and "Steam Streaming Microphone" in msg)

idx, msg = pick("Headset (boAt Rockerz Hands-Free)")
check("a picked mic is opened by name, through the entry that works", idx == 4)
check("checked the way the recorder checks it, at 16 kHz",
      (4, 16000) in STATE["opened"])

TABLE.insert(0, ("Headset (Some USB Mic)", 1, 1, True))            # everything renumbers
idx, msg = pick("Headset (boAt Rockerz Hands-Free)")
check("after a device is plugged in ahead of it, the same mic is still found", idx == 5)
TABLE.pop(0)

idx, msg = pick("Headset (Unplugged Thing)")
check(f"a picked mic that is gone, with no usable default, is reported: {msg!r}",
      idx is None and "not connected" in msg)

STATE["default"] = 4                                 # the headset is the default
idx, msg = pick("Headset (Unplugged Thing)")
check(f"a picked mic that is gone falls back to a real default, and says so: {msg!r}",
      idx == 4 and "Windows default" in msg)

idx, msg = pick("Stereo Mix (Realtek(R) Audio)")
check("picking a loopback is refused", idx is None and "speakers" in msg)

# ------------------------------------------------------- the older number pick
os.environ["JARVIS_VOICE_INPUT_NAME"] = ""
os.environ["JARVIS_VOICE_INPUT_DEVICE"] = "7"
check("a pick saved only as a number is still honoured by the terminal's lookup",
      L.input_device_index() == 7)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all mic choice checks passed")
