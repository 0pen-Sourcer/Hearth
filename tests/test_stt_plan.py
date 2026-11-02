"""Where listening runs, chosen by what the graphics card has free: the card when the
speech model fits beside what is loaded there (a local model fills most of an 8 GB
card), else the processor with a model it writes a sentence out with in under a
second. The live caption always uses the smallest model, never a second copy of
the chosen one. Stand-ins for the card, ctranslate2 and the recorder.

    .\\.venv\\Scripts\\python.exe tests\\test_stt_plan.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

WORK = tempfile.mkdtemp(prefix="hearth-stt-plan-")
os.environ.update(JARVIS_WORKSPACE=WORK, HEARTH_WORKSPACE=WORK, HEARTH_NO_ACTIVITY_LOG="1")
for k in ("HEARTH_REALTIME_MODEL", "HEARTH_REALTIME_LANG"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import env as _env, realtime_voice as rv  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


sys.modules["ctranslate2"] = types.SimpleNamespace(get_cuda_device_count=lambda: 1, __file__=__file__)
rv._cudnn_present = lambda: True
FREE = {"mib": 4000}
import hearth.llmserver as ls  # noqa: E402
ls.detect_gpu_vram_free_gb = lambda: FREE["mib"] / 1024.0


def plan(model, device="cuda", lang="", free=4000):
    _env.set("STT_MODEL", model)
    _env.set("STT_DEVICE", device)
    _env.set("STT_LANGUAGE", lang)
    _env.set("VOICE", "af_heart")
    FREE["mib"] = free
    f = getattr(rv, "stt_plan", None)
    return f() if f else {}


p = plan("small", free=4000)
check(f"room on the card: the chosen model on it ({p})", p.get("device") == "cuda" and p.get("model") == "small")
p = plan("small", free=350)
check(f"a local model filling the card: the processor, with tiny for any language ({p})",
      p.get("device") == "cpu" and p.get("model") == "tiny" and "MiB" in p.get("why", ""))
p = plan("small.en", free=350)
check(f"English only: base.en on the processor ({p})", p.get("device") == "cpu" and p.get("model") == "base.en")
p = plan("small", lang="en", free=350)
check(f"English named in Settings: base.en on the processor ({p})", p.get("model") == "base.en")
p = plan("tiny.en", free=350)
check(f"a small one chosen already is kept ({p})", p.get("model") == "tiny.en" and p.get("device") == "cpu")
p = plan("small", device="cpu", free=4000)
check(f"the processor chosen: the chosen model on it ({p})", p.get("device") == "cpu" and p.get("model") == "small")

# The recorder it builds: the plan's model and device, and the smallest model for the caption.
got = {}


class Rec:
    def __init__(self, **kw):
        got.update(kw)


sys.modules["RealtimeSTT"] = types.SimpleNamespace(AudioToTextRecorder=Rec)
rv.model_downloaded = lambda size="": True
rv._fetch_model = lambda name: name
rv._let_recorder_use_gpu = lambda: None
plan("small", free=350)
sess = rv._Session(1, lambda t: None, None, None, None, None, fed=True)
rv._build_recorder(sess)
check(f"the recorder listens with the plan ({got.get('model')} on {got.get('device')})",
      got.get("model") == "tiny" and got.get("device") == "cpu")
check(f"the caption uses the smallest model, not a second copy ({got.get('realtime_model_type')})",
      got.get("realtime_model_type") == "tiny")
check(f"the session says what listens, for the timing log ({getattr(sess, 'stt', '')!r})",
      getattr(sess, "stt", "") == "tiny cpu")
got.clear()
plan("small", free=4000)
rv._build_recorder(rv._Session(2, lambda t: None, None, None, None, None, fed=True))
check(f"with room, small on the card and tiny for the caption ({got.get('model')}, {got.get('realtime_model_type')})",
      got.get("model") == "small" and got.get("device") == "cuda" and got.get("realtime_model_type") == "tiny")

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall stt plan checks passed")
