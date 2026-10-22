"""Hearth's own engine for images and video: stable-diffusion.cpp's sd-server.

Run the way the chat engine is: a prebuilt release downloaded into
~/.hearth/sdcpp/<tag>/, started on its own local port, stopped with Hearth.
Model files live in ~/.hearth/image-models/ in the engine's category folders
(checkpoints, diffusion_models, text_encoders, vae, loras, ...), plus any folders
the person adds (an existing ComfyUI or WebUI models folder works as is).

What a model family needs is the engine's own business: it ships a recipe per
family (SD 1.x to 3.5, SDXL, FLUX, Qwen Image, Z-Image, Wan, LTX, HunyuanVideo
and more) naming the files each needs. Hearth reads those recipes, matches them
against the files on disk, and loads one; it keeps no family table of its own.
The engine places weights on the card, in RAM or on disk by what is free, so it
can sit beside a loaded chat model (slower when both don't fit).
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO = "leejet/stable-diffusion.cpp"
SERVER = "sd-server.exe" if sys.platform == "win32" else "sd-server"
PORT = int(os.environ.get("HEARTH_SD_PORT") or 8189)
CATEGORIES = ("checkpoints", "diffusion_models", "text_encoders", "vae", "loras", "clip_vision",
              "controlnet", "recipes")

_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()
_cancel = threading.Event()


def _home() -> Path:
    return Path(os.path.expanduser("~/.hearth"))


def builds_dir() -> Path:
    return _home() / "sdcpp"


def models_dir() -> Path:
    """Hearth's own model folder, with the engine's category folders in it."""
    d = _home() / "image-models"
    for c in CATEGORIES:
        (d / c).mkdir(parents=True, exist_ok=True)
    return d


def model_roots() -> List[str]:
    """Hearth's folder first, then the person's own (settings image_model_dirs),
    each followed by its view (below) when it has one."""
    roots = [str(models_dir())]
    try:
        from .web import _load_settings
        s = _load_settings()
        extra = s.get("image_model_dirs") or []
        views = s.get("image_folder_views", True)
    except Exception:
        extra, views = [], True
    for d in extra if isinstance(extra, list) else []:
        d = os.path.expanduser(str(d))
        if os.path.isdir(d) and d not in roots:
            roots.append(d)
            v = folder_view(d) if views else None
            if v:
                roots.append(v)
    return roots


# The engine reads ComfyUI's folder names. Forge keeps diffusion-only models
# (FLUX, Qwen Image, Z-Image) beside its checkpoints in Stable-diffusion and its
# text encoders in text_encoder; older ComfyUI uses unet and clip. A folder like
# that gets a view the engine can read: <folder>/.hearth-view/<category>/, made
# of hard links to the files (no copy, no space, the other app keeps working).
# Hard links stay on one drive, which the view always is.
_OTHER_NAMES = {"stable-diffusion": "diffusion_models", "unet": "diffusion_models",
                "text_encoder": "text_encoders", "clip": "text_encoders"}
_MODEL_EXT = (".gguf", ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".sft")
VIEW = ".hearth-view"


def folder_view(root: str) -> Optional[str]:
    """Bring `root`'s view up to date; its path, or None when nothing needs one."""
    try:
        subs = [s for s in os.listdir(root) if s.lower() in _OTHER_NAMES and os.path.isdir(os.path.join(root, s))]
    except OSError:
        return None
    view = os.path.join(root, VIEW)
    if not subs and not os.path.isdir(view):
        return None
    wanted = set()
    for sub in subs:
        src_dir = os.path.join(root, sub)
        for dirpath, _dirs, names in os.walk(src_dir):
            for n in names:
                if not n.lower().endswith(_MODEL_EXT):
                    continue
                src = os.path.join(dirpath, n)
                dst = os.path.join(view, _OTHER_NAMES[sub.lower()], sub, os.path.relpath(src, src_dir))
                wanted.add(os.path.normcase(dst))
                try:
                    if os.path.exists(dst) and os.path.samefile(src, dst):
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.link(src, dst)
                except OSError:
                    continue            # a drive without hard links: the file stays where the engine can't see it
    for dirpath, _dirs, names in os.walk(view, topdown=False):   # links whose file is gone or moved
        for n in names:
            p = os.path.join(dirpath, n)
            if os.path.normcase(p) not in wanted:
                try:
                    os.remove(p)
                except OSError:
                    pass
        try:
            if dirpath != view and not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass
    return view if wanted else None


# ---------------------------------------------------------------- the engine build

