"""The engine download picks and unpacks the right llama.cpp build on Windows,
Linux and macOS.

Release files are named llama-<tag>-bin-<os>-<kind>-<arch>; Linux and macOS ship
.tar.gz archives with everything in one llama-<tag>/ folder and libraries as
links. Read by one parser, so no list of builds is kept. With HEARTH_TEST_TARS
pointing at a folder holding real llama-*-bin-ubuntu-x64 / -macos-arm64
archives, those are unpacked; otherwise small stand-ins of the same shape are.

    .\\.venv\\Scripts\\python.exe tests\\test_engine_platforms.py
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import llmserver as L  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


TAG = "b11808"
NAMES = """cudart-llama-b11808-bin-ubuntu-cuda-12.8-x64.tar.gz
cudart-llama-b11808-bin-ubuntu-cuda-13.4-x64.tar.gz
cudart-llama-bin-win-cuda-12.4-x64.zip
cudart-llama-bin-win-cuda-13.4-x64.zip
llama-b11808-bin-linux-arm64-snapdragon.tar.gz
llama-b11808-bin-macos-arm64.tar.gz
llama-b11808-bin-macos-x64.tar.gz
llama-b11808-bin-ubuntu-arm64.tar.gz
llama-b11808-bin-ubuntu-cuda-12.8-x64.tar.gz
llama-b11808-bin-ubuntu-cuda-13.4-x64.tar.gz
llama-b11808-bin-ubuntu-vulkan-x64.tar.gz
llama-b11808-bin-ubuntu-x64.tar.gz
llama-b11808-bin-win-cpu-x64.zip
llama-b11808-bin-win-cuda-12.4-x64.zip
llama-b11808-bin-win-vulkan-x64.zip
llama-b11808-ui.tar.gz
llama-b11808-xcframework.zip""".split()

p = L.parse_asset
check("a plain Linux build is the CPU one", p("llama-b11808-bin-ubuntu-x64.tar.gz")["backend"] == "cpu")
check("a macOS build is Metal", p("llama-b11808-bin-macos-arm64.tar.gz")["backend"] == "metal")
check("a CUDA build carries its version", p("llama-b11808-bin-ubuntu-cuda-12.8-x64.tar.gz")["cuda"] == "12.8")
check("the CUDA runtime is told apart", p("cudart-llama-bin-win-cuda-12.4-x64.zip")["cudart"]
      and not p("llama-b11808-bin-win-cuda-12.4-x64.zip")["cudart"])
check("files that aren't builds are left out",
      p("llama-b11808-ui.tar.gz") is None and p("llama-b11808-xcframework.zip") is None)


def stand_in(name: str, system: str) -> bytes:
    """An archive shaped like the real one: server + its impl library + a link."""
    exe = "llama-server.exe" if system == "win" else "llama-server"
    ext = {"win": ".dll", "macos": ".dylib", "ubuntu": ".so"}[system]
    pre = "" if system == "win" else "lib"
    head = b"MZ" if system == "win" else (b"\xcf\xfa\xed\xfe" if system == "macos" else b"\x7fELF")
    kind = (p(name) or {}).get("backend", "cpu")
    files = {exe: head + b"\0" * 9000, f"{pre}llama-server-impl{ext}": b"x" * 20000,
             f"{pre}ggml-base{ext}.0": b"x" * 100}
    if kind in ("cuda", "vulkan", "metal"):
        files[f"{pre}ggml-{kind}{ext}"] = b"x" * 100
    buf = io.BytesIO()
    if name.endswith(".zip"):
        with zipfile.ZipFile(buf, "w") as z:
            for n, b in files.items():
                z.writestr(n, b)
    else:
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for n, b in files.items():
                ti = tarfile.TarInfo(f"llama-{TAG}/{n}")
                ti.size, ti.mode = len(b), 0o644
                t.addfile(ti, io.BytesIO(b))
            ln = tarfile.TarInfo(f"llama-{TAG}/{pre}ggml-base{ext}")
            ln.type, ln.linkname, ln.mode = tarfile.SYMTYPE, f"{pre}ggml-base{ext}.0", 0o777
            t.addfile(ln)
    return buf.getvalue()


real = Path(os.environ.get("HEARTH_TEST_TARS", ""))
home = Path(tempfile.mkdtemp(prefix="hearth-engine-"))
os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
asked: list = []


def fake_download(url, path, part, prog, ua=None, cancel=None):
    name = url.rsplit("/", 1)[-1]
    asked.append(name)
    src = real / name if str(real) else None
    if src and src.is_file():
        shutil.copy(src, path)
    else:
        path.write_bytes(stand_in(name, L.parse_asset(name)["os"]))


L._download_with_resume = fake_download
L._latest_build_release = lambda timeout=20, repo="": {
    "tag_name": TAG, "assets": [{"name": n, "size": 1, "browser_download_url": f"https://x/{n}"} for n in NAMES]}

real_platform = sys.platform
for system, platform, arch, backend, want in [
        ("ubuntu", "linux", "x64", "cpu", ["llama-b11808-bin-ubuntu-x64.tar.gz"]),
        ("ubuntu", "linux", "x64", "cuda", ["llama-b11808-bin-ubuntu-cuda-12.8-x64.tar.gz",
                                            "cudart-llama-b11808-bin-ubuntu-cuda-12.8-x64.tar.gz"]),
        ("ubuntu", "linux", "x64", "vulkan", ["llama-b11808-bin-ubuntu-vulkan-x64.tar.gz"]),
        ("ubuntu", "linux", "arm64", "cpu", ["llama-b11808-bin-ubuntu-arm64.tar.gz"]),
        ("macos", "darwin", "arm64", "metal", ["llama-b11808-bin-macos-arm64.tar.gz"]),
        ("macos", "darwin", "x64", "metal", ["llama-b11808-bin-macos-x64.tar.gz"]),
        ("win", "win32", "x64", "cuda", ["llama-b11808-bin-win-cuda-12.4-x64.zip",
                                         "cudart-llama-bin-win-cuda-12.4-x64.zip"])]:
    shutil.rmtree(home / ".hearth", ignore_errors=True)
    asked.clear()
    sys.platform = platform
    L.SERVER_EXE = "llama-server.exe" if platform == "win32" else "llama-server"
    L.asset_arch = lambda a=arch: a
    try:
        out = L.download_llama_runtime(backend=backend, force=True)
    finally:
        sys.platform = real_platform
    label = f"{system} {arch} {backend}"
    check(f"{label}: fetches {', '.join(want)}", asked == want)
    exe = Path(out.get("dir") or "/nowhere") / L.SERVER_EXE
    check(f"{label}: the server sits in the build folder and passes the checks",
          out.get("ok") is True and exe.is_file())
    sys.platform = platform
    try:
        check(f"{label}: read back as a {backend} build", L.engine_build_of(exe) == backend)
        check(f"{label}: found among the installed builds", L.installed_llama_tags() == [TAG])
    finally:
        sys.platform = real_platform

check("a Linux binary isn't taken for a broken Windows one", L._EXEC_MAGIC[1] == b"\x7fELF")
shutil.rmtree(home, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all engine platform checks passed")
