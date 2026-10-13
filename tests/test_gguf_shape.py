"""What a GGUF file is, read from its header.

An image-generation GGUF was listed under the models as one to chat with, and a
file a download had cut short was listed as ready to load. A chat model always
carries a tokenizer; a file is complete when it reaches past where its own
tensor table says the last tensor starts.

    .\\.venv\\Scripts\\python.exe tests\\test_gguf_shape.py
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
from hearth import llmserver as L                                   # noqa: E402

WS = tempfile.mkdtemp(prefix="hearth-ggufshape-")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def s(x):
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def gguf(name, tokenizer=True, cut=0):
    kv = [s("general.architecture") + struct.pack("<I", 8) + s("demo")]
    if tokenizer:
        kv.append(s("tokenizer.ggml.model") + struct.pack("<I", 8) + s("gpt2"))
    head = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 2) + struct.pack("<Q", len(kv)) + b"".join(kv)
    for i, off in enumerate((0, 1024)):
        head += s(f"blk.{i}.w") + struct.pack("<I", 1) + struct.pack("<Q", 256) + struct.pack("<I", 0) + struct.pack("<Q", off)
    pad = (-len(head)) % 32
    data = b"\0" * 2048
    path = os.path.join(WS, name)
    with open(path, "wb") as f:
        f.write((head + b"\0" * pad + data)[: len(head) + pad + len(data) - cut])
    return path


chat, image, short = gguf("chat.gguf"), gguf("image.gguf", tokenizer=False), gguf("short.gguf", cut=1500)
check(f"a language model reads as a chat model, complete {L.gguf_shape(chat)}",
      L.gguf_shape(chat) == {"chat": True, "complete": True})
check(f"one with no tokenizer (an image or video model) doesn't {L.gguf_shape(image)}",
      L.gguf_shape(image)["chat"] is False)
check(f"a file cut short before its last tensor reads as incomplete {L.gguf_shape(short)}",
      L.gguf_shape(short)["complete"] is False)
from pathlib import Path                                             # noqa: E402
e = L._model_entry(Path(image), "test")
check("the Models list marks it, so it isn't offered as a model to chat with",
      e.get("not_chat") is True and "not_chat" not in L._model_entry(Path(chat), "test"))
check("a file that isn't a GGUF is never hidden on a guess",
      L.gguf_shape(__file__) == {"chat": True, "complete": True})

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all gguf shape checks passed")
