"""A model load that crashes partway does not wedge the next one.

The load marks the model as loading before it does the real work. An exception
after that point used to skip the cleanup: the model stayed marked, so every
later attempt was refused as a duplicate, and the progress the window watches
never reached an end, so it sat on "getting ready" forever.

    .\\.venv\\Scripts\\python.exe tests\\test_model_start_failure.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver                                         # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


d = tempfile.mkdtemp(prefix="hearth-start-")
model = os.path.join(d, "m.gguf")
with open(model, "wb") as f:
    f.write(b"GGUF" + b"\0" * 64)

calls = {"n": 0}


def boom():
    calls["n"] += 1
    raise RuntimeError("something inside the load broke")


llmserver.detect_gpu_compute_cap = boom
llmserver._set_progress(phase="idle", error=None)

r = llmserver.start_builtin(model, ctx=32768)
check("a crash inside the load comes back as an error, not an exception",
      r.get("ok") is False and "broke" in (r.get("error") or ""))
check("the model is no longer marked as loading", not llmserver._starting_paths)
check("the progress the window watches says it failed", llmserver._load_progress.get("phase") == "error")

r2 = llmserver.start_builtin(model, ctx=32768)
check("the next attempt really runs instead of being refused as a duplicate",
      calls["n"] == 2 and "duplicate" not in (r2.get("error") or ""))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all model start failure checks passed")
