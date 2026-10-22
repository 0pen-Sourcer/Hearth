"""Listing audio devices from several threads at once doesn't crash Hearth.

PortAudio is started and stopped process-wide; two threads doing it at once
took the whole process down with an access violation and no Python error (the
wake word retrying a mic that wasn't plugged in while the window asked for the
device list). Runs the race in a child process, since the failure kills it.

    .\\.venv\\Scripts\\python.exe tests\\test_audio_device_race.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    import pyaudio  # noqa: F401
except Exception:
    print("skipped: pyaudio isn't installed here")
    sys.exit(0)

CHILD = f"""
import sys, threading
sys.path.insert(0, {ROOT!r})
from hearth import listen
def hammer():
    for _ in range(40):
        listen.device_rows()
ts = [threading.Thread(target=hammer) for _ in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
print("survived")
"""
for run in range(2):
    r = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, timeout=300)
    check(f"four threads listing devices at once, run {run + 1} (exit {r.returncode})",
          r.returncode == 0 and "survived" in r.stdout)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all audio device race checks passed")
