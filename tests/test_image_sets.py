"""An image or video model downloads as a whole set from Discover.

The engine's recipe names the parts a family needs; the repackaged repo named
like it keeps each part in the engine's category folders. Each part the recipe
needs is matched to its file there, parts already on disk are left out, and a
family with no such repo says so instead of fetching half a model. Hugging
Face is played by stand-ins here.

    .\\.venv\\Scripts\\python.exe tests\\test_image_sets.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import sdserver as S, llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def sib(path, gb):
    return {"rfilename": path, "size": int(gb * 1e9)}


TREES = {
    "Comfy-Org/Wan-2.2": [sib("README.md", 0), sib("split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp16.safetensors", 28.6),
                          sib("split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors", 14.3),
                          sib("split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp16.safetensors", 28.6),
                          sib("split_files/diffusion_models/wan2.2_fun_camera_low_noise_14B_fp16.safetensors", 28.6),
                          sib("split_files/text_encoders/umt5_xxl_fp16.safetensors", 11.4),
                          sib("split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6.7),
                          sib("split_files/vae/wan2.2_vae.safetensors", 1.4),
                          sib("split_files/vae/wan_2.1_vae.safetensors", 0.25),
                          sib("split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", 1.2)],
    "Comfy-Org/Z-Image-Turbo": [sib("split_files/diffusion_models/z_image_turbo_bf16.safetensors", 12.3),
                                sib("split_files/diffusion_models/z_image_turbo_nvfp4.safetensors", 4.5),
                                sib("split_files/text_encoders/qwen_3_4b.safetensors", 8.0),
                                sib("split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors", 5.6),
                                sib("split_files/vae/ae.safetensors", 0.34)],
    "Comfy-Org/Z-Image": [sib("split_files/diffusion_models/z_image_bf16.safetensors", 12.3)],
    "Comfy-Org/Flux-1-Dev": [sib("split_files/diffusion_models/flux1-fill-dev.safetensors", 23.8)],
}
S._packaged_repos = lambda: list(TREES) + ["Comfy-Org/Qwen-Image"]
llmserver._repo_siblings = lambda repo: TREES.get(repo, [])


def slot(i, label, cats, req=True):
    return {"id": i, "label": label, "categories": cats, "category": cats[0], "required": req}


VAE = [slot("vae", "VAE", ["vae"], False), slot("tae", "TAE", ["vae", "vae_approx"], False)]
WAN = {"id": "wan22-t2v", "name": "Wan 2.2 T2V A14B", "required_any": [["vae", "tae"]],
       "slots": [slot("diffusion_model", "Low-noise diffusion model", ["diffusion_models"]),
                 slot("high_noise_diffusion_model", "High-noise diffusion model", ["diffusion_models"]),
                 slot("t5xxl", "UMT5-XXL", ["text_encoders"]),
                 slot("vae", "Wan 2.1 VAE", ["vae"], False), slot("tae", "TAE", ["vae", "vae_approx"], False)]}
ZT = {"id": "z-image-turbo", "name": "Z-Image Turbo", "required_any": [["vae", "tae"]],
      "slots": [slot("diffusion_model", "Diffusion model", ["diffusion_models"]),
                slot("llm", "Qwen 3 Text Encoder", ["text_encoders"])] + VAE}
SCHNELL = {"id": "flux1-schnell", "name": "FLUX.1 schnell", "model_url": "https://huggingface.co/black-forest-labs/FLUX.1-schnell",
           "required_any": [["vae", "tae"]],
           "slots": [slot("diffusion_model", "Diffusion model", ["diffusion_models"]),
                     slot("clip_l", "CLIP-L", ["text_encoders"]), slot("t5xxl", "T5", ["text_encoders"])] + VAE}

p = S.download_plan("wan22-t2v", WAN, roots=[])
by = {x["slot"]: x for x in p.get("parts", [])}
check(f"a whole set is found ({p.get('repo')}, {sorted(by)})", p.get("ok") and p["repo"] == "Comfy-Org/Wan-2.2"
      and set(by) == {"diffusion_model", "high_noise_diffusion_model", "t5xxl", "vae"})
check("low noise and high noise each get their own file",
      "low_noise" in by["diffusion_model"]["path"] and "high_noise" in by["high_noise_diffusion_model"]["path"]
      and "fun_camera" not in by["diffusion_model"]["path"])
check("the plain full-precision file, not a scaled or fp8 one",
      by["t5xxl"]["name"] == "umt5_xxl_fp16.safetensors" and by["high_noise_diffusion_model"]["name"].endswith("fp16.safetensors"))
check("the VAE the recipe names", by["vae"]["name"] == "wan_2.1_vae.safetensors")
check("each part says what it is", [by[k]["kind"] for k in ("diffusion_model", "t5xxl", "vae")] == ["Diffusion model", "Text encoder", "VAE"])
check("each goes to the engine's folder for it", by["t5xxl"]["category"] == "text_encoders" and by["vae"]["category"] == "vae")

p = S.download_plan("z-image-turbo", ZT, roots=[])
check(f"Z-Image Turbo comes from its own repo, not Z-Image's ({p.get('repo')})", p.get("repo") == "Comfy-Org/Z-Image-Turbo"
      and [x["name"] for x in p["parts"]] == ["z_image_turbo_bf16.safetensors", "qwen_3_4b.safetensors", "ae.safetensors"])

root = tempfile.mkdtemp(prefix="hearth-imgset-")
os.makedirs(os.path.join(root, "vae"))
open(os.path.join(root, "vae", "ae.safetensors"), "w").close()
p = S.download_plan("z-image-turbo", ZT, roots=[root])
have = [x["name"] for x in p["parts"] if x["have"]]
check(f"a part already in a model folder isn't fetched again ({have})", have == ["ae.safetensors"]
      and p["bytes"] == int(12.3e9) + int(8.0e9))
shutil.rmtree(root, ignore_errors=True)

p = S.download_plan("flux1-schnell", SCHNELL, roots=[])
check(f"another variant's file never stands in ({p.get('error')})", not p.get("ok") and not p.get("parts")
      and p.get("url", "").endswith("FLUX.1-schnell"))

check("a LoRA is said as one", S.part_kind("lora", "loras") == "LoRA")

# The downloader takes an image model's safetensors, only into a model folder.
d = tempfile.mkdtemp(prefix="hearth-dl-")
real = llmserver._download_with_resume
llmserver._download_with_resume = lambda url, dest, tmp, prog, cancel=None: (open(dest, "w").close(), 1)[1]
got = llmserver.download_from_hf_repo("Comfy-Org/Z-Image-Turbo", "split_files/vae/ae.safetensors",
                                      dest_dir=os.path.join(d, "vae"), save_as="ae.safetensors")
check(f"a set's safetensors part downloads into its folder ({got})", got.get("ok") and os.path.isfile(os.path.join(d, "vae", "ae.safetensors")))
got = llmserver.download_from_hf_repo("someone/repo", "model.safetensors")
check("a chat model download is still GGUF only", not got.get("ok"))
llmserver._download_with_resume = real
shutil.rmtree(d, ignore_errors=True)

print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
