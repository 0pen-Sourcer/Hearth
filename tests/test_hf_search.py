"""Searching Hugging Face from Models finds what a person types or pastes.
"unsloth/gemma-4" came back empty: " gguf" was added to the words, and an
owner/name search with an extra word matches nothing.

    .\\.venv\\Scripts\\python.exe tests\\test_hf_search.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


asked = []


def fake_urlopen(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    asked.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query))
    return io.BytesIO(json.dumps([{"id": "unsloth/gemma-4-12b-it-GGUF", "downloads": 5}]).encode())


llmserver.urllib.request.urlopen = fake_urlopen
llmserver.search_huggingface("unsloth/gemma-4")
check(f"an owner/name search is sent as typed {asked[-1].get('search')}", asked[-1].get("search") == ["unsloth/gemma-4"])
check("the list is still kept to GGUF repos", asked[-1].get("filter") == ["gguf"])
r = llmserver.search_huggingface("https://huggingface.co/unsloth/gemma-4-12b-it-GGUF")
check(f"a pasted page link searches for its repo {asked[-1].get('search')}",
      asked[-1].get("search") == ["unsloth/gemma-4-12b-it-GGUF"] and r and r[0]["hf_repo"] == "unsloth/gemma-4-12b-it-GGUF")
check("an empty search asks nothing", llmserver.search_huggingface("  ") == [] and len(asked) == 2)

# IQ and ternary quants had no tier chip in the pick list.
tiers = {n: llmserver.quant_tier(n) for n in ("m-IQ4_NL.gguf", "m-IQ3_XXS.gguf", "m-UD-Q2_K_XL.gguf", "Bonsai-TQ1_0.gguf",
                                              "m-Q4_K_M.gguf", "m-BF16.gguf", "m-F32.gguf", "qwen3-8b.gguf")}
check(f"every quant gets its tier from its name {tiers}",
      list(tiers.values()) == ["Q4", "Q3", "Q2", "Q1", "Q4", "F16", "F32", ""])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all Hugging Face search checks passed")
