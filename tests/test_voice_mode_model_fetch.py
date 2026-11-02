"""Voice mode fetches its speech model in this process (where truststore is set
up) and hands the recorder's worker a folder on disk, so behind HTTPS inspection
the worker never goes online; a recorder that doesn't start ends on a plain
error after a limit instead of waiting forever, on the processor as on the card.
Stand-in recorder and download; no mic, no network.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_voice_mode_model_fetch.py
"""
from __future__ import annotations

import os
import ssl
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401

os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
os.environ["HEARTH_STT_DEVICE"] = "cpu"
os.environ["HEARTH_STT_MODEL"] = "base.en"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


built: list = []
mode = {"hang": False}


class FakeRecorder:
    def __init__(self, **kw):
        built.append(kw)
        if mode["hang"]:
            threading.Event().wait()     # the worker died; the real constructor waits forever

    def shutdown(self):
        pass


sys.modules["RealtimeSTT"] = types.SimpleNamespace(AudioToTextRecorder=FakeRecorder)
sys.modules["torch"] = types.ModuleType("torch")

from hearth import realtime_voice as R  # noqa: E402
import faster_whisper.utils as fwu  # noqa: E402

check("truststore is set up in this process", "truststore" in type(ssl.create_default_context()).__module__)

folder = tempfile.mkdtemp(dir=_home.HOME)
fetched: list = []
fwu.download_model = lambda name, *a, **k: (fetched.append(name), folder)[1]

sess = R._Session(1, None, None, None, None, None, fed=True)


def build(limit=20.0):
    box: dict = {}

    def run():
        try:
            box["rec"] = R._build_recorder(sess)
        except Exception as e:
            box["err"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(limit)
    return box, t.is_alive()


box, hung = build()
check("the recorder is built", "rec" in box and not hung)
check("the model is fetched in this process", fetched == ["base.en"])
check("the worker gets the folder on disk, not a name to download",
      bool(built) and built[-1].get("model") == folder)

# Download fails (a certificate the worker couldn't check, say): a plain error, no recorder.
def _refuse(name, *a, **k):
    raise ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")
fwu.download_model = _refuse
built.clear()
box, hung = build()
check("a failed download ends with a plain error: " + str(box.get("err")),
      not hung and "couldn't download the speech model" in str(box.get("err", "")) and not built)

# The recorder never starts: on the processor too, a limit and an error.
fwu.download_model = lambda name, *a, **k: folder
mode["hang"] = True
R.CPU_START_S = 1.0
t0 = time.time()
box, hung = build(limit=15.0)
check(f"a recorder that never starts ends in an error ({time.time() - t0:.1f} s): {box.get('err')}",
      not hung and isinstance(box.get("err"), TimeoutError) and "didn't start" in str(box.get("err")))

print("PASS" if not FAILS else f"FAILED: {len(FAILS)}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