def installed() -> List[Path]:
    """Usable sd-server builds on disk, newest first."""
    from .llmserver import _usable_server_exe
    root = builds_dir()
    if not root.is_dir():
        return []
    found = [p for p in root.glob(f"*/{SERVER}") if _usable_server_exe(p)]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _backend() -> str:
    from .llmserver import engine_backend
    return engine_backend()


def _latest_release(timeout: float = 20) -> Optional[Dict[str, Any]]:
    from .release_files import for_this_system
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=10",
                                 headers={"User-Agent": "Hearth", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rels = json.load(r)
    return next((rel for rel in rels if isinstance(rels, list) and for_this_system(rel.get("assets"))), None)


def install(on_progress: Optional[Callable[[int, int], None]] = None, force: bool = False) -> Dict[str, Any]:
    """Fetch the newest build for this system and card. NVIDIA on Linux, where
    no CUDA build is published, gets the Vulkan one, which its driver runs."""
    from . import llmserver as L
    from .release_files import for_this_system, pick
    _cancel.clear()
    try:
        rel = _latest_release()
    except Exception as e:
        return {"ok": False, "error": f"couldn't reach GitHub releases: {e}"}
    if not rel:
        return {"ok": False, "error": "no image engine build for this system among the recent releases"}
    tag = rel.get("tag_name") or "latest"
    assets = rel.get("assets") or []
    backend = _backend()
    bins, runtime, _cuda = pick(assets, backend)
    if not bins and backend == "cuda":
        backend = "vulkan"
        bins, runtime, _cuda = pick(assets, backend)
    if not bins and backend != "cpu":
        backend = "cpu"
        bins, runtime, _cuda = pick(assets, backend)
    if not bins:
        have = sorted({p["backend"] for _a, p in for_this_system(assets)})
        return {"ok": False, "error": f"no build for this system in {tag} (there are: {', '.join(have) or 'none'})"}
    dest = builds_dir() / tag
    exe = dest / SERVER
    if not force and L._usable_server_exe(exe) and L.engine_build_of(exe) == backend:
        return {"ok": True, "dir": str(dest), "tag": tag, "backend": backend, "already": True}
    if dest.is_dir():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    cache = builds_dir() / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    total = int(bins.get("size") or 0) + int((runtime or {}).get("size") or 0)
    done = [0]
    try:
        for asset in [a for a in (bins, runtime) if a]:
            name = asset["name"]
            path, part = cache / name, cache / (name + ".part")
            base = done[0]
            if path.exists() and not L._archive_ok(path):
                path.unlink(missing_ok=True)
            if not path.exists():
                L._download_with_resume(asset["browser_download_url"], path, part,
                                        (lambda d, t, b=base: on_progress and on_progress(b + d, total)),
                                        ua="Hearth", cancel=_cancel.is_set)
            done[0] = base + int(asset.get("size") or 0)
            L._extract_archive(path, dest)
            path.unlink(missing_ok=True)
    except L.DownloadCancelled:
        return {"ok": False, "cancelled": True, "error": "download cancelled (progress kept, it will resume)"}
    except Exception as e:
        return {"ok": False, "error": f"download/extract failed: {type(e).__name__}: {e}"}
    if sys.platform != "win32":
        for f in dest.iterdir():
            if f.is_file() and (f.name == SERVER or f.name.startswith("sd-")):
                os.chmod(f, f.stat().st_mode | 0o755)
    if not L._usable_server_exe(exe):
        size = exe.stat().st_size if exe.exists() else 0
        return {"ok": False, "stub": True, "dir": str(dest), "tag": tag,
                "error": (f"{SERVER} is unusable after extract ({size} bytes). Antivirus quarantine is the "
                          f"usual cause: allow {builds_dir()} in it and install again.")}
    for old in sorted((d for d in builds_dir().iterdir() if d.is_dir() and not d.name.startswith(".") and d != dest),
                      key=lambda p: p.stat().st_mtime, reverse=True)[1:]:
        shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "dir": str(dest), "tag": tag, "backend": backend}


def cancel_install() -> Dict[str, Any]:
    _cancel.set()
    return {"ok": True}


# ---------------------------------------------------------------- running it

def base_url() -> str:
    return f"http://127.0.0.1:{PORT}"


