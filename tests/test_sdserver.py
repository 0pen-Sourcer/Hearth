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

real_has_model = sdserver.has_model
made = []
sdserver.has_model = lambda video: True
sdserver.generate = lambda prompt, **k: made.append(("local", k.get("video", False))) or {
    "ok": True, "paths": [r["paths"][0]], "recipe": "z-image-turbo", "seconds": 9.0}
imagine.generate_image = lambda **k: made.append(("cloud", False)) or {
    "ok": True, "paths": [r["paths"][0]], "provider": "openai", "model": "gpt-image", "prompt": k["prompt"]}
out = tools._generate_image({"prompt": "a fox"})
check("with a local image model it's made on this PC, and shown in the chat",
      made == [("local", False)] and tools.media_in(out) and "__JARVIS" not in out and "on this PC" in out)
made.clear()
tools._generate_image({"prompt": "a fox", "where": "cloud"})
check("where=cloud goes to the cloud", made == [("cloud", False)])
made.clear()
sdserver.has_model = lambda video: False
tools._generate_image({"prompt": "a fox"})
check("without a local model it goes to the cloud", made == [("cloud", False)])

# A checkpoint named for no family ("ponyDiffusionV6XL...") is loaded once; the
# engine logs the family it read from the file's tensors, and the recipe with
# that name takes the file from then on.
from pathlib import Path  # noqa: E402

sdserver.has_model = real_has_model
sdserver.installed = lambda: [Path("sd-server")]
work = tempfile.mkdtemp(prefix="hearth-kinds-")
sdserver._kinds_path = lambda: Path(work) / "image_model_kinds.json"
log = Path(work) / "sdserver.log"
log.write_text("[I] earlier run\n[I] Version: SD 1.x  --- diffusion_engine.cpp:1017\n", encoding="utf-8")
sdserver._log_path = lambda: log

checkpoints = [
    {"id": "sd-turbo", "name": "SD-Turbo", "slots": [slot("model", True, ["checkpoints"])], "defaults": {"steps": 1}},
    {"id": "sdxl-turbo", "name": "SDXL Turbo", "slots": [slot("model", True, ["checkpoints"])], "defaults": {"steps": 1}},
    {"id": "sd1-checkpoint", "name": "SD 1.x", "slots": [slot("model", True, ["checkpoints"])], "defaults": {"width": 512}},
    {"id": "sdxl-checkpoint", "name": "SDXL", "slots": [slot("model", True, ["checkpoints"])], "defaults": {"width": 1024}},
]
pony = {"id": "checkpoints/ponyDiffusionV6XL_v6StartWithThisOne.safetensors",
        "name": "ponyDiffusionV6XL_v6StartWithThisOne.safetensors", "category": "checkpoints", "size": 6938040682}
check("the engine's own family names point at their recipes",
      sdserver.recipe_for_family("SDXL", checkpoints)["id"] == "sdxl-checkpoint"
      and sdserver.recipe_for_family("SD 1.x", checkpoints)["id"] == "sd1-checkpoint"
      and sdserver.recipe_for_family("Wan 2.x", RECIPES)["id"] == "wan21-t2v"
      and sdserver.recipe_for_family("Flux", RECIPES)["id"] == "flux1-dev"
      and sdserver.recipe_for_family("Hunyuan Video", checkpoints) is None)
check("a checkpoint named for no family makes nothing ready by itself",
      not any(r["ready"] for r in sdserver.readiness(checkpoints, [pony], {})))
check("and is listed as not placed yet", [f["id"] for f in sdserver.unnamed(checkpoints, [pony], {})] == [pony["id"]])

loads = []
state = {"recipe": None}


def engine(method, path, body=None, timeout=30):
    if path == "/sdcpp/v1/models/status":
        return {"state": "ready" if state["recipe"] else "empty", "recipe": state["recipe"], "error": None}
    if path == "/sdcpp/v1/recipes":
        return {"recipes": checkpoints}
    if path == "/sdcpp/v1/model-files":
        return {"files": [pony]}
    if path == "/sdcpp/v1/models/load":
        loads.append(body["recipe"])
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("[I] Version: SDXL  --- diffusion_engine.cpp:1017\n")
        state["recipe"] = body["recipe"]
        return {"state": "ready", "recipe": body["recipe"], "error": None}
    return {}


sdserver.api = engine
sdserver.start = lambda timeout=60: {"ok": True}
check("with only that file, a picture is still worth trying on this PC", sdserver.has_model(False))
got = sdserver.ensure_loaded(False)
check(f"asking for a picture finds out what it is and loads it as SDXL ({loads})",
      got.get("ok") and got.get("recipe") == "sdxl-checkpoint" and loads[-1] == "sdxl-checkpoint")
check("an older 'Version' line in the log isn't taken for this file's",
      sdserver.known_kinds()[pony["id"]]["family"] == "SDXL")
rows = {r["id"]: r for r in sdserver.catalog()["recipes"]}
check("from then on SDXL is ready with it, and no other recipe claims it",
      rows["sdxl-checkpoint"]["ready"] and not rows["sd1-checkpoint"]["ready"] and not rows["sd-turbo"]["ready"])
loads.clear()
sdserver.ensure_loaded(False)
check("and it isn't loaded a second time to be asked again", loads == [])

sd2 = [{"id": "sd2-inpainting", "name": "SD 2 Inpainting", "slots": [slot("model", True, ["checkpoints"])]},
       {"id": "sd2-512-checkpoint", "name": "SD 2.1 512", "slots": [slot("model", True, ["checkpoints"])]}]
check("for a file in checkpoints, the family's checkpoint recipe comes before its inpainting one",
      sdserver.recipe_for_family("SD 2.x", sd2, "checkpoints")["id"] == "sd2-512-checkpoint")

