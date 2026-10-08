"""PrismML's ternary models run on PrismML's engine, and nothing else changes.

A model packed as PQ2_0 or PTQ1_0 failed to load with "invalid ggml type 142",
because only PrismML's own build of llama.cpp reads those packings and Hearth
only ever started the stock one.

Uses a throwaway home folder and small GGUF files written here.

    .\\.venv\\Scripts\\python.exe tests\\test_prism_engine.py
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="hearth-prism-")
os.environ["USERPROFILE"] = os.environ["HOME"] = HOME
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver as L                                   # noqa: E402

if not os.path.expanduser("~").lower().startswith(HOME.lower()):
    sys.exit("refusing to run: home is not the throwaway folder")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def gguf(path, tensor_types):
    """A GGUF header with one string key, one array key and the given tensors."""
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", len(tensor_types)) + struct.pack("<Q", 2)
    out += s("general.architecture") + struct.pack("<I", 8) + s("llama")
    out += s("tokenizer.ggml.scores") + struct.pack("<I", 9) + struct.pack("<I", 6) + struct.pack("<Q", 3) + b"\0" * 12
    for i, t in enumerate(tensor_types):
        out += s(f"blk.{i}.w") + struct.pack("<I", 2) + struct.pack("<QQ", 64, 64) + struct.pack("<I", t) + struct.pack("<Q", 0)
    Path(path).write_bytes(out)
    return path


def engine(tag):
    d = Path(HOME, ".hearth", "llamacpp", tag)
    d.mkdir(parents=True, exist_ok=True)
    (d / "llama-server.exe").write_bytes(b"MZ" + b"\0" * (L._MIN_SERVER_EXE_BYTES + 10))


ternary = gguf(os.path.join(HOME, "Bonsai-PQ2_0.gguf"), [0, 142, 30])
plain = gguf(os.path.join(HOME, "plain-Q4_K_M.gguf"), [0, 12, 14])
check(f"the tensor types are read from the header {sorted(L.gguf_tensor_types(ternary))}",
      L.gguf_tensor_types(ternary) == {0, 30, 142})
check("a PQ2_0 model is known to need PrismML's engine; a Q4_K_M one isn't",
      L.needs_prism(ternary) and not L.needs_prism(plain))

engine("b10488")
L.reset_native_cache()
eng, why = L.engine_for(ternary)
check(f"without PrismML's engine the load says where to get it, before trying {why!r}",
      eng is None and "PrismML" in why and "Engine" in why)
r = L.start_builtin(ternary)
check(f"loading it refuses with that reason instead of starting the stock engine {r.get('error')!r}",
      r.get("ok") is False and r.get("needs") == "prism")

engine("prism-b10754-2459f68")
L.reset_native_cache()
eng, why = L.engine_for(ternary)
check(f"with it installed the ternary model gets PrismML's engine {eng}",
      eng and "prism-b10754" in eng["exe"] and not why)
check("every other model still gets the stock engine",
      L.engine_for(plain)[0]["label"] == "b10488" and L.find_native_llama_server()["label"] == "b10488")
check(f"PrismML's build isn't listed as a stock engine build {L.installed_llama_tags()}",
      L.installed_llama_tags() == ["b10488"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all prism engine checks passed")
