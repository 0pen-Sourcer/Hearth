"""The graphics card is found on Linux and macOS, so the engine download picks
the build that uses it.

Linux: the kernel's own device files (AMD reports its memory there; NVIDIA's
details come from nvidia-smi). An AMD card used to be invisible and got the CPU
build. Apple silicon: memory shared with the GPU, about three quarters usable.

    .\\.venv\\Scripts\\python.exe tests\\test_gpu_platforms.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import gpu, llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


drm = tempfile.mkdtemp(prefix="hearth-drm-")


def card(n, vendor, vram=None):
    d = os.path.join(drm, f"card{n}", "device")
    os.makedirs(d)
    open(os.path.join(d, "vendor"), "w").write(vendor + "\n")
    if vram is not None:
        open(os.path.join(d, "mem_info_vram_total"), "w").write(str(vram) + "\n")


card(0, "0x8086")                       # integrated Intel
card(1, "0x1002", 16 * 1024 ** 3)       # AMD with 16 GB
os.makedirs(os.path.join(drm, "card1-DP-1"))   # a connector, not a card
cards = gpu._linux_cards(drm)
check("the kernel's cards are read", [c["vendor"] for c in cards] == ["Intel", "AMD"])
check("AMD's own memory is read", cards[1]["mem_total_mb"] == 16384)

real_platform = sys.platform
gpu._linux_cards = lambda drm="": cards
gpu._nvidia_smi = lambda: []
sys.platform = "linux"
try:
    gpu._adapters_cache.clear()
    check("on Linux the AMD card is the one models run on", gpu.primary()["vendor"] == "AMD")
    check("so the Vulkan build is fetched, not the CPU one", llmserver.engine_backend() == "vulkan")
    gpu._adapters_cache.clear()
    gpu._linux_cards = lambda drm="": [dict(c, vendor="NVIDIA", mem_total_mb=0) for c in cards[1:]]
    gpu._nvidia_smi = lambda: [{"name": "NVIDIA GeForce RTX 4070", "mem_total_mb": 12282}]
    p = gpu.primary()
    check("an NVIDIA card is named by nvidia-smi", p["name"] == "NVIDIA GeForce RTX 4070" and p["mem_total_mb"] == 12282)
    check("and gets the CUDA build", llmserver.engine_backend() == "cuda")
finally:
    sys.platform = real_platform


class _R:
    stdout = "34359738368\nApple M3 Pro\n"


import platform  # noqa: E402
real_machine, real_run = platform.machine, subprocess.run
platform.machine = lambda: "arm64"
subprocess.run = lambda *a, **k: _R()
try:
    mac = gpu._apple_silicon()
finally:
    platform.machine, subprocess.run = real_machine, real_run
check("Apple silicon: three quarters of 32 GB for the GPU", mac and mac[0]["mem_total_mb"] == 24576
      and mac[0]["name"] == "Apple M3 Pro")

gpu._adapters_cache.clear()
shutil.rmtree(drm, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all gpu platform checks passed")
