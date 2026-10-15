"""A model from Hugging Face that can see gets its vision projector with it.
The pick list offered mmproj files as if they were models, and downloading a
model never fetched its projector, so a vision model came down blind.

    .\\.venv\\Scripts\\python.exe tests\\test_vision_addon.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import llmserver as L  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


SIBS = [{"rfilename": n} for n in ("gemma-4-12b-it-Q4_K_M.gguf", "gemma-4-12b-it-Q8_0.gguf",
                                   "mmproj-BF16.gguf", "mmproj-F16.gguf", "mmproj-F32.gguf", "README.md")]
L.urllib.request.urlopen = lambda req, timeout=None: io.BytesIO(json.dumps({"siblings": SIBS}).encode())
files = L.list_hf_files("unsloth/gemma-4-12b-it-GGUF")
names = [f["filename"] for f in files]
check(f"the pick list holds models only {names}", names and not any("mmproj" in n for n in names))
check("and says they can see", all(f.get("vision") for f in files))
check("the F16 projector is the one fetched", L.vision_addon(SIBS) == "mmproj-F16.gguf")

d = Path(tempfile.mkdtemp(prefix="hearth-vision-"))
L.MODELS_DIR = d
got = []
L._download_with_resume = lambda url, dest, tmp, prog, cancel=None: (got.append((url, dest.name)), dest.write_bytes(b"x"))
job = {"repo": "unsloth/gemma-4-12b-it-GGUF", "stop": __import__("threading").Event()}
L._fetch_vision_addon(job, None)
check(f"a download brings the projector, named for its repo {got}",
      got and got[0][1] == "gemma-4-12b-it-GGUF-mmproj-F16.gguf" and got[0][0].endswith("mmproj-F16.gguf"))
(d / "gemma-4-12b-it-Q4_K_M.gguf").write_bytes(b"x")
(d / "Qwen3-VL-8B-Instruct-Q4_K_M.gguf").write_bytes(b"x")
(d / "Qwen3-VL-8B-Instruct-GGUF-mmproj-F16.gguf").write_bytes(b"x")
pick = L._find_mmproj(str(d / "gemma-4-12b-it-Q4_K_M.gguf"))
check(f"with two models' projectors side by side, each model gets its own ({os.path.basename(pick or '')})",
      pick and os.path.basename(pick) == "gemma-4-12b-it-GGUF-mmproj-F16.gguf"
      and os.path.basename(L._find_mmproj(str(d / "Qwen3-VL-8B-Instruct-Q4_K_M.gguf"))) == "Qwen3-VL-8B-Instruct-GGUF-mmproj-F16.gguf")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all vision add-on checks passed")
