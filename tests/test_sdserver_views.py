"""A Forge or older ComfyUI models folder works with the image engine.

The engine reads ComfyUI's folder names. Forge keeps diffusion-only models
(FLUX, Qwen Image) in Stable-diffusion beside its checkpoints and its text
encoders in text_encoder, so the engine filed a Qwen Image model as a
checkpoint and never saw the text encoder. Hearth gives such a folder a view of
hard links under .hearth-view/<category>/: no copy, the other app untouched.
With the engine installed, it is asked to list the view as well.

    .\\.venv\\Scripts\\python.exe tests\\test_sdserver_views.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import sdserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


work = tempfile.mkdtemp(prefix="hearth-forge-")
forge = os.path.join(work, "models")
for sub in ("Stable-diffusion", "text_encoder", "VAE", "Lora"):
    os.makedirs(os.path.join(forge, sub))


def put(rel, data=b"weights"):
    p = os.path.join(forge, rel)
    with open(p, "wb") as f:
        f.write(data)
    return p


model = put("Stable-diffusion/qwen-image-2.1-UC-Q4_K_M.gguf")
encoder = put("text_encoder/qwen3vl_8b.safetensors")
put("VAE/qwen_image_2.1_vae_bf16.safetensors")
put("Stable-diffusion/Put Checkpoint here.txt", b"note")

view = sdserver.folder_view(forge)
vm = os.path.join(forge, ".hearth-view", "diffusion_models", "Stable-diffusion", "qwen-image-2.1-UC-Q4_K_M.gguf")
ve = os.path.join(forge, ".hearth-view", "text_encoders", "text_encoder", "qwen3vl_8b.safetensors")
check("a Forge folder gets a view", view == os.path.join(forge, ".hearth-view"))
check("its Stable-diffusion models show as diffusion models, its text encoders as text encoders",
      os.path.isfile(vm) and os.path.isfile(ve))
check("as hard links to the same files, not copies",
      os.path.samefile(vm, model) and os.path.samefile(ve, encoder) and os.stat(model).st_nlink == 2)
check("notes beside the models are left out", not os.path.exists(vm.replace(
    "qwen-image-2.1-UC-Q4_K_M.gguf", "Put Checkpoint here.txt")))
check("folders the engine already reads (VAE) aren't linked", not os.path.isdir(os.path.join(view, "vae")))

os.remove(encoder)
sdserver.folder_view(forge)
check("a file removed from the folder leaves the view too", not os.path.exists(ve))

plain = os.path.join(work, "comfy")
os.makedirs(os.path.join(plain, "diffusion_models"))
check("a folder already in the engine's names gets no view", sdserver.folder_view(plain) is None
      and not os.path.exists(os.path.join(plain, ".hearth-view")))

# The real engine, when it's installed: the view's files are listed in their category.
builds = sdserver.installed()
if builds:
    import subprocess
    import json
    import urllib.request
    port = 18189
    proc = subprocess.Popen([str(builds[0]), "--listen-ip", "127.0.0.1", "--listen-port", str(port),
                             "--model-dir", os.path.join(work, "empty"), "--model-dir", forge, "--model-dir", view],
                            cwd=str(builds[0].parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=0x08000000 if sys.platform == "win32" else 0)
    os.makedirs(os.path.join(work, "empty"), exist_ok=True)
    files = None
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/sdcpp/v1/model-files", timeout=2) as r:
                    files = json.load(r)
                break
            except Exception:
                time.sleep(0.5)
    finally:
        proc.kill()
    cats = {(f["category"], os.path.basename(f["name"])) for f in (files or {}).get("files", [])}
    check(f"the engine lists the Qwen model as a diffusion model through the view {sorted(cats)}",
          ("diffusion_models", "qwen-image-2.1-UC-Q4_K_M.gguf") in cats)
    check(f"and doesn't refuse the links {(files or {}).get('errors')}", files is not None and not files.get("errors"))
else:
    print("skipped: the image engine isn't installed here")

shutil.rmtree(work, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all model folder view checks passed")
