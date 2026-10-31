"""An image or video model's diffusion file comes in smaller GGUF sizes.

GGUF builds are found by searching Hugging Face for the family, as the chat
side finds quants: a repo for another variant (Turbo, 2.1, an abliterated one)
never stands in, each of Wan's two experts gets its own file, and the size
picked is the largest that fits the card. Hugging Face and the card are played
by stand-ins here.

    .\\.venv\\Scripts\\python.exe tests\\test_image_quants.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401

from hearth import sdserver as S, llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def sib(path, gb):
    return {"rfilename": path, "size": int(gb * 1e9)}


SEARCH = {
    "Z-Image": ["unsloth/Z-Image-Turbo-GGUF", "someone/Z-Image-abliterated-GGUF", "unsloth/Z-Image-GGUF"],
    "Z-Image Turbo": ["someone/Z-Image-Turbo-abliterated-GGUF", "unsloth/Z-Image-Turbo-GGUF"],
    "Qwen Image": ["unsloth/Qwen-Image-2.1-GGUF", "unsloth/Qwen-Image-Edit-2511-GGUF"],
    "Wan 2.2 T2V A14B": ["QuantStack/Wan2.2-T2V-A14B-GGUF"],
}
TREES = {
    "unsloth/Z-Image-Turbo-GGUF": [sib("z-image-turbo-BF16.gguf", 12.3), sib("z-image-turbo-Q8_0.gguf", 7.2),
                                   sib("z-image-turbo-Q6_K.gguf", 5.4), sib("z-image-turbo-Q5_1.gguf", 5.5),
                                   sib("z-image-turbo-Q4_K_M.gguf", 4.6), sib("z-image-turbo-Q2_K.gguf", 3.0),
                                   sib("README.md", 0)],
    "unsloth/Z-Image-GGUF": [sib("z-image-Q8_0.gguf", 7.2), sib("z-image-Q4_K_M.gguf", 4.6)],
    "unsloth/Qwen-Image-2.1-GGUF": [sib("qwen-image-2.1-Q4_K_M.gguf", 4.2)],
    "QuantStack/Wan2.2-T2V-A14B-GGUF": [
        sib("HighNoise/Wan2.2-T2V-A14B-HighNoise-Q8_0.gguf", 15.4), sib("HighNoise/Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf", 9.7),
        sib("LowNoise/Wan2.2-T2V-A14B-LowNoise-Q8_0.gguf", 15.4), sib("LowNoise/Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf", 9.7),
        sib("VACE/Wan2.2-T2V-A14B-VACE-Q4_K_M.gguf", 9.9)],
}
llmserver.search_huggingface = lambda q, limit=12: [{"hf_repo": r} for r in SEARCH.get(q, [])]
llmserver._repo_siblings = lambda repo: TREES.get(repo, [])


def slot(i):
    return {"id": i, "required": True}


ZT = {"id": "z-image-turbo", "name": "Z-Image Turbo"}
q = S.quant_options(ZT, slot("diffusion_model"), [slot("diffusion_model"), slot("llm")], "z_image_turbo_bf16.safetensors")
check(f"Z-Image Turbo's sizes come from its own GGUF repo, not an abliterated one ({sorted({o['repo'] for o in q})})",
      q and {o["repo"] for o in q} == {"unsloth/Z-Image-Turbo-GGUF"})
check(f"largest first, with the quant named ({[o['quant'] for o in q]})",
      [o["quant"] for o in q] == ["Q8_0", "Q5_1", "Q6_K", "Q4_K_M", "Q2_K"])
check("a GGUF at full precision isn't offered beside the full file", all(o["quant"] != "BF16" for o in q))

q = S.quant_options({"id": "z-image", "name": "Z-Image"}, slot("diffusion_model"), [slot("diffusion_model")], "z_image_bf16.safetensors")
check(f"Z-Image isn't given Turbo's files ({sorted({o['repo'] for o in q})})", {o["repo"] for o in q} == {"unsloth/Z-Image-GGUF"})
q = S.quant_options({"id": "qwen-image", "name": "Qwen Image"}, slot("diffusion_model"), [slot("diffusion_model")], "qwen_image_bf16.safetensors")
check("Qwen Image isn't given Qwen Image 2.1 or Edit", q == [])

WAN = {"id": "wan22-t2v", "name": "Wan 2.2 T2V A14B"}
need = [slot("diffusion_model"), slot("high_noise_diffusion_model"), slot("t5xxl")]
low = S.quant_options(WAN, need[0], need, "wan2.2_t2v_low_noise_14B_fp16.safetensors")
high = S.quant_options(WAN, need[1], need, "wan2.2_t2v_high_noise_14B_fp16.safetensors")
check(f"each of Wan's experts gets its own sizes ({[o['name'] for o in low]}, {[o['name'] for o in high]})",
      low and high and all("LowNoise" in o["path"] for o in low) and all("HighNoise" in o["path"] for o in high))

# The size picked: the largest that fits an 8 GB card, or the one already here.
llmserver.detect_gpu_vram_gb = lambda: 8.0
llmserver.vram_for_a_new_model = lambda: 8.0
part = {"category": "diffusion_models", "repo": "Comfy-Org/Z-Image-Turbo", "path": "split_files/diffusion_models/z_image_turbo_bf16.safetensors",
        "name": "z_image_turbo_bf16.safetensors", "size": int(12.3e9)}
opts = S.quant_options(ZT, slot("diffusion_model"), [slot("diffusion_model")], part["name"])
part["options"] = sorted(opts + [dict(part, quant=S.quant_label(part["name"]))], key=lambda o: -o["size"])
part["options"] = [{k: o[k] for k in ("repo", "path", "name", "size", "quant")} for o in part["options"]]
p = dict(part, options=[dict(o) for o in part["options"]])
S._choose_size(p, [])
check(f"on an 8 GB card the largest quant that fits is picked ({p['quant']}, {p['size'] / 1e9:.1f} GB)",
      p["quant"] == "Q6_K" and p["name"] == "z-image-turbo-Q6_K.gguf" and p["repo"] == "unsloth/Z-Image-Turbo-GGUF")
check("each size says how it fits", {o["name"]: o["fit"] for o in p["options"]}["z-image-turbo-Q8_0.gguf"] == "partial"
      and {o["name"]: o["fit"] for o in p["options"]}["z-image-turbo-Q4_K_M.gguf"] == "good")
llmserver.detect_gpu_vram_gb = lambda: 24.0
llmserver.vram_for_a_new_model = lambda: 24.0
p = dict(part, options=[dict(o) for o in part["options"]])
S._choose_size(p, [])
check(f"on a 24 GB card the full file is ({p['quant']})", p["quant"] == "BF16" and p["repo"] == "Comfy-Org/Z-Image-Turbo")
root = tempfile.mkdtemp(prefix="hearth-quant-")
os.makedirs(os.path.join(root, "diffusion_models"))
open(os.path.join(root, "diffusion_models", "z-image-turbo-Q4_K_M.gguf"), "w").close()
p = dict(part, options=[dict(o) for o in part["options"]])
S._choose_size(p, [root])
check("a size already on this PC is the one picked, and needs nothing", p["quant"] == "Q4_K_M" and p["have"])
shutil.rmtree(root, ignore_errors=True)

# Once downloaded, each expert's file loads into its own slot.
files = [{"id": "diffusion_models/wan2.2_t2v_low_noise_14B_Q4_K_M.gguf", "name": "wan2.2_t2v_low_noise_14B_Q4_K_M.gguf", "category": "diffusion_models"},
         {"id": "diffusion_models/wan2.2_t2v_high_noise_14B_Q4.gguf", "name": "wan2.2_t2v_high_noise_14B_Q4.gguf", "category": "diffusion_models"}]
RECIPE = {"id": "wan22-t2v", "name": "Wan 2.2 T2V A14B",
          "slots": [{"id": "diffusion_model", "label": "Low-noise diffusion model", "categories": ["diffusion_models"], "required": True},
                    {"id": "high_noise_diffusion_model", "label": "High-noise diffusion model", "categories": ["diffusion_models"], "required": True}]}
a = S._assign(RECIPE, S._slots_of(RECIPE, files))
check(f"the high-noise file goes to the high-noise slot ({a['picks']})",
      "high_noise" in a["picks"].get("high_noise_diffusion_model", "") and "low_noise" in a["picks"].get("diffusion_model", ""))

check("quant names are read from file names", [S.quant_label(n) for n in
      ("x-Q4_K_M.gguf", "x-UD-Q5_K_M.gguf", "x-IQ4_NL.gguf", "x_bf16.safetensors", "x_fp8_scaled.safetensors", "x.safetensors")]
      == ["Q4_K_M", "UD-Q5_K_M", "IQ4_NL", "BF16", "FP8", "Full"])

print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
