"""First-run setup offers a model that fits, without Hearth keeping a list.

Setup only listed models already on disk, so someone with none finished it
with nothing to run. It now asks Hugging Face for the most downloaded chat
GGUFs and offers the first one its makers published that fits the card,
passing over community remixes ("uncensored", "abliterated" and the like).

    .\\.venv\\Scripts\\python.exe tests\\test_what_to_get.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-whattoget-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
sys.path.insert(0, ROOT)

from hearth import llmserver as L  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def m(repo, params_b, base=None, dl=1000):
    tags = [f"base_model:{base}"] if base else []
    return {"id": repo, "downloads": dl, "tags": tags, "gguf": {"total": int(params_b * 1e9)}}


LISTING = [
    m("someone/Big-27B-Uncensored-GGUF", 27, "Maker/Big-27B", 9_000_000),
    m("remixer/Small-8B-GGUF", 8, "Maker/Small-8B", 8_000_000),            # someone else's copy
    m("CoolCorp/Random-1B", 1.8, None, 7_000_000),                          # name not theirs
    m("maker-ai/Maker-35B-GGUF", 35, None, 6_000_000),                      # theirs, too big for 8 GB
    m("maker-ai/Maker-9B-GGUF", 9, None, 5_000_000),                        # theirs, fits
    m("Maker/Small-4B-GGUF", 4, "Maker/Small-4B", 4_000_000),
]


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


L.urllib.request.urlopen = lambda req, timeout=0: _Resp(json.dumps(LISTING).encode())
L.list_hf_files = lambda repo: [{"filename": repo.split("/")[-1] + "-Q4_K_M.gguf", "size_gb": 5.0,
                                 "recommended": True, "fit_tier": "good", "quant_tier": 4}]

check("a remix of someone else's model isn't its maker's", not L._from_its_maker(LISTING[1]))
check("a name that isn't the publisher's isn't either", not L._from_its_maker(LISTING[2]))
check("the makers' own GGUF is", L._from_its_maker(LISTING[4]) and L._from_its_maker(LISTING[5]))

pick = L.suggest_model(8)
check(f"on an 8 GB card the most downloaded fit from its makers is offered ({pick})",
      pick and pick["repo"] == "maker-ai/Maker-9B-GGUF" and pick["filename"].endswith("Q4_K_M.gguf"))
L._SUGGEST.clear()
small = L.suggest_model(4)
check(f"on a 4 GB card something smaller ({small and small['repo']})", small and small["repo"] == "Maker/Small-4B-GGUF")
L._SUGGEST.clear()
L.urllib.request.urlopen = lambda req, timeout=0: (_ for _ in ()).throw(OSError("offline"))
check("offline it offers nothing rather than guessing", L.suggest_model(8) is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all what-to-get checks passed")