def api(method: str, path: str, body: Optional[dict] = None, timeout: float = 30) -> Dict[str, Any]:
    """Call the engine. A failed call comes back marked "failed" (with error,
    code and the HTTP status) rather than raising. Successful replies can carry
    an "error" field of their own (null), so callers test "failed", never "error"."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read() or b"{}")
        except ValueError:
            out = {}
        if not isinstance(out, dict):
            out = {"error": str(out)}
        if not out.get("error"):
            out["error"] = f"HTTP {e.code}"
        out.update(failed=True, http_status=e.code)
        return out
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"failed": True, "error": f"the image engine isn't answering ({type(e).__name__})",
                "code": "unreachable"}


def _log_path() -> Path:
    try:
        from .tools import WORKSPACE
        logs = Path(WORKSPACE) / "logs"
    except Exception:
        logs = _home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "sdserver.log"


def running() -> bool:
    st = api("GET", "/sdcpp/v1/models/status", timeout=2)
    return not st.get("failed") and "state" in st


def start(timeout: float = 60) -> Dict[str, Any]:
    """Start the engine if it isn't answering already."""
    global _proc
    with _lock:
        if running():
            return {"ok": True, "already": True}
        builds = installed()
        if not builds:
            return {"ok": False, "error": "the image engine isn't installed", "code": "not_installed"}
        from . import llmserver as L
        exe = builds[0]
        cmd = [str(exe), "--listen-ip", "127.0.0.1", "--listen-port", str(PORT)]
        for root in model_roots():
            cmd += ["--model-dir", root]
        log = open(_log_path(), "ab")
        env = L._with_libs(dict(os.environ), [str(exe.parent)])
        _proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(exe.parent), env=env,
                                 creationflags=0x08000000 if sys.platform == "win32" else 0)
        L._assign_to_job(_proc)
    end = time.time() + timeout
    while time.time() < end:
        if _proc.poll() is not None:
            return {"ok": False, "error": f"the image engine stopped as it started (code {_proc.returncode}); "
                                          f"its log is logs/sdserver.log"}
        if running():
            return {"ok": True}
        time.sleep(0.5)
    return {"ok": False, "error": "the image engine didn't answer in time"}


def stop() -> Dict[str, Any]:
    global _proc
    with _lock:
        p, _proc = _proc, None
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    return {"ok": True}


# ---------------------------------------------------------------- models

def _words(s: str) -> set:
    # A version stays one word: "qwen-image-2.1" and "Qwen Image 2.1" both give "21".
    s = re.sub(r"(\d)[._](?=\d)", r"\1", (s or "").lower())
    return {w for w in re.split(r"[^a-z0-9]+", s) if len(w) > 1}


# The slot that holds the model itself. Its file must be named like the recipe:
# folders alone can't tell a Wan checkpoint from a Z-Image one.
_MAIN_SLOTS = ("model", "diffusion_model")


def _recipe_words(recipe: Dict[str, Any]) -> set:
    return _words(str(recipe.get("id"))) | _words(str(recipe.get("name")))


def _fit(file_words: set, recipe: Dict[str, Any]) -> tuple:
    """How well a file's name fits a recipe: words shared, then fewest words of
    the recipe's the file lacks (qwen-image-2.1 is Qwen Image 2.1, not its Turbo)."""
    rw = _recipe_words(recipe)
    return (len(file_words & rw), -len(rw - file_words))


def _best_homes(recipes: List[dict], files: List[dict]) -> Dict[str, tuple]:
    """For each file, how well it fits the recipe it fits best. A model file
    belongs only to the recipes that fit it that well: sd_turbo shares 'sd'
    with every SD recipe but 'sd turbo' only with SD-Turbo."""
    best: Dict[str, tuple] = {}
    for f in files:
        w = _words(str(f.get("name") or f.get("id")))
        best[f["id"]] = max((_fit(w, r) for r in recipes), default=(0, 0))
    return best


# A model file named for no recipe (a fine-tune called "ponyDiffusionV6XL", say)
# is loaded once and the family the engine reports for it is kept here, so the
# file joins that family's recipe from then on: {file id: {recipe, family, size}}.
def _kinds_path() -> Path:
    return _home() / "image_model_kinds.json"


def known_kinds() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_kinds_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_kind(fs: List[dict], recipe: Optional[str], family: str, note: str = "") -> None:
    data = known_kinds()
    for f in fs:
        data[f["id"]] = {"recipe": recipe, "family": family, "size": f.get("size"), "note": note}
    tmp = _kinds_path().with_suffix(".tmp")
    _kinds_path().parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, _kinds_path())


def _placed(files: List[dict]) -> Dict[str, Optional[str]]:
    """File id -> the recipe it was found to be, for files still as they were then."""
    by_id = {f["id"]: f for f in files}
    out: Dict[str, Optional[str]] = {}
    for fid, k in known_kinds().items():
        f = by_id.get(fid)
        if f is not None and (k.get("size") is None or f.get("size") is None or k.get("size") == f.get("size")):
            out[fid] = k.get("recipe")
    return out


