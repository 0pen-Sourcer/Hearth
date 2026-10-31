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
    pg.route("**/api/llmserver/hf-search*", lambda r: r.fulfill(json={"results": [
        {"hf_repo": "unsloth/Qwen3.5-9B-GGUF", "downloads": 900000, "likes": 4, "tags": ["gguf"], "params_b": 9}]}))
    # Discover's image and video families: one with a whole download, one without.
    pg.route("**/api/images/sets", lambda r: r.fulfill(json={"installed": True, "ready": True, "sets": [
        {"id": "z-image-turbo", "name": "Z-Image Turbo", "video": False, "blurb": "Makes pictures from text, in a few steps",
         "url": "", "ok": True, "repo": "Comfy-Org/Z-Image-Turbo", "downloads": 400000, "bytes": 8_000_000_000, "parts": [
             {"slot": "diffusion_model", "kind": "Diffusion model", "label": "Diffusion model", "repo": "unsloth/Z-Image-Turbo-GGUF",
              "path": "z-image-turbo-Q6_K.gguf", "category": "diffusion_models", "name": "z-image-turbo-Q6_K.gguf",
              "size": 6_000_000_000, "have": False, "quant": "Q6_K", "pick": 2, "options": [
                  {"repo": "Comfy-Org/Z-Image-Turbo", "path": "split_files/diffusion_models/z_image_turbo_bf16.safetensors",
                   "name": "z_image_turbo_bf16.safetensors", "size": 12_300_000_000, "quant": "BF16", "fit": "partial", "have": False},
                  {"repo": "unsloth/Z-Image-Turbo-GGUF", "path": "z-image-turbo-Q8_0.gguf", "name": "z-image-turbo-Q8_0.gguf",
                   "size": 7_200_000_000, "quant": "Q8_0", "fit": "partial", "have": False},
                  {"repo": "unsloth/Z-Image-Turbo-GGUF", "path": "z-image-turbo-Q6_K.gguf", "name": "z-image-turbo-Q6_K.gguf",
                   "size": 6_000_000_000, "quant": "Q6_K", "fit": "good", "have": False},
                  {"repo": "unsloth/Z-Image-Turbo-GGUF", "path": "z-image-turbo-Q4_K_M.gguf", "name": "z-image-turbo-Q4_K_M.gguf",
                   "size": 4_600_000_000, "quant": "Q4_K_M", "fit": "good", "have": False}]},
             {"slot": "vae", "kind": "VAE", "label": "VAE", "repo": "Comfy-Org/Z-Image-Turbo", "path": "split_files/vae/ae.safetensors",
              "category": "vae", "name": "ae.safetensors", "size": 300_000_000, "have": True},
             {"slot": "llm", "kind": "Text encoder", "label": "Qwen 3", "repo": "Comfy-Org/Z-Image-Turbo",
              "path": "split_files/text_encoders/q.safetensors", "category": "text_encoders", "name": "q.safetensors",
              "size": 2_000_000_000, "have": False}]},
        # A huge set from a repackaged repo shared by two families, its downloads counting both.
        *[{"id": f"minimax-{v}", "name": f"MiniMax H3 {v}", "video": True, "blurb": "Brings a picture to life as a short video",
           "url": "", "ok": True, "repo": "Comfy-Org/MiniMax-H3", "downloads": 29_000_000, "bytes": 123_000_000_000, "parts": [
               {"slot": "diffusion_model", "kind": "Diffusion model", "label": "Diffusion model", "repo": "Comfy-Org/MiniMax-H3",
                "path": f"split_files/diffusion_models/m_{v}.safetensors", "category": "diffusion_models", "name": f"m_{v}.safetensors",
                "size": 123_000_000_000, "have": False}]} for v in ("FL2VA", "Ref2VA")],
        {"id": "wan21-t2v", "name": "Wan 2.1 T2V", "video": True, "blurb": "Makes short videos from text", "url": "",
         "ok": False, "repo": "", "downloads": 0, "parts": []}]}))
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
    disc = pg.evaluate("""() => ({ rows: [...document.querySelectorAll('#models-pane-discover .model-row')].map(r => r.querySelector('.model-name').innerText),
        sections: document.querySelectorAll('#models-pane-discover .models-section').length,
        text: document.getElementById('models-pane-discover').innerText })""")
    check(f"chat and image models are one list in Discover {disc['rows']}",
          disc["sections"] == 1 and disc["rows"][0].startswith("unsloth/Qwen3.5") and disc["rows"][1].startswith("Z-Image Turbo"))
    check("a set far past this PC goes last, whatever its repo's downloads",
          len(disc["rows"]) == 4 and all(r.startswith("MiniMax H3") for r in disc["rows"][2:]))
    check("a repo's downloads shared by several families aren't shown as one family's",
          "29,000,000" not in disc["text"] and "2,90,00,000" not in disc["text"] and "400,000 downloads" in disc["text"].replace("4,00,000", "400,000"))
    check("a family with no whole download isn't in the list, only counted at the end",
          not any(r.startswith("Wan 2.1 T2V") for r in disc["rows"]) and "1 more the image engine can run" in disc["text"])
    check("the engine's own notes aren't the description", "Makes pictures from text, in a few steps" in disc["text"])
    pg.click(".model-row[data-img-recipe=z-image-turbo]")
    pg.wait_for_timeout(400)
    pick = pg.evaluate("""() => { const r = document.querySelector('.model-row[data-img-recipe=z-image-turbo]');
        return { on: [...r.querySelectorAll('.img-pick .mp-row')].map(b => b.classList.contains('cur')),
                 btn: r.querySelector('[data-img-get]').textContent }; }""")
    check(f"each part has a tick, off for the one on this PC, and the button totals what's ticked {pick}",
          pick["on"] == [True, False, True] and pick["btn"] == "Download 2 parts · 8.0 GB")
    pg.click(".model-row[data-img-recipe=z-image-turbo] .img-pick .mp-row[data-i='2']")
    pg.wait_for_timeout(200)
    btn = pg.evaluate("document.querySelector('[data-img-get=z-image-turbo]').textContent")
    check(f"a part can be left out ({btn})", btn == "Download 1 part · 6.0 GB")
    sizes = pg.evaluate("""() => [...document.querySelectorAll('.model-row[data-img-recipe=z-image-turbo] .img-sizes .ex-file')]
        .map(f => f.querySelector('.ex-quant').textContent + (f.classList.contains('selected') ? '*' : ''))""")
    check(f"the diffusion model offers its sizes, the one that fits this card picked {sizes}",
          sizes == ["BF16", "Q8_0", "Q6_K*", "Q4_K_M"])
    pg.click(".model-row[data-img-recipe=z-image-turbo] .img-sizes .ex-file[data-o='3']")
    pg.wait_for_timeout(200)
    got = pg.evaluate("""() => { const r = document.querySelector('.model-row[data-img-recipe=z-image-turbo]');
        return { btn: r.querySelector('[data-img-get]').textContent, name: r.querySelector('.img-pick .mp-row[data-i="0"] .mp-s').textContent,
                 part: _modelsState.imgSets.sets[0].parts[0].path }; }""")
    check(f"a smaller size changes the part and the total {got}",
          got["btn"] == "Download 1 part · 4.6 GB" and got["name"] == "z-image-turbo-Q4_K_M.gguf" and got["part"] == "z-image-turbo-Q4_K_M.gguf")
    pg.locator(".model-row[data-img-recipe=z-image-turbo]").scroll_into_view_if_needed()
    pg.screenshot(path=os.path.join(work, "sizes.png"))
    print("screenshot:", os.path.join(work, "sizes.png"))
    pg.evaluate("_modelsState.hfQuery = 'wan'; modelsRenderDiscover()")
    pg.wait_for_timeout(1000)
    found = pg.evaluate("document.getElementById('models-pane-discover').innerText")
    check("searching finds one without a whole download, and says so", "Wan 2.1 T2V" in found and "Not available as one download" in pg.evaluate("document.getElementById('models-pane-discover').innerHTML"))
    pg.evaluate("_modelsState.hfQuery = ''")
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
    fold = pg.evaluate("""() => { const d = document.querySelector('#mine-images details.img-extra');
        return { shut: d && !d.open, says: d ? d.querySelector('summary').innerText : '' }; }""")
    check(f"the rest folds into one quiet line, so images don't outgrow the chat list {fold}",
          fold["shut"] and fold["says"] == "2 not placed yet")
    pg.evaluate("document.querySelector('#mine-images details.img-extra').open = true")
    pg.wait_for_timeout(300)
    flat = pg.evaluate("document.getElementById('models-pane-mine').innerText").replace("\\n", " ")
    check("a file named for no family is listed with a way to find out what it is",
          "ponyDiffusionV6XL.safetensors" in flat and "not placed yet" in flat
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
    # Engine: the image engine beside the chat one; a GitHub limit is named as one.
    pg.route("**/api/runtime/builds", lambda r: r.fulfill(json={"ok": False, "builds": [], "limited": True,
                                                                "stale": False, "error": "HTTP 403"}))
    pg.route("**/api/images/builds", lambda r: r.fulfill(json={"ok": True, "stale": False, "limited": False, "error": "",
        "current": {"tag": "master-987-a77d362", "backend": "cuda", "path": "x", "running": True, "pinned": ""},
        "builds": [{"tag": "master-990-b", "backend": "cuda", "size_mb": 712, "published": "", "url": "", "installed": False, "current": False},
                   {"tag": "master-987-a77d362", "backend": "cuda", "size_mb": 712, "published": "", "url": "", "installed": True, "current": True}]}))
    pg.click(".models-tab[data-tab=engine]")
    try:
        pg.wait_for_function("(document.getElementById('models-pane-engine').innerText || '').includes('Image and video engine')", timeout=30000)
    except Exception:
        pass
    eng = pg.evaluate("document.getElementById('models-pane-engine').innerText")
    check("Engine shows the image engine, its build and what it runs on",
          "Image and video engine" in eng and "master-987-a77d362" in eng and "cuda" in eng and "running" in eng)
    check("with an update to the newest build", "Update to master-990-b" in eng)
    check("a GitHub limit is said as one, not as no connection",
          "limiting requests from this network" in eng and "Couldn't reach GitHub" not in eng)
    check(f"no script errors {errors[:2]}", not errors)
    print("screenshot:", os.path.join(work, "my_models.png"))
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all My Models image checks passed")
