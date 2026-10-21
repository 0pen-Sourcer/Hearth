"""Discover's fit doesn't count Hearth's own loaded model against a new one.

With a model loaded, an 8 GB card showed a 4.5 GB Q4 as "mostly CPU": the fit
was measured against what was free right then, but loading the new model stops
Hearth's server first. Another program's model still counts. Stand-in Hugging
Face reply and GPU readings.

    .\\.venv\\Scripts\\python.exe tests\\test_fit_with_model_loaded.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-fit-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


repo = {"siblings": [{"rfilename": "Chat-8B-Q4_K_M.gguf", "size": int(4.48 * 1024 ** 3)}]}
llmserver.urllib.request.urlopen = lambda *a, **k: _Resp(json.dumps(repo).encode())
llmserver.detect_gpu_vram_free_gb = lambda: 0.5
llmserver.detect_gpu_vram_gb = lambda: 8.0

llmserver.read_runfile = lambda *a, **k: {"url": "http://127.0.0.1:1234/v1", "model": "other.gguf"}
ours = llmserver.list_hf_files("maker/Chat-8B-GGUF")[0]
check(f"with Hearth's own model loaded, a Q4 that fits the card says it fits ({ours['fit_tier']}, {ours['fit_label']})",
      ours["fit_tier"] == "good")

llmserver.read_runfile = lambda *a, **k: {}
llmserver._proc = None
theirs = llmserver.list_hf_files("maker/Chat-8B-GGUF")[0]
check(f"with another program's model holding the card, it still says it won't fit ({theirs['fit_tier']})",
      theirs["fit_tier"] != "good")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all fit checks passed")
