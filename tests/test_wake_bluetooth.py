"""The wake word leaves a Bluetooth headset's mic alone unless told otherwise.

Holding a Bluetooth headset's mic open keeps it in hands-free mode, where music
and video play at call quality, for as long as Hearth sits in the tray. Windows
lists such a mic, raw, under its Bluetooth hands-free driver with the headset's
name in brackets; that is how one is told apart. The device rows below are the
ones Windows gave on a PC with a boAt Rockerz headset.

    .\\.venv\\Scripts\\python.exe tests\\test_wake_bluetooth.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


if sys.platform != "win32":
    print("skipped: the Bluetooth hands-free driver is a Windows one")
    sys.exit(0)

from hearth import listen, wake, web  # noqa: E402

ROWS = [
    (1, "Microphone (Realtek(R) Audio)", 2, "MME"),
    (2, "Headset (boAt Rockerz)", 1, "MME"),
    (24, "Microphone (Steam Streaming Microphone)", 2, "Windows WASAPI"),
    (29, "Microphone (Realtek HD Audio Mic input)", 2, "Windows WDM-KS"),
    (31, "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free AG Audio%0\n;(boAt Rockerz))", 1, "Windows WDM-KS"),
]

check("a Bluetooth headset's mic is told apart by Windows' hands-free driver entry",
      listen.is_bluetooth_handsfree("Headset (boAt Rockerz)", ROWS))
check("a wired or built-in mic is not", not listen.is_bluetooth_handsfree("Microphone (Realtek(R) Audio)", ROWS)
      and not listen.is_bluetooth_handsfree("Microphone (Steam Streaming Microphone)", ROWS))

opened = []
listen.device_rows = lambda: (ROWS, 1)
listen.MicInput = lambda dev, rate, block: opened.append(dev) or object()
settings = {"voice_input_name": "Headset (boAt Rockerz)"}
web._load_settings = lambda: dict(settings)

w = wake.WakeListener(on_wake=lambda t: None, phrases=["jarvis"])
stream, why = w._open_mic()
check(f"with the headset picked, the wake word doesn't open its mic and says why ({why[:60]}...)",
      stream is None and not opened and "Bluetooth" in why)

settings["wake_on_bluetooth"] = True
stream, why = w._open_mic()
check("with the switch on, it listens on the headset", stream is not None and opened == [2])

settings.update(voice_input_name="Microphone (Realtek(R) Audio)", wake_on_bluetooth=False)
opened.clear()
stream, why = w._open_mic()
check("a wired mic is opened as before", stream is not None and opened == [1])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all wake Bluetooth checks passed")
