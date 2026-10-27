"""JARVIS can load a model file itself: by part of its name, beside the brain
for helpers when it thinks on a cloud model or an agent program (the brain
stays), or as its own brain otherwise, through the same load the Models tab
uses. It asks first, and names the files there are when nothing matches.

    .\\.venv\\Scripts\\python.exe tests\\test_load_model_tool.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-load-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import llmserver as L, tools as T, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


check("settings are the test's own", os.path.dirname(web.SETTINGS_PATH) == WS)
files = {}
for name in ("Ornith-1.5-9B-Q4_K_M.gguf", "Qwen3.6-35B-A3B-Q4_K_M.gguf", "Qwen3.6-35B-A3B-Q8_0.gguf"):
    p = os.path.join(WS, name)
    open(p, "wb").close()
    files[name] = p
L.scan_disk_for_models = lambda *a, **k: [{"path": p, "filename": n, "size_gb": 5.4} for n, p in files.items()]
calls = []
L.start_helper = lambda path, ctx=16384, **k: calls.append(("helper", path, ctx)) or {"ok": True, "url": "http://127.0.0.1:8091/v1"}
L.start_builtin = lambda path, **k: calls.append(("brain", path, k.get("ctx"))) or {"ok": True, "url": "http://127.0.0.1:8090/v1"}
L.get_model_config = lambda path: {"ctx": 24576}

web._save_settings({"llm_provider": "codex", "llm_model": "gpt-6.1"})
out = T._load_model({"model": "ornith"})
check(f"on an agent brain it loads for helpers ({out[:60]})", calls and calls[-1][0] == "helper"
      and calls[-1][1] == files["Ornith-1.5-9B-Q4_K_M.gguf"])
s = web._load_settings()
check("and the brain stays Codex", s.get("llm_provider") == "codex" and s.get("llm_model") == "gpt-6.1")
check("the helper model is remembered", s.get("helper_model") == files["Ornith-1.5-9B-Q4_K_M.gguf"])

out = T._load_model({"model": "qwen3.6"})
check(f"two files that match are named back, nothing loads ({out[:70]})",
      out.startswith("Error") and "Q8_0" in out and "Q4_K_M" in out and len(calls) == 1)
out = T._load_model({"model": "llama 70b"})
check("nothing matching names what there is", out.startswith("Error") and "Ornith-1.5-9B" in out)

out = T._load_model({"model": "qwen3.6 q8", "for": "chat"})
check(f"for chat it is the brain, with the settings tuned for it ({calls[-1]})",
      calls[-1] == ("brain", files["Qwen3.6-35B-A3B-Q8_0.gguf"], 24576))
check("and the brain is now this PC", web._load_settings().get("llm_provider") == "local")

web._save_settings({"llm_provider": "local"})
T._load_model({"model": "ornith"})
check("with a local brain, no 'for' means the brain", calls[-1][0] == "brain")

check("it asks before it runs", "load_model" in T._RISKY_TOOLS)
check("and list_models points at it", "load_model" in "\n".join(T._disk_models_lines()))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all load model checks passed")
