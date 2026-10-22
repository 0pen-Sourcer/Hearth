"""The local image engine picks a model it can actually load.

stable-diffusion.cpp ships a recipe per model family naming the files it needs
by slot (diffusion model, text encoders, VAE...). Hearth matches those against
the files in the engine's category folders: a recipe is ready only when every
required slot and each either-or group has a file, and the file chosen for a
slot is the one whose name fits the recipe, not whichever comes first.

    .\\.venv\\Scripts\\python.exe tests\\test_sdserver.py
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import sdserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def slot(sid, required, cats):
    return {"id": sid, "required": required, "categories": cats, "label": sid}


RECIPES = [
    {"id": "flux1-dev", "name": "FLUX.1 dev",
     "slots": [slot("diffusion_model", True, ["diffusion_models"]), slot("clip_l", True, ["text_encoders"]),
               slot("t5xxl", True, ["text_encoders"]), slot("vae", False, ["vae"]), slot("tae", False, ["vae", "vae_approx"])],
     "required_any": [["vae", "tae"]], "defaults": {"steps": 20}},
    {"id": "z-image-turbo", "name": "Z-Image Turbo",
     "slots": [slot("diffusion_model", True, ["diffusion_models"]), slot("llm", True, ["text_encoders"]),
               slot("vae", True, ["vae"])], "defaults": {"steps": 8}},
    {"id": "wan21-t2v", "name": "Wan 2.1 T2V",
     "slots": [slot("diffusion_model", True, ["diffusion_models"]), slot("t5xxl", True, ["text_encoders"]),
               slot("vae", True, ["vae"])], "defaults": {"video_frames": 33}},
    {"id": "sd-turbo", "name": "SD-Turbo", "slots": [slot("model", True, ["checkpoints"])], "defaults": {"steps": 1}},
]
FILES = [
    {"id": "diffusion_models/z_image_turbo-Q4_K.gguf", "name": "z_image_turbo-Q4_K.gguf", "category": "diffusion_models"},
    {"id": "diffusion_models/flux1-dev-Q4_0.gguf", "name": "flux1-dev-Q4_0.gguf", "category": "diffusion_models"},
    {"id": "text_encoders/qwen_3_4b.safetensors", "name": "qwen_3_4b.safetensors", "category": "text_encoders"},
    {"id": "vae/ae.safetensors", "name": "ae.safetensors", "category": "vae"},
]

rows = {r["id"]: r for r in sdserver.readiness(RECIPES, FILES)}
check("a recipe with every file is ready", rows["z-image-turbo"]["ready"])
check("one missing a required file is not, and says which",
      not rows["flux1-dev"]["ready"] and {"clip_l", "t5xxl"} & set(rows["flux1-dev"]["missing"]))
check("nothing on disk for a checkpoint recipe", rows["sd-turbo"]["missing"] == ["model"])
check("video recipes are told apart", rows["wan21-t2v"]["video"] and not rows["z-image-turbo"]["video"])

sd_family = RECIPES + [{"id": "sd1-checkpoint", "name": "SD 1.x checkpoint", "slots": [slot("model", True, ["checkpoints"])]},
                       {"id": "sdxl-turbo", "name": "SDXL Turbo", "slots": [slot("model", True, ["checkpoints"])]}]
turbo = [{"id": "checkpoints/sd_turbo-f16-q8_0.gguf", "name": "sd_turbo-f16-q8_0.gguf", "category": "checkpoints"}]
check("one SD-Turbo file makes SD-Turbo ready, not every recipe that says 'sd'",
      [r["id"] for r in sdserver.readiness(sd_family, turbo) if r["ready"]] == ["sd-turbo"])

real_api = sdserver.api
sdserver.api = lambda *a, **k: {"state": "empty", "recipe": None, "error": None}
check("the engine answering with no error counts as running", sdserver.running())
sdserver.api = lambda *a, **k: {"failed": True, "error": "the image engine isn't answering", "code": "unreachable"}
check("and not answering doesn't", not sdserver.running())


def loads_fine(method, path, body=None, timeout=30):
    if path == "/sdcpp/v1/models/status":
        return {"state": "empty", "recipe": None, "error": None}
    if path == "/sdcpp/v1/recipes":
        return {"recipes": sd_family, "errors": []}
    if path == "/sdcpp/v1/model-files":
        return {"files": turbo, "errors": []}
    if path == "/sdcpp/v1/models/load":
        return {"state": "ready", "recipe": "sd-turbo", "error": None}
    return {}


sdserver.api = loads_fine
real_start = sdserver.start
sdserver.start = lambda timeout=60: {"ok": True}
check("a load whose reply says ready (its error field empty) has loaded",
      sdserver.ensure_loaded(False).get("recipe") == "sd-turbo")
sdserver.api, sdserver.start = real_api, real_start

picks = sdserver.choose_files(rows["z-image-turbo"], RECIPES[1])
check("the diffusion file is the one named for the recipe, not the first in the folder",
      picks["diffusion_model"] == "diffusion_models/z_image_turbo-Q4_K.gguf")
check("every required slot gets a file", set(picks) == {"diffusion_model", "llm", "vae"})

flux_files = FILES + [{"id": "text_encoders/clip_l.safetensors", "name": "clip_l.safetensors", "category": "text_encoders"},
                      {"id": "text_encoders/t5xxl_fp8.safetensors", "name": "t5xxl_fp8.safetensors", "category": "text_encoders"}]
frow = {r["id"]: r for r in sdserver.readiness(RECIPES, flux_files)}["flux1-dev"]
fpick = sdserver.choose_files(frow, RECIPES[0])
check("an either-or group is filled once (VAE), the other left empty", "vae" in fpick and "tae" not in fpick)
check("each text encoder slot gets the file named for it",
      fpick["clip_l"].endswith("clip_l.safetensors") and fpick["t5xxl"].endswith("t5xxl_fp8.safetensors"))

# A whole generation against a stand-in engine: the job is polled and saved.
png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
calls = []


def fake_api(method, path, body=None, timeout=30):
    calls.append((method, path))
    if path == "/sdcpp/v1/models/status":
        return {"state": "ready", "recipe": "z-image-turbo"}
    if path == "/sdcpp/v1/recipes":
        return {"recipes": RECIPES}
    if path == "/sdcpp/v1/model-files":
        return {"files": FILES}
    if path == "/sdcpp/v1/img_gen":
        calls.append(("body", body))
        return {"id": "job_1", "status": "queued"}
    if path == "/sdcpp/v1/jobs/job_1":
        return {"status": "completed", "started": 1000, "completed": 13500,
                "result": {"output_format": "png", "images": [{"index": 0, "b64_json": png}]}}
    return {}


out_dir = tempfile.mkdtemp(prefix="hearth-gen-")
sdserver.api = fake_api
sdserver.start = lambda timeout=60: {"ok": True}
sdserver._out_dir = lambda: __import__("pathlib").Path(out_dir)
sdserver.time.sleep = lambda s: None
r = sdserver.generate("A red fox in the snow, watercolor")
check("a generation comes back as a saved file named from the prompt",
      r.get("ok") and os.path.basename(r["paths"][0]) == "a-red-fox-in-the-snow-watercolor.png"
      and open(r["paths"][0], "rb").read().startswith(b"\x89PNG"))
check("its time is reported", r.get("seconds") == 12.5)
check("no reload when the right kind of model is already loaded",
      not any(p == "/sdcpp/v1/models/load" for _m, p in calls if isinstance(p, str)))
v = sdserver.ensure_loaded(video=True)
check("asking for video with only an image model ready says what to add",
      not v["ok"] and "video" in v["error"])

check("an aspect ratio is sized at the model's own pixel count",
      sdserver.size_for("16:9", {"width": 512, "height": 512}) == (704, 384)
      and sdserver.size_for("1:1", {}) == (1024, 1024))

# The tools: on this PC when a model is set up, else the cloud; `where` decides when given.
from hearth import imagine, tools  # noqa: E402

made = []
sdserver.has_model = lambda video: True
sdserver.generate = lambda prompt, **k: made.append(("local", k.get("video", False))) or {
    "ok": True, "paths": [r["paths"][0]], "recipe": "z-image-turbo", "seconds": 9.0}
imagine.generate_image = lambda **k: made.append(("cloud", False)) or {
    "ok": True, "paths": [r["paths"][0]], "provider": "openai", "model": "gpt-image", "prompt": k["prompt"]}
out = tools._generate_image({"prompt": "a fox"})
check("with a local image model it's made on this PC, and shown in the chat",
      made == [("local", False)] and "__JARVIS_IMAGE__" in out and "on this PC" in out)
made.clear()
tools._generate_image({"prompt": "a fox", "where": "cloud"})
check("where=cloud goes to the cloud", made == [("cloud", False)])
made.clear()
sdserver.has_model = lambda video: False
tools._generate_image({"prompt": "a fox"})
check("without a local model it goes to the cloud", made == [("cloud", False)])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all image engine checks passed")