# A FLUX.1 file whose likeliest-looking recipe can't load it: the next one that does is kept.
flux_recipes = [
    {"id": "flux2-dev", "name": "FLUX.2 dev",
     "slots": [slot("diffusion_model", True, ["diffusion_models"]), slot("llm", True, ["text_encoders"])]},
    {"id": "flux1-dev", "name": "FLUX.1 dev",
     "slots": [slot("diffusion_model", True, ["diffusion_models"]), slot("clip_l", True, ["text_encoders"]),
               slot("t5xxl", True, ["text_encoders"])]},
]
dreamy = {"id": "diffusion_models/dreamy_v3.gguf", "name": "dreamy_v3.gguf", "category": "diffusion_models"}
flux_disk = [dreamy, {"id": "text_encoders/qwen3_4b.gguf", "name": "qwen3_4b.gguf", "category": "text_encoders"},
             {"id": "text_encoders/clip_l.safetensors", "name": "clip_l.safetensors", "category": "text_encoders"},
             {"id": "text_encoders/t5xxl_fp8.safetensors", "name": "t5xxl_fp8.safetensors", "category": "text_encoders"}]
loads.clear()
state["recipe"] = None


def flux_engine(method, path, body=None, timeout=30):
    if path == "/sdcpp/v1/recipes":
        return {"recipes": flux_recipes}
    if path == "/sdcpp/v1/model-files":
        return {"files": flux_disk}
    if path == "/sdcpp/v1/models/load":
        loads.append(body["recipe"])
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("[I] Version: Flux  --- diffusion_engine.cpp:1017\n")
        if body["recipe"] != "flux1-dev":
            state["recipe"] = None
            return {"failed": True, "error": "Model initialization failed", "http_status": 422}
        state["recipe"] = "flux1-dev"
        return {"state": "ready", "recipe": "flux1-dev", "error": None}
    return engine(method, path, body, timeout)


sdserver.api = flux_engine
found = sdserver.identify(dreamy["id"])
check(f"the recipe that fails to load it is passed over for the one that does ({loads})",
      found.get("recipe") == "flux1-dev" and found.get("loaded") and loads == ["flux2-dev", "flux1-dev"])


def unreadable(method, path, body=None, timeout=30):
    if path == "/sdcpp/v1/models/load":
        loads.append(body["recipe"])
        return {"failed": True, "error": "Model initialization failed", "http_status": 422}
    if path == "/sdcpp/v1/models/status":
        return {"state": "empty", "recipe": None, "error": None}
    return engine(method, path, body, timeout)


qslots = [slot("diffusion_model", True, ["diffusion_models"]), slot("llm", True, ["text_encoders"]),
          slot("vae", True, ["vae"])]
qwens = [{"id": "qwen-image", "name": "Qwen Image", "slots": qslots},
         {"id": "qwen-image-21", "name": "Qwen Image 2.1", "slots": qslots},
         {"id": "qwen-image-21-turbo", "name": "Qwen Image 2.1 Turbo", "slots": qslots}]
qfiles = [{"id": "@f/diffusion_models/qwen-image-2.1-UC-Q4_K_M.gguf", "name": "qwen-image-2.1-UC-Q4_K_M.gguf",
           "category": "diffusion_models"},
          {"id": "@f/text_encoders/qwen3vl_8b_w4a8_heretic.safetensors", "name": "qwen3vl_8b_w4a8_heretic.safetensors",
           "category": "text_encoders"},
          {"id": "@f/vae/qwen_image_2.1_vae_bf16.safetensors", "name": "qwen_image_2.1_vae_bf16.safetensors",
           "category": "vae"}]
check("a qwen-image-2.1 file is Qwen Image 2.1's, not 2.0's or the Turbo's",
      [r["id"] for r in sdserver.readiness(qwens, qfiles, {}) if r["ready"]] == ["qwen-image-21"])

# Downloads from Discover: Hugging Face's own pipeline tag says chat, image or
# video, and an image file goes to the folder of the recipe its name fits.
from hearth import llmserver  # noqa: E402

check("Hugging Face's pipeline tag says what a repo is",
      llmserver.model_kind({"pipeline_tag": "text-to-image"}) == "image"
      and llmserver.model_kind({"pipeline_tag": "image-to-video"}) == "video"
      and llmserver.model_kind({"pipeline_tag": "text-generation"}) == "chat"
      and llmserver.model_kind({"tags": ["gguf", "text-to-video"]}) == "video")
sdserver.api = lambda m, p, b=None, timeout=30: {"recipes": checkpoints + qwens} if p == "/sdcpp/v1/recipes" else {}
sdserver.running = lambda: True
check("a whole checkpoint goes to checkpoints, a diffusion model to diffusion_models, a VAE to vae",
      sdserver.folder_for("sdxl-turbo-q8.gguf") == "checkpoints"
      and sdserver.folder_for("qwen-image-2.1-UC-Q4_K_M.gguf") == "diffusion_models"
      and sdserver.folder_for("qwen_image_2.1_vae_bf16.safetensors") == "vae")

odd = {"id": "checkpoints/mystery.safetensors", "name": "mystery.safetensors", "category": "checkpoints"}
pony_files = [odd]
sdserver.api = lambda m, p, b=None, timeout=30: {"files": pony_files} if p == "/sdcpp/v1/model-files" else unreadable(m, p, b, timeout)
loads.clear()
miss = sdserver.ensure_loaded(False)
check("a file the engine can't read says there's no model, so the picture goes to the cloud",
      not miss["ok"] and miss.get("code") == "no_model")
tries = len(loads)
sdserver.ensure_loaded(False)
check(f"and it isn't tried again on the next picture ({tries} then {len(loads)})", tries > 0 and len(loads) == tries)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all image engine checks passed")