def _main_slot(recipe: Dict[str, Any]) -> Optional[dict]:
    return next((s for s in recipe.get("slots") or [] if s["id"] in _MAIN_SLOTS), None)


def _slot_cats(s: dict) -> set:
    return {c.lower() for c in (s.get("categories") or [s.get("category") or ""])}


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def recipes_for_family(family: str, candidates: List[dict], category: str = "") -> List[dict]:
    """The recipes the engine's own name for a family points at, likeliest
    first: one named the same ("SDXL", "SD 1.x"), then those whose id or name
    starts with it ("SD 2.x" -> sd2-..., "Flux" -> flux1-..., flux2-...), the
    ones named for the file's folder (a checkpoint recipe for a file in
    checkpoints) and the plainest before the rest. Which of them really runs
    the file is for loading it to settle."""
    fam = _compact(family)
    if not fam:
        return []
    stem = fam[:-1] if fam.endswith("x") and fam[-2:-1].isdigit() else fam
    folder = _compact(category).rstrip("s")

    def rank(r: dict) -> Optional[tuple]:
        rid, name = _compact(r.get("id")), _compact(r.get("name"))
        if fam in (rid, name):
            return (0,)
        if not (rid.startswith(stem) or name.startswith(stem)):
            return None
        return (1, 0 if folder and folder in rid else 1,
                sum(1 for s in r.get("slots") or [] if s.get("required")), len(rid))
    ranked = [(k, r) for r in candidates for k in [rank(r)] if k is not None]
    return [r for _k, r in sorted(ranked, key=lambda kr: kr[0])]


def recipe_for_family(family: str, candidates: List[dict], category: str = "") -> Optional[dict]:
    found = recipes_for_family(family, candidates, category)
    return found[0] if found else None


