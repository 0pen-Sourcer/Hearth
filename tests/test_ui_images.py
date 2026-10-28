"""The Models tab's Images & video pane, in the real page.

Not installed: it offers the engine. Installed: the models the files on disk
make ready, each with a Load button, the loaded one marked, the model folders,
and every other family the engine can run with what it still needs.

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

state = {"installed": False}
loads: list = []
OVERVIEW = {
    "installed": True, "build": "master-987-a77d362", "backend": "cuda",
    "roots": [os.path.join(work, "image-models"), os.path.join(work, "ComfyUI", "models")],
    "status": {"state": "ready", "recipe": "z-image-turbo", "error": None},
    "recipes": [
        {"id": "z-image-turbo", "name": "Z-Image Turbo", "video": False, "ready": True, "missing": [], "url": "",
         "picks": {"diffusion_model": "diffusion_models/z_image_turbo-Q4_K.gguf", "vae": "vae/ae.safetensors"}},
        {"id": "sd-turbo", "name": "SD-Turbo", "video": False, "ready": True, "missing": [], "url": "",
         "picks": {"model": "checkpoints/sd_turbo-f16-q8_0.gguf"}},
        {"id": "wan21-t2v", "name": "Wan 2.1 T2V", "video": True, "ready": False, "missing": ["T5-XXL"],
         "url": "https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B", "picks": {}},
    ],
    "unnamed": [{"id": "checkpoints/ponyDiffusionV6XL.safetensors", "name": "ponyDiffusionV6XL.safetensors",
                 "category": "checkpoints"}],
    "unsure": [{"id": "checkpoints/odd.safetensors", "name": "odd.safetensors", "family": "Hunyuan Video", "note": ""}]}
identifies: list = []


def identify(route):
    identifies.append(json.loads(route.request.post_data or "{}"))
    route.fulfill(json={"ok": True, "recipe": "sdxl-checkpoint", "name": "SDXL", "family": "SDXL"})


def images(route):
    route.fulfill(json=OVERVIEW if state["installed"] else {"installed": False, "recipes": [], "roots": []})


def load(route):
    loads.append(json.loads(route.request.post_data or "{}"))
    route.fulfill(json={"ok": True, "recipe": "sd-turbo"})


PANE = """() => {
  const p = document.getElementById('models-pane-images');
  return { shown: !p.hidden, text: p.innerText,
           ready: [...p.querySelectorAll('.model-row')].map(r => r.querySelector('.model-name')?.innerText || ''),
           load: [...p.querySelectorAll('[data-img-load]')].map(b => b.dataset.imgLoad),
           unload: p.querySelectorAll('[data-img-unload]').length,
           install: !!p.querySelector('#img-install') };
}"""

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skipped: no browser to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.route("**/api/images", images)
    pg.route("**/api/images?*", images)
    pg.route("**/api/images/load", load)
    pg.route("**/api/images/identify", identify)
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
    pg.click(".models-tab[data-tab=images]")
    pg.wait_for_timeout(600)
    a = pg.evaluate(PANE)
    check("the pane opens from its tab", a["shown"])
    check("without the engine it offers to install it", a["install"] and "not installed" in a["text"])

    state["installed"] = True
    pg.evaluate("modelsRenderImages()")
    pg.wait_for_timeout(600)
    b = pg.evaluate(PANE)
    check(f"ready models are listed, the loaded one marked {b['ready'][:3]}",
          any("Z-Image Turbo" in r and "loaded" in r for r in b["ready"]) and any("SD-Turbo" in r for r in b["ready"]))
    check("the loaded one can be unloaded, the other loaded", b["unload"] == 1 and b["load"] == ["sd-turbo"])
    check("the folders are shown, the person's own one too", "ComfyUI" in b["text"] and "Inglen's own" in b["text"])
    check("a family still missing a file says which", "needs T5-XXL" in b["text"].replace("\n", " ") or
          "needs T5-XXL" in pg.evaluate("document.getElementById('models-pane-images').innerHTML"))
    pg.screenshot(path=os.path.join(work, "images_pane.png"))
    pg.click("[data-img-load=sd-turbo]")
    pg.wait_for_timeout(800)
    check(f"Load asks for that model {loads}", loads and loads[0].get("recipe") == "sd-turbo")
    flat = b["text"].replace("\n", " ")
    check("a file named for no family is listed with a way to find out what it is",
          "ponyDiffusionV6XL.safetensors" in flat and "Not placed yet" in b["text"]
          and pg.locator("[data-img-identify]").count() == 1)
    check("one found to be a family nothing here runs says so", "Hunyuan Video, which nothing here runs" in flat)
    pg.click("[data-img-identify]")
    pg.wait_for_timeout(800)
    check(f"Find out asks the engine about that file {identifies}",
          identifies and identifies[0].get("file") == "checkpoints/ponyDiffusionV6XL.safetensors")
    check(f"no script errors {errors[:2]}", not errors)
    print("screenshot:", os.path.join(work, "images_pane.png"))
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all images pane checks passed")
