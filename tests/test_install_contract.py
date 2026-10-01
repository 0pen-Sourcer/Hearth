"""Checks over the engine picker and the repair plan.

Both decide things the user never sees until they are wrong. The engine picker
chooses which llama.cpp build runs, and the repair plan promises which parts of
a broken install it can put back.

    .\\.venv\\Scripts\\python.exe tests\\test_install_contract.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------- engine build ordering
from hearth.llmserver import build_rank                             # noqa: E402

# llama.cpp build numbers crossed four digits into five. A string sort puts
# b9999 above b10488, which picks a stale engine and, worse, makes the pruner
# below keep the two oldest and delete the current one.
tags = ["b10068", "b10488", "b9999", "b9820"]
ordered = sorted(tags, key=build_rank, reverse=True)
check("a five-digit build outranks a four-digit one", ordered[0] == "b10488")
check("the whole ordering is numeric", ordered == ["b10488", "b10068", "b9999", "b9820"])
check("the pruner would keep the current builds",
      set(ordered[:2]) == {"b10488", "b10068"})
check("a tag that is not a build number sorts below every build",
      sorted(["b100", "nightly", "custom"], key=build_rank, reverse=True)[0] == "b100")
check("an empty tag does not raise", build_rank("") == (0, 0, ""))
check("case does not matter", build_rank("B123") == build_rank("b123"))

# ---------------------------------------------- repair promises match the patch
# The repair plan and the patch builder keep separate lists. If they drift, the
# plan either promises to restore something the patch never ships, or reports a
# part unfixable that it could have fixed.
from hearth import updater                                          # noqa: E402

_bp = open(os.path.join(ROOT, "build_patch.py"), encoding="utf-8").read()


def _list_from_patch(name):
    m = re.search(name + r"\s*=\s*\[(.*?)\]", _bp, re.S)
    return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()


shipped = _list_from_patch("_BINARY_INCLUDES") | _list_from_patch("_VENV_PURE_INCLUDES")
# Hearth's own files are checked against the packed list below; these are the
# bundled libraries taken from the build.
promised = {m for _, mods, _ in updater._REPAIRABLE for m in mods
            if "/" not in m and m != "hearth_cli.py"}
missing = sorted(promised - shipped)
check("every part repair promises is one the patch ships"
      + (", missing " + ", ".join(missing) if missing else ""), not missing)

plan_unpatchable = {m for _, mods, _ in updater._UNPATCHABLE for m in mods}
patch_unpatchable = _list_from_patch("_UNPATCHABLE")
check("the two unpatchable lists agree", plan_unpatchable == patch_unpatchable)
check("nothing is called both repairable and unpatchable",
      not (promised & plan_unpatchable))

# ------------------------------------------------ which engine build to fetch
# The repository's "latest" release once pointed at a v0.5.0 tag with no
# Windows files, so every engine install failed. And only CUDA builds were
# ever fetched, which on an AMD or Intel card run on the processor.
import io as _io                                                    # noqa: E402
import json as _json                                                # noqa: E402
import urllib.request as _ur                                        # noqa: E402
from hearth import llmserver as _ls, gpu as _gpu                    # noqa: E402

_rels = [{"tag_name": "v0.5.0", "assets": [{"name": "something.tar.gz"}]},
         {"tag_name": "b11307", "assets": [{"name": "llama-b11307-bin-win-vulkan-x64.zip"},
                                            {"name": "llama-b11307-bin-win-cuda-12.4-x64.zip"}]},
         {"tag_name": "b11306", "assets": [{"name": "llama-b11306-bin-win-cpu-x64.zip"}]}]
_real_open = _ur.urlopen
_ur.urlopen = lambda req, timeout=0: _io.BytesIO(_json.dumps(_rels).encode())
try:
    rel = _ls._latest_build_release()
finally:
    _ur.urlopen = _real_open
check("a release that isn't a numbered build is passed over", rel and rel["tag_name"] == "b11307")

_real_primary = _gpu.primary
for vendor, mb, want in (("NVIDIA", 8192, "cuda"), ("AMD", 16384, "vulkan"), ("Intel", 12288, "vulkan"),
                         ("Intel", 128, "cpu")):
    _gpu.primary = lambda v=vendor, m=mb: {"vendor": v, "mem_total_mb": m, "name": v}
    check(f"{vendor} with {mb} MB gets the {want} build", _ls.engine_backend() == want)
_gpu.primary = lambda: None
check("no graphics card gets the CPU build", _ls.engine_backend() == "cpu")

# An engine built for NVIDIA cards is not an engine on a PC without one: it is
# passed over, the person is told, and a repair fetches the build for the card.
import tempfile as _tf                                                # noqa: E402
from pathlib import Path as _Path                                     # noqa: E402
_eng = _Path(_tf.mkdtemp(prefix="hearth-engine-")) / "b99999"
_eng.mkdir(parents=True)
(_eng / "llama-server.exe").write_bytes(b"MZ" + b"0" * (_ls._MIN_SERVER_EXE_BYTES + 8))
(_eng / "ggml-cuda.dll").write_bytes(b"x")
check("an engine with CUDA libraries beside it is an NVIDIA build", _ls.engine_build_of(_eng / "llama-server.exe") == "cuda")
_gpu.primary = lambda: {"vendor": "AMD", "mem_total_mb": 8192, "name": "AMD"}
check("it doesn't fit a PC with an AMD card", not _ls.engine_fits(_eng / "llama-server.exe"))
_gpu.primary = lambda: {"vendor": "NVIDIA", "mem_total_mb": 8192, "name": "NVIDIA"}
check("it fits one with an NVIDIA card", _ls.engine_fits(_eng / "llama-server.exe"))
(_eng / "ggml-cuda.dll").unlink()
(_eng / "ggml-vulkan.dll").write_bytes(b"x")
_gpu.primary = lambda: {"vendor": "AMD", "mem_total_mb": 8192, "name": "AMD"}
check("a Vulkan build fits any card", _ls.engine_build_of(_eng / "llama-server.exe") == "vulkan"
      and _ls.engine_fits(_eng / "llama-server.exe"))
_real_home = os.environ.get("USERPROFILE"), os.environ.get("HOME")
_home = _tf.mkdtemp(prefix="hearth-home-")
os.environ["USERPROFILE"] = os.environ["HOME"] = _home
_mine = _Path(_home) / ".hearth" / "llamacpp" / "b99999"
_mine.mkdir(parents=True)
(_mine / "llama-server.exe").write_bytes(b"MZ" + b"0" * (_ls._MIN_SERVER_EXE_BYTES + 8))
(_mine / "ggml-cuda.dll").write_bytes(b"x")
_ls.reset_native_cache()
_real_pref = _ls.get_engine_preference
_ls.get_engine_preference = lambda: "hearth"
check("with an AMD card an NVIDIA-only engine on disk is passed over", _ls.find_native_llama_server() is None)
check("and the person is told which engine to get", _ls.runtime_advice()["nudge"]
      and "NVIDIA" in _ls.runtime_advice()["short"] and _ls.runtime_advice()["action"] == "install_runtime")
from hearth import updater as _up                                     # noqa: E402
check("a repair counts it as missing, so it fetches the one for this card", not _up._engine_ok() and _up._needs_engine())
_gpu.primary = lambda: {"vendor": "NVIDIA", "mem_total_mb": 8192, "name": "NVIDIA"}
_ls.reset_native_cache()
check("on an NVIDIA card the same engine is used", (_ls.find_native_llama_server() or {}).get("label") == "b99999")
_ls.get_engine_preference = _real_pref
for _k, _v in zip(("USERPROFILE", "HOME"), _real_home):
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v
_ls.reset_native_cache()
_gpu.primary = _real_primary

# ------------------------------------------- the patch carries Hearth's own files
# It used to pack only hearth/*.py and ui.html, so the skills, the helper
# agents' instructions and every icon and piece of art never updated and never
# came back after a repair, while the plan called the app intact.
import build_patch                                                  # noqa: E402

packed = {arc for _, arc in build_patch.code_members()}
for want in ("hearth/ui.html", "hearth_cli.py", "hearth/subagents/coder.md",
             "hearth/skills/make-pdf/SKILL.md", "assets/icon.png", "assets/mark.svg"):
    check(f"the patch packs {want}", want in packed)
if os.path.isdir(os.path.join(ROOT, "assets", "team")):
    check("the patch packs the Team room art", "assets/team/manifest.json" in packed)
check("no compiled or backup files are packed",
      not any(a.endswith((".pyc", ".bak")) or "__pycache__" in a for a in packed))
own = [m for _, mods, _ in updater._REPAIRABLE for m in mods if "/" in m or m == "hearth_cli.py"]
unpacked = [m for m in own if not any(a == m or a.startswith(m + "/") for a in packed)]
check("every file repair checks for is one the patch restores"
      + (", missing " + ", ".join(unpacked) if unpacked else ""), not unpacked)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all install contract checks passed")