def _assign(recipe: Dict[str, Any], slots: Dict[str, List[dict]],
            homes: Optional[Dict[str, tuple]] = None,
            placed: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """A distinct file for each slot the recipe needs, or what is missing.
    Slots with the fewest candidates choose first; a file named for the slot
    (clip_l, t5xxl) wins it, then one sharing words with the recipe. A model
    file found to be one family's (`placed`) goes only to that family's recipe."""
    placed = placed or {}
    want = _words(str(recipe.get("id"))) | _words(str(recipe.get("name")))
    needed = [s for s in recipe.get("slots") or [] if s.get("required")]
    for group in recipe.get("required_any") or []:
        if not any(s["id"] in group for s in needed):
            pick = next((g for g in group if slots.get(g)), group[0])
            needed += [s for s in recipe.get("slots") or [] if s["id"] == pick]
    used, picks, missing = set(), {}, []

    def score(slot_id: str, f: dict) -> tuple:
        name = str(f.get("name") or f.get("id") or "").lower()
        return (placed.get(f["id"]) == recipe.get("id"), slot_id.lower() in name.replace("-", "_"),
                len(_words(name) & want), -len(name))

    for s in sorted(needed, key=lambda s: len(slots.get(s["id"]) or [])):
        sid = s["id"]
        options = [f for f in slots.get(sid) or [] if f["id"] not in used]
        if sid in _MAIN_SLOTS:
            options = [f for f in options
                       if (placed[f["id"]] == recipe.get("id") if f["id"] in placed else
                           (fit := _fit(_words(str(f.get("name") or f.get("id"))), recipe))[0] > 0
                           and fit >= (homes or {}).get(f["id"], (0, 0)))]
        if not options:
            missing.append(s.get("label") or sid)
            continue
        best = max(options, key=lambda f: score(sid, f))
        picks[sid] = best["id"]
        used.add(best["id"])
    return {"picks": picks, "missing": missing}


def _slots_of(r: Dict[str, Any], files: List[dict]) -> Dict[str, List[dict]]:
    return {s["id"]: [f for f in files if str(f.get("category") or "").lower() in _slot_cats(s)]
            for s in r.get("slots") or []}


def readiness(recipes: List[dict], files: List[dict],
              placed: Optional[Dict[str, Optional[str]]] = None) -> List[Dict[str, Any]]:
    """Each recipe with what the files on disk give it: per slot the files that
    fit its folders, the file chosen for each, which needed slots have nothing,
    and whether it can load as is."""
    out = []
    homes = _best_homes(recipes, files)
    for r in recipes:
        slots = _slots_of(r, files)
        a = _assign(r, slots, homes, placed)
        defaults = r.get("defaults") or {}
        out.append({"id": r.get("id"), "name": r.get("name") or r.get("id"), "description": r.get("description") or "",
                    "url": r.get("model_url") or "", "video": "video_frames" in defaults,
                    "slots": slots, "picks": a["picks"], "missing": a["missing"], "ready": not a["missing"]})
    return out


def choose_files(ready: Dict[str, Any], recipe: Dict[str, Any]) -> Dict[str, str]:
    return dict(ready.get("picks") or _assign(recipe, ready["slots"])["picks"])


def unnamed(recipes: List[dict], files: List[dict],
            placed: Optional[Dict[str, Optional[str]]] = None) -> List[dict]:
    """Model files whose names fit no recipe and that haven't been tried yet."""
    placed = placed if placed is not None else _placed(files)
    main_cats = set()
    for r in recipes:
        s = _main_slot(r)
        if s:
            main_cats |= _slot_cats(s)
    homes = _best_homes(recipes, files)
    out, seen = [], set()
    for f in files:
        if str(f.get("category") or "").lower() in main_cats and homes.get(f["id"], (0, 0))[0] == 0 \
                and not any(c["id"] in placed for c in _copies(f, files)) and _base(f) not in seen:
            seen.add(_base(f))
            out.append(f)
    return out


def _base(f: dict) -> str:
    return os.path.basename(str(f.get("name") or f["id"]).replace("\\", "/")).lower()


def _copies(f: dict, files: List[dict]) -> List[dict]:
    """The same file listed in more than one category (a folder and its view)."""
    return [x for x in files if _base(x) == _base(f) and (x.get("size") is None or f.get("size") is None
                                                          or x.get("size") == f.get("size"))]


def _family_logged_since(offset: int) -> str:
    """The family the engine named ("Version: SDXL") in its log after `offset`."""
    try:
        with open(_log_path(), "rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    # "[I] Version: SD 2.x  --- diffusion_engine.cpp:1017": the source place follows.
    found = re.findall(r"Version:\s*(.+?)\s*(?:---.*)?$", text, re.M)
    return found[-1] if found else ""


def folder_for(filename: str) -> str:
    """The category folder a downloaded image or video file goes in: a VAE or a
    LoRA by its name, else the folder of the main slot of the recipe its name
    fits best (a whole checkpoint or a diffusion model). Without the engine to
    ask, diffusion_models, where most single-file GGUFs belong."""
    w = _words(filename)
    if "vae" in w:
        return "vae"
    if "lora" in w:
        return "loras"
    up = running() or (bool(installed()) and start().get("ok"))
    recipes = api("GET", "/sdcpp/v1/recipes", timeout=5) if up else {"failed": True}
    best, cat = (0, 0), "diffusion_models"
    for r in recipes.get("recipes") or []:
        s = _main_slot(r)
        f = _fit(w, r)
        if s and f[0] > 0 and f > best:
            best, cat = f, sorted(_slot_cats(s))[0]
    return cat if cat in CATEGORIES else "diffusion_models"


def load_failure(offset: int, picks: Dict[str, str], row: Dict[str, Any]) -> str:
    """Why a load failed, in words, from what the engine logged after `offset`:
    which of the picked files it refused. Its tensor names carry the slot
    ("text_encoders.llm.model..." is the llm slot's file)."""
    try:
        with open(_log_path(), "rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    errors = [l for l in text.splitlines() if l.startswith("[E]")]
    if not errors:
        return ""
    name = row.get("name") or row.get("id")
    for line in errors:
        m = re.search(r"tensor '([^']+)'", line)
        if not m:
            continue
        dotted = "." + m.group(1) + "."
        slot = next((s for s in sorted(picks, key=len, reverse=True) if f".{s}." in dotted), None)
        if slot:
            file = picks[slot].replace("\\", "/").split("/")[-1]
            label = next((s.get("label") for s in row.get("slots") or [] if s.get("id") == slot), "") or slot
            if "wrong shape" in line:
                return (f"{name} didn't load: the engine can't read {file} ({label}). Its weights are stored in a "
                        f"form it doesn't know, like a 4-bit pack made for another app. A GGUF or a plain "
                        f"safetensors (BF16, FP8) of the same model works.")
            return f"{name} didn't load: the engine refused {file} ({label}): {line[3:].split(' --- ')[0].strip()}"
    return f"{name} didn't load: {errors[-1][3:].split(' --- ')[0].strip()}"


def identify(file_id: str) -> Dict[str, Any]:
    """Find out which family a model file is by loading it once: the engine reads
    the file's own tensors and logs the family, and the recipe with that name is
    remembered for the file. Returns {ok, recipe, family, loaded} or {ok: False, error}."""
    s = start()
    if not s.get("ok"):
        return s
    recipes = api("GET", "/sdcpp/v1/recipes")
    files = api("GET", "/sdcpp/v1/model-files")
    if recipes.get("failed") or files.get("failed"):
        return {"ok": False, "error": recipes.get("error") or files.get("error")}
    recipes, files = recipes.get("recipes") or [], files.get("files") or []
    first = next((x for x in files if x["id"] == file_id), None)
    if first is None:
        return {"ok": False, "error": f"the engine doesn't list {file_id}"}
    copies = _copies(first, files)
    copy_ids = {c["id"] for c in copies}
    others = [x for x in files if x["id"] not in copy_ids]
    cat = str(first.get("category") or "").lower()

    def fits(r: dict, f: dict) -> Optional[Dict[str, str]]:
        """The files to load `r` with `f` as its model, when the rest are on disk."""
        main = _main_slot(r)
        if not main or str(f.get("category") or "").lower() not in _slot_cats(main):
            return None
        slots = _slots_of(r, others)
        slots[main["id"]] = [f]
        a = _assign(r, slots, None, {f["id"]: r.get("id")})
        return None if a["missing"] else a["picks"]

    takers = [(r, p) for r in recipes for f in copies for p in [fits(r, f)] if p]
    f = first
    if not takers:
        return {"ok": False, "error": f"no recipe here can load a file from {cat} with what else is on disk"}
    takers.sort(key=lambda rp: (len(rp[1]), len(str(rp[0].get("id")))))
    picks_for = {r["id"]: p for r, p in takers}
    tried: Dict[str, bool] = {}          # recipe id -> loaded

    def load(r: dict) -> tuple:
        try:
            offset = _log_path().stat().st_size
        except OSError:
            offset = 0
        out = api("POST", "/sdcpp/v1/models/load", {"recipe": r["id"], "models": picks_for[r["id"]]}, timeout=900)
        tried[r["id"]] = not out.get("failed") and out.get("state") == "ready"
        return _family_logged_since(offset), out

    family, out = "", {}
    for probe, _picks in takers[:2]:
        family, out = load(probe)
        if family:
            break
    if not family:
        note = str(out.get("error") or "the engine didn't say what it is")
        _remember_kind(copies, None, "", note)
        return {"ok": False, "error": f"couldn't tell what {f.get('name')} is: {note}"}
    chosen = None
    for r in recipes_for_family(family, [r for r, _p in takers], cat)[:3]:
        if r["id"] not in tried:
            load(r)
        if tried[r["id"]]:
            chosen = r
            break
    _remember_kind(copies, chosen["id"] if chosen else None, family,
                   "" if chosen else str(out.get("error") or ""))
    if not chosen:
        return {"ok": False, "family": family,
                "error": f"{f.get('name')} is {family}, and no recipe here loads it with the files on disk"}
    status = api("GET", "/sdcpp/v1/models/status", timeout=5)
    return {"ok": True, "recipe": chosen["id"], "name": chosen.get("name"), "family": family,
            "loaded": status.get("state") == "ready" and status.get("recipe") == chosen["id"]}


def catalog() -> Dict[str, Any]:
    """{recipes: readiness rows, status} from the running engine."""
    recipes = api("GET", "/sdcpp/v1/recipes")
    files = api("GET", "/sdcpp/v1/model-files")
    if recipes.get("failed") or files.get("failed"):
        return {"error": recipes.get("error") or files.get("error")}
    recipes, files = recipes.get("recipes") or [], files.get("files") or []
    placed = _placed(files)
    rows = readiness(recipes, files, placed)
    return {"recipes": rows, "status": api("GET", "/sdcpp/v1/models/status"),
            "raw": {r["id"]: r for r in recipes}, "unnamed": unnamed(recipes, files, placed),
            "placed": placed, "files": files}


def ensure_loaded(video: bool, recipe_id: str = "") -> Dict[str, Any]:
    """Start the engine and have a model of the right kind loaded: the one asked
    for, else the one loaded now if it fits, else the first that is ready."""
    s = start()
    if not s.get("ok"):
        return s
    api("POST", "/sdcpp/v1/models/refresh")
    cat = catalog()
    if "error" in cat:
        return {"ok": False, "error": cat["error"]}
    status = cat["status"]
    rows = {r["id"]: r for r in cat["recipes"]}
    current = rows.get(status.get("recipe") or "")
    if status.get("state") == "ready" and (not recipe_id or recipe_id == status.get("recipe")) and (
            current is None or current["video"] == video):
        rid = status.get("recipe") or ""
        return {"ok": True, "recipe": rid, "defaults": (cat["raw"].get(rid) or {}).get("defaults") or {}}
    if recipe_id:
        row = rows.get(recipe_id)
        if not row:
            return {"ok": False, "error": f"the engine has no recipe '{recipe_id}'"}
    else:
        row = next((r for r in cat["recipes"] if r["ready"] and r["video"] == video), None)
        for f in ([] if row else cat["unnamed"][:3]):
            found = identify(f["id"])
            if found.get("ok") and rows.get(found["recipe"], {}).get("video", video) == video:
                if found.get("loaded"):
                    rid = found["recipe"]
                    return {"ok": True, "recipe": rid, "defaults": (cat["raw"].get(rid) or {}).get("defaults") or {}}
                cat = catalog()
                if "error" in cat:
                    return {"ok": False, "error": cat["error"]}
                row = next((r for r in cat["recipes"] if r["ready"] and r["video"] == video), None)
                if row:
                    break
    if not row:
        kind = "video" if video else "image"
        return {"ok": False, "code": "no_model",
                "error": f"no {kind} model on this PC yet: add one under Models > Images & video"}
    if not row["ready"]:
        return {"ok": False, "code": "missing_files",
                "error": f"{row['name']} still needs: {', '.join(row['missing'])}"}
    body = {"recipe": row["id"], "models": choose_files(row, cat["raw"][row["id"]])}
    try:
        offset = _log_path().stat().st_size
    except OSError:
        offset = 0
    out = api("POST", "/sdcpp/v1/models/load", body, timeout=900)
    if out.get("failed") or out.get("state") != "ready":
        why = load_failure(offset, body["models"], cat["raw"][row["id"]])
        return {"ok": False, "code": "load_failed", "error": why or out.get("error") or "the model didn't load"}
    return {"ok": True, "recipe": row["id"], "defaults": cat["raw"][row["id"]].get("defaults") or {}}


# ---------------------------------------------------------------- generating

def _out_dir() -> Path:
    try:
        from .tools import WORKSPACE
        d = Path(WORKSPACE) / "generated"
    except Exception:
        d = _home() / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _name_for(prompt: str, ext: str) -> Path:
    slug = "-".join(re.findall(r"[a-z0-9]+", (prompt or "image").lower())[:7]) or "image"
    path = _out_dir() / f"{slug}.{ext}"
    n = 2
    while path.exists():
        path = _out_dir() / f"{slug}-{n}.{ext}"
        n += 1
    return path


def _image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def size_for(aspect: str, defaults: Dict[str, Any]) -> tuple:
    """Width and height for an aspect ratio at the model's own size (its pixel
    count), in steps of 64."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)\s*$", aspect or "")
    if not m:
        return 0, 0
    a, b = float(m.group(1)), float(m.group(2))
    area = int(defaults.get("width") or 1024) * int(defaults.get("height") or 1024)
    w = (area * a / b) ** 0.5
    return max(64, round(w / 64) * 64), max(64, round(w * b / a / 64) * 64)


def generate(prompt: str, video: bool = False, width: int = 0, height: int = 0, steps: int = 0,
             seed: int = -1, count: int = 1, negative: str = "", image: str = "", frames: int = 0,
             recipe: str = "", aspect: str = "", on_progress: Optional[Callable[[int, int], None]] = None,
             timeout: float = 3600) -> Dict[str, Any]:
    """Make images or a video locally. Returns {ok, paths, recipe, seconds} or {ok: False, error}."""
    ready = ensure_loaded(video, recipe)
    if not ready.get("ok"):
        return ready
    body: Dict[str, Any] = {"prompt": prompt, "negative_prompt": negative or "", "seed": int(seed),
                            "output_format": "webm" if video else "png"}
    if not (width and height) and aspect:
        width, height = size_for(aspect, ready.get("defaults") or {})
    if width and height:
        body["width"], body["height"] = int(width), int(height)
    if steps:
        body["sample_params"] = {"sample_steps": int(steps)}
    if image:
        body["init_image"] = _image_b64(image)
    if video:
        if frames:
            body["video_frames"] = int(frames)
    else:
        body["batch_count"] = max(1, min(int(count or 1), 4))
    job = api("POST", "/sdcpp/v1/vid_gen" if video else "/sdcpp/v1/img_gen", body)
    if job.get("failed"):
        return {"ok": False, "error": str(job.get("error"))}
    jid = job["id"]
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(1.0)
        j = api("GET", f"/sdcpp/v1/jobs/{jid}", timeout=10)
        if j.get("failed"):
            continue
        pv = j.get("preview") or {}
        if on_progress and pv.get("total_steps"):
            try:
                on_progress(int(pv.get("step") or 0), int(pv["total_steps"]))
            except Exception:
                pass
        st = j.get("status")
        if st in ("failed", "cancelled"):
            return {"ok": False, "error": (j.get("error") or {}).get("message") or st}
        if st == "completed":
            res = j.get("result") or {}
            paths = []
            if video:
                p = _name_for(prompt, res.get("output_format") or "webm")
                p.write_bytes(base64.b64decode(res.get("b64_json") or ""))
                paths.append(str(p))
            else:
                for im in res.get("images") or []:
                    p = _name_for(prompt, res.get("output_format") or "png")
                    p.write_bytes(base64.b64decode(im.get("b64_json") or ""))
                    paths.append(str(p))
            secs = ((j.get("completed") or 0) - (j.get("started") or 0)) / 1000
            return {"ok": True, "paths": paths, "recipe": ready.get("recipe"), "seconds": round(secs, 1)}
    api("POST", f"/sdcpp/v1/jobs/{jid}/cancel")
    return {"ok": False, "error": "it took too long and was stopped"}


def has_model(video: bool) -> bool:
    """Whether a local model of this kind could run now, without starting anything
    new when the engine isn't installed."""
    if not installed():
        return False
    if not start().get("ok"):
        return False
    cat = catalog()
    # A file named for no family yet is worth a try: the first request finds out
    # what it is, and one that turns out not to load falls back as "no_model".
    return any(r["ready"] and r["video"] == video for r in cat.get("recipes") or []) or bool(cat.get("unnamed"))


def overview(start_engine: bool = True) -> Dict[str, Any]:
    """What the Models tab shows: the engine, the model folders, and each recipe
    with whether the files on disk make it ready."""
    builds = installed()
    out: Dict[str, Any] = {"installed": bool(builds), "build": builds[0].parent.name if builds else "",
                           "roots": model_roots(), "folder": str(models_dir()), "recipes": [], "status": {},
                           "forge": forge_models()}
    if builds:
        from .llmserver import engine_build_of
        out["backend"] = engine_build_of(builds[0])
    if not builds or not (running() or (start_engine and start().get("ok"))):
        return out
    api("POST", "/sdcpp/v1/models/refresh")
    cat = catalog()
    if "error" in cat:
        out["error"] = cat["error"]
        return out
    out["status"] = cat["status"]
    out["recipes"] = [{**{k: r[k] for k in ("id", "name", "description", "url", "video", "ready", "missing", "picks")},
                       "has_model": any(s in (r["picks"] or {}) for s in _MAIN_SLOTS)}
                      for r in cat["recipes"]]
    kinds = known_kinds()
    by_id = {f["id"]: f for f in cat["files"]}
    out["unnamed"] = [{"id": f["id"], "name": f.get("name") or f["id"], "category": f.get("category")}
                      for f in cat["unnamed"]]
    out["unsure"] = [{"id": fid, "name": by_id[fid].get("name") or fid, "family": kinds.get(fid, {}).get("family") or "",
                      "note": kinds.get(fid, {}).get("note") or ""}
                     for fid, rid in cat["placed"].items() if rid is None and fid in by_id]
    return out


def forge_models() -> str:
    """A Forge / Stable Diffusion WebUI install's models folder, when there is
    one and it isn't read already. The engine reads its Stable-diffusion, VAE
    and Lora folders as they are."""
    try:
        from .tools import FORGE_DIR
    except Exception:
        return ""
    d = os.path.join(FORGE_DIR or "", "models")
    if not FORGE_DIR or not os.path.isdir(d):
        return ""
    known = {os.path.normcase(os.path.abspath(r)) for r in model_roots()}
    return "" if os.path.normcase(os.path.abspath(d)) in known else d


def set_folder(path: str, add: bool = True) -> Dict[str, Any]:
    """Add or remove a models folder of the person's own (a ComfyUI or WebUI one works)."""
    from .web import _load_settings, _save_settings
    p = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    if add and not os.path.isdir(p):
        return {"ok": False, "error": f"there is no folder {p}"}
    s = _load_settings()
    dirs = [d for d in (s.get("image_model_dirs") or []) if os.path.normcase(d) != os.path.normcase(p)]
    if add:
        dirs.append(p)
    else:
        # The view only holds hard links; removing them leaves the files as they are.
        shutil.rmtree(os.path.join(p, VIEW), ignore_errors=True)
    s["image_model_dirs"] = dirs
    _save_settings(s)
    if running():
        stop()                 # the folders are given at start
    return {"ok": True, "roots": model_roots()}


import atexit as _atexit  # noqa: E402
_atexit.register(stop)     # Windows' job object ends it with Hearth too; elsewhere this does
