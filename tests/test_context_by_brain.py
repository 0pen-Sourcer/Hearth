"""Where a turn's context size comes from, by brain: a server on this PC or the
network is asked for what it loaded, OpenRouter's catalog is read, and a named
cloud provider isn't sent local probes (they cost a second a turn and only
missed). A local model kept for helpers doesn't change a cloud brain's size.

    .\\.venv\\Scripts\\python.exe tests\\test_context_by_brain.py
"""
from __future__ import annotations

import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-ctxbrain-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
os.environ.pop("HEARTH_CONTEXT", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import headless as H, llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


probed = []
H.autodetect_context = lambda mid: probed.append(H.LOCAL_API_BASE) or (8192 if "localhost" in H.LOCAL_API_BASE
                                                                         or "192.168" in H.LOCAL_API_BASE else None)
H.listed_context = lambda base, key, mid: 200000 if "anthropic" in base else None
# A local model loaded for helpers on port 1234 while the brain is in the cloud.
llmserver._proc_info = {"ctx": 16384, "url": "http://127.0.0.1:1234/v1", "model_path": "x.gguf"}

H.LOCAL_API_BASE = "https://api.anthropic.com/v1/"
tok, src = H.resolve_context_tokens("claude-sonnet-5-5")
check(f"a cloud brain takes its provider's size, not the helpers' local model ({tok}, {src})", tok == 200000)
check(f"and no local probe was sent to it ({probed})", probed == [])

H.LOCAL_API_BASE = "http://localhost:1234/v1"
tok, src = H.resolve_context_tokens("x")
check(f"the built-in server says what it loaded ({tok}, {src})", tok == 16384)

H.LOCAL_API_BASE = "http://localhost:11434/v1"
probed.clear()
tok, src = H.resolve_context_tokens("llama")
check(f"another server on this PC is asked ({tok}, {src})", probed and tok == 8192)

H.LOCAL_API_BASE = "http://192.168.1.5:1234/v1"
probed.clear()
tok, src = H.resolve_context_tokens("llama")
check(f"a server on the network is asked too ({tok}, {src})", probed and tok == 8192)

H.LOCAL_API_BASE = "https://openrouter.ai/api/v1"
probed.clear()
H.resolve_context_tokens("openai/gpt-5")
check("OpenRouter still has its catalog read", probed == ["https://openrouter.ai/api/v1"])

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall context-by-brain checks passed")
