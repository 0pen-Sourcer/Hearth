"""What a prebuilt engine's release file is for, read from its name.

llama.cpp names them llama-<tag>-bin-<os>-<kind>-<arch>.<zip|tar.gz>;
stable-diffusion.cpp sd-<tag>-bin-<OS>-<distro>-<arch>-<kind>.zip, with cuda12
for cuda-12. Both ship the NVIDIA runtime as a separate cudart-... file. One
reader for both, by the words in the name, so no list of builds is kept.
"""
from __future__ import annotations

import platform
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

_OS = (("win", ("win", "windows")), ("linux", ("linux", "ubuntu")), ("mac", ("macos", "darwin", "osx")))
_KINDS = ("vulkan", "rocm", "sycl", "openvino", "opencl", "snapdragon", "metal", "cpu", "hip", "musa")


def this_os() -> str:
    return "win" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "linux"


def this_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def classify(name: str) -> Optional[Dict[str, Any]]:
    """{os, arch, backend, cuda, cudart} for a prebuilt archive, else None.
    A build that names no kind is the CPU one, a Mac's the Metal one."""
    low = (name or "").lower()
    if not low.endswith((".zip", ".tar.gz", ".tgz", ".tar.xz")):
        return None
    words = set(re.split(r"[-_.]+", re.sub(r"x86[-_]64", "x64", low)))
    os_ = next((o for o, keys in _OS if words & set(keys)), None)
    if not os_ or "bin" not in words:
        return None
    arch = "arm64" if words & {"arm64", "aarch64"} else "x64" if words & {"x64", "amd64", "x86"} else None
    if not arch:
        arch = "arm64" if os_ == "mac" else None
    if not arch:
        return None
    m = re.search(r"(?<![a-z])(?:cuda|cu)[-_]?(\d+(?:\.\d+)?)(?![\d.]*[a-z])", low)
    if m or "cuda" in words:
        backend, cuda = "cuda", (m.group(1) if m else "")
    else:
        backend = next((k for k in _KINDS if k in words), "metal" if os_ == "mac" else "cpu")
        cuda = ""
    return {"os": os_, "arch": arch, "backend": backend, "cuda": cuda, "cudart": low.startswith("cudart")}


def for_this_system(assets) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(asset, what it is) for the release files built for this system."""
    out = []
    for a in assets or []:
        p = classify(a.get("name") or "")
        if p and p["os"] == this_os() and p["arch"] == this_arch():
            out.append((a, p))
    return out


def _ver(v: str) -> List[int]:
    return [int(x) for x in v.split(".") if x.isdigit()]


def pick(assets, backend: str, cuda: str = "") -> Tuple[Optional[dict], Optional[dict], str]:
    """(binaries, runtime or None, CUDA version used) for this system and card.
    For NVIDIA, the CUDA version asked for when it is there, else the oldest,
    which the most drivers run; its runtime comes with the same major version."""
    here = for_this_system(assets)
    if backend != "cuda":
        bins = next((a for a, p in here if p["backend"] == backend and not p["cudart"]), None)
        return bins, None, ""
    versions = sorted({p["cuda"] for _a, p in here if p["backend"] == "cuda" and not p["cudart"]}, key=_ver)
    if versions and cuda not in versions:
        cuda = versions[0]
    bins = next((a for a, p in here if p["backend"] == "cuda" and p["cuda"] == cuda and not p["cudart"]), None)
    runtimes = [(a, p) for a, p in here if p["backend"] == "cuda" and p["cudart"]]
    runtime = next((a for a, p in runtimes if p["cuda"] == cuda), None) or next(
        (a for a, p in runtimes if p["cuda"].split(".")[0] == cuda.split(".")[0]), None)
    return bins, runtime, cuda
