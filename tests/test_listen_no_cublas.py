"""A card that ctranslate2 sees, on a PC without cuBLAS (Lite on an NVIDIA PC):
listening stays on the processor, chosen or not, and the card doesn't read as
usable. With cuBLAS the card is picked as before. Stand-in model, card and DLL.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_listen_no_cublas.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401

os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
os.environ.pop("HEARTH_STT_DEVICE", None)

from hearth import listen as L  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


loaded: list = []


class Model:
    def __init__(self, size, device="", compute_type="", download_root=""):
        loaded.append((device, compute_type))


sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=Model)
sys.modules["ctranslate2"] = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
have = {"dll": False}
real_dll = ctypes.WinDLL


def dll(name, *a, **k):
    if "cublas" in name.lower() and not have["dll"]:
        raise FileNotFoundError(f"Could not find module '{name}'")
    return real_dll("kernel32.dll") if "cublas" in name.lower() else real_dll(name, *a, **k)


ctypes.WinDLL = dll


def pick(explicit, cublas):
    have["dll"] = cublas
    if hasattr(L, "_cublas_seen"):
        L._cublas_seen.clear()
    L._model = None
    L.COMPUTE_TYPE = "int8"
    loaded.clear()
    if explicit:
        os.environ["HEARTH_STT_DEVICE"] = explicit
    else:
        os.environ.pop("HEARTH_STT_DEVICE", None)
    L._try_load_model()
    return loaded[-1] if loaded else None


check("no cuBLAS: the card doesn't read as usable", (have.update(dll=False), not L.cuda_available())[1])
got = pick("", False)
check(f"no cuBLAS, nothing chosen: the processor ({got})", got == ("cpu", "int8"))
got = pick("cuda", False)
check(f"no cuBLAS, the card saved in settings: the processor ({got})", got == ("cpu", "int8"))
got = pick("", True)
check(f"cuBLAS loads: the card, in float16 ({got})", got == ("cuda", "float16"))
check("and reads as usable", L.cuda_available())

print("PASS" if not FAILS else f"FAILED: {len(FAILS)}")
sys.exit(1 if FAILS else 0)
