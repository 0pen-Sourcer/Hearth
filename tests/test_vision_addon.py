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

# A model whose projector wasn't beside it was called blind, even when its repo
# publishes one; a model Hearth didn't download had no repo to ask.
e = Path(tempfile.mkdtemp(prefix="hearth-vision2-"))
L.MODELS_DIR = e
(e / "Ornith-1.5-9B-Q4_K_M.gguf").write_bytes(b"x")
L._REPO_SIBLINGS.clear()
L.search_huggingface = lambda q, limit=12: [{"hf_repo": "someone/Other-GGUF"}, {"hf_repo": "ornith-ai/Ornith-1.5-9B-GGUF"}]
L._repo_siblings = lambda repo: ([{"rfilename": "Ornith-1.5-9B-Q4_K_M.gguf"}, {"rfilename": "mmproj-Ornith-1.5-9B-BF16.gguf", "size": 880000000}]
                                 if repo.startswith("ornith") else [{"rfilename": "Other-Q4_K_M.gguf"}])
v = L.vision_for_file(str(e / "Ornith-1.5-9B-Q4_K_M.gguf"))
check(f"a model whose repo ships a projector can see once it's fetched {v}",
      v.get("state") == "needs_file" and v.get("repo") == "ornith-ai/Ornith-1.5-9B-GGUF")
check("the repo found by its file is remembered", L.model_source("Ornith-1.5-9B-Q4_K_M.gguf") == "ornith-ai/Ornith-1.5-9B-GGUF")
check(f"it says what it would fetch, how big, and where it goes: beside the model, under the repo's name {v}",
      v.get("file") == "mmproj-Ornith-1.5-9B-BF16.gguf" and v.get("folder") == str(e)
      and v.get("save_as") == "Ornith-1.5-9B-GGUF-mmproj-Ornith-1.5-9B-BF16.gguf" and v.get("size_gb") == 0.88)
(e / v["save_as"]).write_bytes(b"x")
check("with it there, it sees", L.vision_for_file(str(e / "Ornith-1.5-9B-Q4_K_M.gguf")).get("state") == "sees")

# Its download sits beside the model, outside the models folder: Cancel has to find it there,
# and a pending one remembers the model it belongs to, so Resume brings it back to the same place.
import threading  # noqa: E402
other = Path(tempfile.mkdtemp(prefix="hearth-vision3-"))
name = "Ornith-1.5-9B-GGUF-mmproj-Ornith-1.5-9B-BF16.gguf"
(other / (name + ".part")).write_bytes(b"x" * 10)
L._dl_jobs[name] = {"dest_dir": str(other), "status": "paused", "stop": threading.Event()}
gone = L.discard_partial(name)
check(f"Cancel removes a vision file's partial download beside its model ({gone})",
      gone.get("ok") and not (other / (name + ".part")).exists())
L.save_pending_download("ornith-ai/Ornith-1.5-9B-GGUF", name, vision_for=str(other / "Ornith-1.5-9B-Q4_K_M.gguf"))
check("a pending vision download remembers its model",
      (L._read_pending().get(name) or {}).get("vision_for", "").endswith("Ornith-1.5-9B-Q4_K_M.gguf"))

# A cloud model was judged by fragments of its name; its provider's catalog says what it takes.
from hearth import tools as T, model_facts as MF  # noqa: E402
MF.from_catalog = lambda pid, mid: {"inputs": ["text"]} if mid == "claude-sonnet-text-only" else {"inputs": ["text", "image"]}
check("a cloud model's own catalog decides, not its name",
      not T._loaded_model_is_vision(model="claude-sonnet-text-only", base="https://api.anthropic.com/v1")
      and T._loaded_model_is_vision(model="ox-alpha", base="https://api.anthropic.com/v1"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all vision add-on checks passed")
