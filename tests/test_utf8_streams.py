"""Output in any script, whatever stream it goes to.

Windows gives a pipe or a log file its own code page (cp1252), which holds no
Devanagari, and the packaged app ignores PYTHONIOENCODING. A heard Hindi sentence
was printed to the bridge's log before it was sent, so the print raised and the
sentence was lost. `python -I` ignores the environment the same way.

    .\\.venv\\Scripts\\python.exe tests\\test_utf8_streams.py
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix="hearth-utf8-")
os.environ.update(JARVIS_WORKSPACE=WORK, HEARTH_WORKSPACE=WORK, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, ROOT)

HINDI = "मुझसे हिंदी में बात करो"
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def run_to_file(args, label):
    out = os.path.join(WORK, label + ".txt")
    env = dict(os.environ, USERPROFILE=WORK, HOME=WORK)
    for k in ("PYTHONIOENCODING", "PYTHONUTF8"):
        env.pop(k, None)
    with open(out, "wb") as f:
        rc = subprocess.run(args, stdout=f, stderr=subprocess.STDOUT, env=env, cwd=WORK, timeout=180).returncode
    with open(out, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    return rc, text


py = sys.executable
probe = os.path.join(WORK, "probe.py")
with open(probe, "w", encoding="utf-8") as f:
    f.write(f"print({HINDI!r})\n")

rc, text = run_to_file([py, "-I", "-c", f"import sys; sys.path.insert(0, {ROOT!r}); import hearth; print({HINDI!r})"],
                       "package")
check(f"importing hearth, Hindi printed to a file is written as UTF-8 (rc {rc})", rc == 0 and HINDI in text)

for launcher in ("_launch_tray.py", "_launch_cli.py"):
    rc, text = run_to_file([py, "-I", os.path.join(ROOT, launcher), "--hearth-run-python", probe], launcher)
    check(f"{launcher}: a child it runs prints Hindi to a file (rc {rc})", rc == 0 and HINDI in text)

# The voice loop on a stream that can't hold Devanagari: the sentence still reaches the chat.
from hearth import realtime_voice as rv  # noqa: E402


class Recorder:
    def __init__(self, phrases):
        self.q, self.down = list(phrases), threading.Event()

    def text(self):
        if self.q:
            time.sleep(0.05)
            return self.q.pop(0)
        self.down.wait()
        raise RuntimeError("shut down")

    def clear_audio_queue(self): pass
    def stop(self): pass
    def shutdown(self): self.down.set()


class QuietTTS:
    def is_speaking(self): return False
    def seconds_since_spoke(self): return 99.0
    def set_voice_state(self, s): pass
    def last_spoken_text(self): return ""
    def recently_spoke_text(self): return ""
    def stop(self): pass

    @property
    def DEFAULT(self):
        return self


rv._REALTIME_AVAILABLE = True
rv._tts = QuietTTS()
rv._half_duplex = lambda: False
rec = Recorder([HINDI])
rv._build_recorder = lambda sess=None: rec
got: list = []
real_out = sys.stdout
sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
try:
    lease = rv.start_continuous(got.append)
    end = time.time() + 3
    while time.time() < end and not got:
        time.sleep(0.02)
    rv.stop_continuous(lease)
    time.sleep(0.2)
finally:
    sys.stdout = real_out
check(f"a Hindi sentence heard while the log can't hold it still reaches the chat ({got!r})", got == [HINDI])

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall utf-8 stream checks passed")
