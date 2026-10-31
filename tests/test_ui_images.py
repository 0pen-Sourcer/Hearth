"""Image and video models in My Models, in the real page.

One list for every model on the PC: chat models and image or video models
side by side, filtered by kind. Not installed: the image part offers the
engine. Installed: the models the files on disk make ready, each with its
kind, size and Load, Unload or Delete; the loaded one marked; what's almost
ready and what isn't placed; the model folders and what each family needs.
There is no separate Images tab.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_images.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="hearth-images-")
os.environ["JARVIS_WORKSPACE"] = work
os.environ["HEARTH_WORKSPACE"] = work
from hearth import web  # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

own = os.path.join(work, "image-models")
STATUS = {"builtin_running": False, "external_running": False, "disk_models": [
    {"filename": "Qwen3.5-9B-Q4_K_M.gguf", "path": os.path.join(work, "models", "Qwen3.5-9B-Q4_K_M.gguf"),
     "source": "hearth", "size_gb": 5.6, "layers": 36},
    # The chat scan sees an image model's .gguf too; the image list shows it instead.
    {"filename": "z_image_turbo-Q4_K.gguf", "path": os.path.join(own, "diffusion_models", "z_image_turbo-Q4_K.gguf"),
     "source": "hearth", "size_gb": 3.4, "not_chat": True}]}
state = {"installed": False}
loads: list = []
deletes: list = []
identifies: list = []
OVERVIEW = {
    "installed": True, "build": "master-987-a77d362", "backend": "cuda",
    "roots": [own, os.path.join(work, "ComfyUI", "models")],
    "status": {"state": "ready", "recipe": "z-image-turbo", "error": None},
    "recipes": [
        {"id": "z-image-turbo", "name": "Z-Image Turbo", "video": False, "ready": True, "missing": [], "url": "",
         "size_gb": 6.9, "own": True, "has_model": True,
         "picks": {"diffusion_model": "diffusion_models/z_image_turbo-Q4_K.gguf", "vae": "vae/ae.safetensors"}},
        {"id": "sd-turbo", "name": "SD-Turbo", "video": False, "ready": True, "missing": [], "url": "",
         "size_gb": 2.1, "own": False, "has_model": True, "picks": {"model": "checkpoints/sd_turbo-f16-q8_0.gguf"}},
        {"id": "wan22-ti2v", "name": "Wan 2.2 TI2V 5B", "video": True, "ready": True, "missing": [], "url": "",
         "size_gb": 8.2, "own": True, "has_model": True,
         "picks": {"diffusion_model": "diffusion_models/wan2.2_ti2v_5B-Q4_K.gguf"}},
        {"id": "wan21-t2v", "name": "Wan 2.1 T2V", "video": True, "ready": False, "missing": ["T5-XXL"],
         "url": "https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B", "picks": {}, "has_model": False},
    ],
    "unnamed": [{"id": "checkpoints/ponyDiffusionV6XL.safetensors", "name": "ponyDiffusionV6XL.safetensors",
                 "category": "checkpoints"}],
    "unsure": [{"id": "checkpoints/odd.safetensors", "name": "odd.safetensors", "family": "Hunyuan Video", "note": ""}]}


def images(route):
    route.fulfill(json=OVERVIEW if state["installed"] else {"installed": False, "recipes": [], "roots": [own]})


def load(route):
    loads.append(json.loads(route.request.post_data or "{}"))
    route.fulfill(json={"ok": True, "recipe": "sd-turbo"})


def delete(route):
    deletes.append(json.loads(route.request.post_data or "{}"))
    route.fulfill(json={"ok": True, "freed_gb": 3.4})


def identify(route):
    identifies.append(json.loads(route.request.post_data or "{}"))
    route.fulfill(json={"ok": True, "recipe": "sdxl-checkpoint", "name": "SDXL", "family": "SDXL"})


PANE = """() => {
  const p = document.getElementById('models-pane-mine');
  const vis = (el) => !!el && el.offsetParent !== null;
  const rows = [...p.querySelectorAll('.model-row')].filter(r => vis(r) && !r.closest('#mine-folders'));   // folders aren't models
  return { shown: vis(p), text: p.innerText, html: p.innerHTML,
           rows: rows.map(r => r.querySelector('.model-name')?.innerText || ''),
           load: [...p.querySelectorAll('[data-img-load]')].filter(vis).map(b => b.dataset.imgLoad),
           unload: [...p.querySelectorAll('[data-img-unload]')].filter(vis).length,
           install: vis(p.querySelector('#img-install')),
           count: document.getElementById('tab-mine-n').textContent };
}"""

with sync_playwright() as pw:
    try:
        # Software rendering: a GPU-less session can't draw a frame, so clicks never settle.
        browser = pw.chromium.launch(channel="msedge", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    except Exception as e:
        print(f"skipped: no browser to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(0)
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.route("**/api/llmserver/status*", lambda r: r.fulfill(json=STATUS))
    pg.route("**/api/images", images)
    pg.route("**/api/images?*", images)
    pg.route("**/api/images/load", load)
    pg.route("**/api/images/delete", delete)
    pg.route("**/api/images/identify", identify)
    pg.route("**/api/llmserver/hf-search*", lambda r: r.fulfill(json={"results": []}))
    # Every folder models are read from, as My Models' Model folders lists them.
    pg.route("**/api/model-folders", lambda r: r.fulfill(json={
        "chat": os.path.join(work, "models"), "chat_also": [{"path": "C:\\x\\.lmstudio\\models", "from": "LM Studio"}],
        "images": [{"path": own, "own": True}, {"path": os.path.join(work, "ComfyUI", "models"), "own": False}],
        "forge": ""}))
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    time.sleep(3)
    pg.evaluate("showView('models')")
    pg.wait_for_timeout(1200)
    check("there is no separate Images tab or pane",
          pg.locator(".models-tab[data-tab=images]").count() == 0 and pg.locator("#models-pane-images").count() == 0)
    a = pg.evaluate(PANE)
    check("My Models opens with the chat model listed", a["shown"] and any("Qwen3.5-9B" in r for r in a["rows"]))
    check("without the engine My Models offers to install it", a["install"] and "isn't installed" in a["text"])

    state["installed"] = True
    pg.evaluate("modelsRenderImages(true)")
    pg.wait_for_timeout(800)
    b = pg.evaluate(PANE)
    check(f"image and video models are in My Models with their kind {b['rows']}",
          any("Z-Image Turbo" in r and "image" in r and "loaded" in r for r in b["rows"])
          and any("Wan 2.2 TI2V 5B" in r and "video" in r for r in b["rows"])
          and any("Qwen3.5-9B" in r for r in b["rows"]))
    check("an image model's .gguf isn't listed again as a chat model",
          not any(r.startswith("z_image_turbo-Q4_K.gguf") for r in b["rows"]))
    check("each image model shows its size", "6.9 GB" in b["text"] and "8.2 GB" in b["text"])
    check(f"the loaded one can be unloaded, the others loaded {b['load']}",
          b["unload"] == 1 and b["load"] == ["sd-turbo", "wan22-ti2v"])
    check(f"the My Models count has chat and image models ({b['count']})", b["count"] == "4")
    flat = b["text"].replace("\n", " ")
    pg.evaluate("modelsRenderFolders()")
    pg.wait_for_timeout(500)
    fold = pg.evaluate("document.getElementById('mine-folders')?.innerText || ''").replace("\n", " ")
    check("one Model folders section has the chat folder and the image ones, the person's own one too",
          "Chat models" in fold and os.path.join(work, "models") in fold and "ComfyUI" in fold and "Inglen's own" in fold)
    check("My Models no longer lists what else the engine can run", "What else it can run" not in b["html"]
          and "Wan 2.1 T2V" not in b["text"])
    pg.click(".models-tab[data-tab=discover]")
    pg.wait_for_timeout(1200)
    disc = pg.evaluate("document.getElementById('disc-images')?.innerText || ''")
    check("Discover lists every family the engine runs under Image & video",
          "Image & video" in disc and "Wan 2.1 T2V" in disc and "Z-Image Turbo" in disc)
    pg.click(".models-tab[data-tab=mine]")
    pg.wait_for_timeout(600)

    pg.click("[data-mine-kind=video]")
    pg.wait_for_timeout(300)
    v = pg.evaluate(PANE)
    check(f"Video shows the video models and no chat ones {v['rows']}",
          any("Wan 2.2" in r for r in v["rows"]) and not any("Z-Image" in r or "Qwen" in r for r in v["rows"]))
    pg.click("[data-mine-kind=chat]")
    pg.wait_for_timeout(300)
    c = pg.evaluate(PANE)
    check(f"Chat shows only chat models {c['rows']}", c["rows"] == [r for r in c["rows"] if "Qwen" in r] and c["rows"])
    pg.click("[data-mine-kind=all]")
    pg.wait_for_timeout(300)
    pg.screenshot(path=os.path.join(work, "my_models.png"), full_page=True)

    pg.click("[data-img-load=sd-turbo]")
    pg.wait_for_timeout(800)
    check(f"Load asks the image engine for that model {loads}", loads and loads[0].get("recipe") == "sd-turbo")
    check("a file named for no family is listed with a way to find out what it is",
          "ponyDiffusionV6XL.safetensors" in flat and "Not placed yet" in b["text"]
          and pg.locator("[data-img-identify]").count() == 1)
    check("one found to be a family nothing here runs says so", "Hunyuan Video, which nothing here runs" in flat)
    pg.click("[data-img-identify]")
    pg.wait_for_timeout(800)
    check(f"Find out asks the engine about that file {identifies}",
          identifies and identifies[0].get("file") == "checkpoints/ponyDiffusionV6XL.safetensors")

    pg.wait_for_timeout(500)
    pg.click(".model-row[data-row^=img-z-image] .model-name")
    pg.wait_for_timeout(300)
    check("only a model in Inglen's own folder can be deleted",
          pg.locator("[data-img-del]").count() == 2 and pg.locator("[data-img-del=sd-turbo]").count() == 0)
    pg.click("[data-img-del=z-image-turbo]")
    pg.wait_for_timeout(300)
    pg.get_by_role("button", name="Delete", exact=True).last.click()
    pg.wait_for_timeout(800)
    check(f"Delete asks the image engine to delete that model {deletes}",
          deletes and deletes[0].get("recipe") == "z-image-turbo")
    check(f"no script errors {errors[:2]}", not errors)
    print("screenshot:", os.path.join(work, "my_models.png"))
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all My Models image checks passed")
