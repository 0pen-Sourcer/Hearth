"""What this copy of Inglen can do right now, and setting up what's missing.

The agent reads this through what_can_i_do and acts on it through set_up. Each area
is read from the code and the PC when asked, never from a list kept here. None of it
goes in the system prompt: it changes as things are installed, and the prompt has to
stay the same byte for byte between turns.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- what's on this PC

def saved_keys() -> List[str]:
    """The cloud providers a key is saved for, by name. Never the keys."""
    from .headless import PROVIDERS
    from .web import _provider_key
    return [v[0] for pid, v in PROVIDERS.items() if pid != "local" and _provider_key(pid)]


def _brain(deep: bool) -> str:
    from .persona import _brain_line
    keys = saved_keys()
    return (_brain_line() + " " + (f"Keys saved for: {', '.join(keys)}." if keys else "No cloud keys are saved.")
            + " They switch brains themselves in the model menu (/model in the terminal); keys go in Settings > Brain.")


def _chat_models() -> List[Dict[str, Any]]:
    from . import llmserver
    return [m for m in llmserver.scan_disk_for_models() or [] if not (m.get("not_chat") or m.get("incomplete"))]


def _chat(deep: bool) -> str:
    from . import llmserver
    engine = llmserver.find_native_llama_server()
    disk = _chat_models()
    out = [(f"Chat engine installed ({engine.get('label') or 'llama.cpp'})." if engine else
            "Chat engine not installed: set_up(what='chat') installs it for this graphics card, so no separate "
            "server is needed."),
           f"{_plural(len(disk), 'model')} on disk" + (" (list_models shows them, load_model loads one)." if disk else ".")]
    if deep:
        out += [f"  {m.get('filename')} ({m.get('size_gb')} GB)" for m in disk[:15]]
        pick = llmserver.suggest_model()
        vram = llmserver.detect_gpu_vram_gb()
        out.append(f"Graphics card memory: {vram} GB." if vram else "No graphics card memory detected.")
        if pick:
            out.append(f"A good first model for this PC: {pick['repo']}/{pick['filename']} ({pick.get('size_gb')} GB). "
                       "set_up(what='chat', name='<words, a repo or repo/file.gguf>') fetches this or any other "
                       "Hugging Face GGUF.")
        return "\n".join(out)
    if not disk:
        out.append("what_can_i_do(area='chat') suggests one that fits; set_up fetches it.")
    return " ".join(out)


def _image_clouds(video: bool = False) -> List[str]:
    from . import imagine
    can = imagine._supported_for_video if video else imagine._supported_for_images
    return [prov for prov in imagine._SAVED_AS if can(prov) and imagine._saved_key(prov)]


def _images(deep: bool) -> str:
    from . import sdserver
    clouds = _image_clouds()
    cloud = f" Cloud pictures work through a saved key: {', '.join(clouds)}." if clouds else ""
    if not sdserver.installed():
        return ("The image and video engine isn't installed." + cloud + " set_up(what='images') installs it for this "
                "graphics card, then this area lists models that fit. generate_image and generate_video make them.")
    if not deep and not sdserver.running():
        return ("Image and video engine installed, stopped until needed." + cloud + " Just call generate_image or "
                "generate_video; what_can_i_do(area='images') lists the models ready and the ones to download.")
    view = sdserver.overview(start_engine=deep)
    rows = view.get("recipes") or []
    ready = [r for r in rows if r.get("ready")]
    pics = [r["name"] for r in ready if not r.get("video")]
    vids = [r["name"] for r in ready if r.get("video")]
    out = ["Image and video engine installed." + cloud,
           "Ready for pictures: " + (", ".join(pics) or "no model yet") + ".",
           "Ready for video: " + (", ".join(vids) or "no model yet") + "."]
    if not deep:
        return " ".join(out)
    sets = sdserver.catalog_sets()
    can_get = sorted((s for s in sets.get("sets") or [] if s.get("ok") and s.get("bytes")),
                     key=lambda s: (bool(s.get("video")), s["bytes"]))
    for s in can_get:
        main = next((p for p in s.get("parts") or [] if p.get("options")), {})
        fit = next((o.get("fit") for o in main.get("options") or [] if o.get("name") == main.get("name")), "")
        out.append(f"  {s['id']}: {s.get('blurb')}, {s['bytes'] / 1e9:.1f} GB"
                   + (f", fit on the card: {fit}" if fit and fit != "unknown" else ""))
    if can_get:
        out.append("set_up(what='images', name='<id>') fetches every file a model needs, sized for this card.")
    elif not sets.get("ready"):
        out.append("The list of models to download is still being built, about a minute: look again then.")
    return "\n".join(out)


def media_gap(video: bool = False) -> str:
    """What is missing for a picture or a video and how to get it, for a tool error."""
    from . import sdserver
    kind = "videos" if video else "pictures"
    if not sdserver.installed():
        return (f"Nothing on this PC can make {kind} yet. With their yes, set_up(what='images') downloads and installs "
                "the engine itself, so they don't have to get anything; what_can_i_do(area='images') then lists "
                "models that fit, with their sizes.")
    return (f"The engine is installed but no model for {kind} is ready. what_can_i_do(area='images') lists models "
            "that fit this PC, and with their yes set_up(what='images', name='<id>') fetches one.")


def _voice(deep: bool) -> str:
    from . import listen, voice
    tts, stt = voice.models_present(), listen.model_present()
    if tts and stt:
        return "Speech out and in are ready (set_voice and list_voices pick a voice)."
    gaps = [w for w, have in (("speech out", tts), ("speech in", stt)) if not have]
    return f"Not fetched yet: {' and '.join(gaps)}. set_up(what='voice') fetches both."


def _phone(deep: bool) -> str:
    from . import call_me, channel_core
    here = channel_core.here()
    out = []
    if here is not None:
        out.append(f"You are talking to them over {getattr(here[0], 'label', 'their phone')} right now: send_to_phone "
                   "puts a message, picture or any file in this conversation.")
    no = call_me.reach()
    if not no:
        out.append("A phone is paired and reachable: send_to_phone sends messages, pictures and any file; call_phone rings it.")
    else:
        out.append(f"The Inglen app can't be reached: {no}. That step is theirs, on the PC; set_up can't do it. "
                   "send_to_phone still reaches a connected chat app.")
    if deep:
        out.append("There are no voice notes: to speak to them, call_phone.")
    return " ".join(out)


def _chat_apps(deep: bool) -> str:
    from .tools import _connectors
    return (_connectors({"action": "status"}).replace("\n", "; ") + ". connectors(action='connect') starts one "
            "with a bot token they give you, and approve lets someone in. send_to_phone reaches them there.")


def _email(deep: bool) -> str:
    from . import email_tools
    if email_tools.status().get("configured"):
        return "Set up: read_inbox and send_email work."
    return "Not set up. It takes their app password, in Settings > Connectors."


def _helpers(deep: bool) -> str:
    from . import subagents
    from .web import _load_settings
    n, why = subagents.concurrency_capacity()
    out = [f"spawn_subagent starts helpers for work that splits into independent parts, runs long, or belongs in the "
           f"background. Up to {n} at once ({why}). delegate hands one task to an agent program; launch_team opens a "
           "watched team; race_helpers compares agents on one coding task."]
    helper = _load_settings().get("helper_model") or ""
    keys = saved_keys()
    if helper:
        out.append(f"A model is set for runs_on='local' helpers: {os.path.basename(str(helper))}.")
    elif deep:
        out.append("For helpers on this PC while your own brain stays as it is: load_model(for='helpers').")
    if deep and keys:
        out.append(f"Helpers can think on: {', '.join(keys)} (runs_on).")
    return " ".join(out)


def _agents(deep: bool) -> str:
    from . import external_agents as xa
    here = [a["label"] for a in xa.available()]
    get = [a for a in xa.missing() if a["get"] in ("setup", "install")]
    out = [("Installed: " + ", ".join(here) + ". delegate gives one a task; agent_sessions reads and continues the "
            "sessions they run themselves.") if here else "No agent programs are installed."]
    if get:
        out.append("set_up(what='agent', name=...) installs: " + ", ".join(a["id"] for a in get) + ".")
    if deep:
        by_site = [f"{a['label']} ({a['site']})" for a in xa.missing() if a["get"] == "site"]
        if by_site:
            out.append("Only from their makers: " + ", ".join(by_site) + ".")
    return " ".join(out)


def _memory_area(deep: bool) -> str:
    return ("memory_save and memory_recall keep facts; search_chats and read_chat reach every past conversation on "
            "any brain or surface.")


def _reminders(deep: bool) -> str:
    return "set_reminder fires on its own, once or repeating, and can run a tool at that moment; list_reminders shows them."


def _desktop(deep: bool) -> str:
    from . import desktop_a11y
    try:
        import playwright  # type: ignore  # noqa: F401
        page = "browse drives a window of its own."
    except ImportError:
        page = "browse needs Playwright: run its install yourself with start_job."
    tree = "You can read any app's controls with desktop_snapshot" if desktop_a11y.available() else \
        "The accessibility tree isn't available here, so work from screenshot and smart_click"
    return f"{tree}. screenshot and view_image see the screen. {page}"


def _mcp(deep: bool) -> str:
    from . import mcp_client
    rows = mcp_client.list_bridges()
    path = str(mcp_client._mcp_json_path())
    if not rows:
        return f"None connected. They're added in Settings > MCP servers, or in {path}."
    out = [f"{r['raw_name']}: {r['state']}, {_plural(len(r['tools']), 'tool')}" for r in rows]
    how = "load_tools with the server's name brings its tools in."
    if deep:
        for r in rows:
            out += [f"  {t['name']}" for t in r["tools"][:30]]
        return "\n".join(out + [how])
    return "; ".join(out) + ". " + how


def _skills(deep: bool) -> str:
    from . import plugins, skills_loader
    from .tools import WORKSPACE
    names = [s["name"] for s in skills_loader.list_skills()]
    made = sorted(plugins._plugin_names(WORKSPACE))
    return (f"Skills: {', '.join(names) or 'none'} (load_skill follows one; create_skill and install_skill add more). "
            f"Plugins, tools of your own: {', '.join(made) or 'none'} (create_plugin writes one that stays).")


def _undo(deep: bool) -> str:
    from . import undo
    rows = undo.recent(8 if deep else 3)
    if not rows:
        return "Nothing to undo yet. Files changed by tools are copied aside first, for a week."
    lines = [f"{r['id']}: {r['what']}" + ("" if r["can"] else " (can't be undone)") for r in rows]
    return "undo(id) takes one back. Latest: " + "; ".join(lines) + "."


# Key, what the person would call it, how to read it.
AREAS: List[Tuple[str, str, Callable[[bool], str]]] = [
    ("brain", "Brain", _brain),
    ("chat", "Chat models", _chat),
    ("images", "Pictures and video", _images),
    ("voice", "Voice", _voice),
    ("phone", "Phone", _phone),
    ("apps", "Chat apps", _chat_apps),
    ("email", "Email", _email),
    ("helpers", "Helpers", _helpers),
    ("agents", "Agent programs", _agents),
    ("memory", "Memory and past chats", _memory_area),
    ("reminders", "Reminders", _reminders),
    ("desktop", "Desktop and browser", _desktop),
    ("mcp", "MCP servers", _mcp),
    ("skills", "Skills and plugins", _skills),
    ("undo", "Undo", _undo),
]


def area_names() -> List[str]:
    return [key for key, _title, _read in AREAS]


def report(area: str = "") -> str:
    """Every area in a line or two, or one in full."""
    area = (area or "").strip().lower()
    wanted = [a for a in AREAS if a[0] == area] if area else AREAS
    if not wanted:
        return f"Error: no area '{area}'. There are: {', '.join(area_names())}."
    lines = []
    for job in running_jobs():
        lines.append(f"Being set up now: {job}.")
    for _key, title, read in wanted:
        try:
            body = read(bool(area))
        except Exception as e:
            body = f"couldn't check ({type(e).__name__}: {e})"
        lines.append(f"{title}: {body}")
    if not area:
        lines.append("what_can_i_do(area=...) gives one area in full. Every held-back tool is named in load_tools.")
    return "\n".join(lines)


# ---------------------------------------------------------------- setting things up

class Failed(Exception):
    """A setup step that stopped, in words for the agent."""


_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _tell(job: Dict[str, Any]) -> None:
    """Show the job in the Downloads list, in whichever window is open."""
    done, total = job.get("done") or 0, job.get("total") or 0
    now = time.time()
    if job["status"] == "downloading" and now - job.get("told", 0) < 0.7:
        return
    went = now - job["started"]
    speed = done / went if went > 1 else 0
    job["told"] = now
    try:
        from . import live_bus
        live_bus.publish({"kind": "setup", "id": job["id"], "name": job["label"], "status": job["status"],
                          "done": done, "total": total, "pct": int(done * 100 / total) if total else None,
                          "speed": speed, "eta": (total - done) / speed if speed > 0 else 0})
    except Exception:
        pass


def _progress(job: Dict[str, Any], done: int, total: int) -> None:
    job["done"], job["total"] = done, total
    _tell(job)


def running_jobs() -> List[str]:
    with _JOBS_LOCK:
        rows = [j for j in _JOBS.values() if j["status"] == "downloading"]
    return [j["label"] + (f", {int(j['done'] * 100 / j['total'])}%" if j.get("total") else "") for j in rows]


def cancel(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    if not job or job["status"] != "downloading":
        return False
    job["stop"].set()
    try:
        job["halt"]()
    except Exception:
        pass
    return True


def _start(key: str, label: str, work: Callable[[Dict[str, Any]], List[str]]) -> str:
    with _JOBS_LOCK:
        have = _JOBS.get(key)
        if have and have["status"] == "downloading":
            return f"{have['label']} is already under way; a report arrives when it ends."
        job = _JOBS[key] = {"id": key, "label": label, "status": "downloading", "done": 0, "total": 0,
                            "started": time.time(), "stop": threading.Event(), "halt": lambda: None}

    def run() -> None:
        from .subagents import enqueue_notification
        try:
            said = " ".join(work(job))
            job["status"] = "done"
            enqueue_notification(source="setup", name=label, status="completed", result_text=said,
                                 summary=f"{label}: done", elapsed_s=round(time.time() - job["started"]))
        except Exception as e:
            job["status"] = "error"
            why = "It was cancelled." if job["stop"].is_set() else (
                str(e) if isinstance(e, Failed) else f"{type(e).__name__}: {e}")
            enqueue_notification(source="setup", name=label, status="failed",
                                 result_text=f"{label} didn't finish. {why}", summary=f"{label}: stopped",
                                 elapsed_s=round(time.time() - job["started"]))
        _tell(job)

    threading.Thread(target=run, daemon=True, name=f"setup-{key}").start()
    _tell(job)
    return (f"Started: {label}. It shows in the window's Downloads list, and a report arrives here when it ends with "
            "what's next, so don't guess sizes or places meanwhile.")


def _fits_disk(folder: str, need: int) -> None:
    os.makedirs(folder, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if need and need + 1_000_000_000 > free:
        raise Failed(f"That needs {need / 1e9:.1f} GB and {folder} has {free / 1e9:.1f} GB free.")


def _fetch(job: Dict[str, Any], repo: str, path: str, base: int, total: int, dest_dir: str = "", save_as: str = "") -> None:
    """One Hugging Face file through the model downloader, so it resumes like any other."""
    from . import llmserver
    name = (save_as or path).split("/")[-1]
    llmserver.start_model_download(repo, path, dest_dir=dest_dir, save_as=save_as)
    job["halt"] = lambda: llmserver.pause_model_download(name)
    while True:
        st = llmserver.model_download_state(name) or {}
        if st.get("status") != "downloading":
            got = st.get("result") or {}
            if got.get("ok"):
                return
            raise Failed(str(got.get("error") or "the download stopped"))
        _progress(job, base + int(st.get("done") or 0), max(total, base + int(st.get("total") or 0)))
        time.sleep(0.5)


def _image_work(name: str) -> Callable[[Dict[str, Any]], List[str]]:
    def work(job: Dict[str, Any]) -> List[str]:
        from . import sdserver
        said = []
        if not sdserver.installed():
            job["halt"] = sdserver.cancel_install
            r = sdserver.install(on_progress=lambda d, t: _progress(job, d, t))
            if not r.get("ok"):
                raise Failed(str(r.get("error")))
            said.append(f"The image engine is installed ({r.get('tag')}, {r.get('backend')}).")
        if not name:
            return said + ["No model is on it yet: what_can_i_do(area='images') lists the ones that fit, and "
                           "set_up(what='images', name='<id>') fetches one."]
        started = sdserver.start()
        if not started.get("ok"):
            raise Failed(str(started.get("error")))
        recipe, options = sdserver.find_recipe(name)
        if recipe is None:
            raise Failed(f"No model called '{name}'. " + (f"Closest: {', '.join(options)}." if options else
                         "what_can_i_do(area='images') lists them."))
        plan = sdserver.plan_for_this_pc(recipe["id"])
        if not plan.get("ok"):
            raise Failed(str(plan.get("error")) + ". what_can_i_do(area='images') lists the ones that can be fetched.")
        todo = [p for p in plan["parts"] if not p.get("have")]
        total = sum(int(p.get("size") or 0) for p in todo)
        _fits_disk(str(sdserver.models_dir()), total)
        base = 0
        for p in todo:
            if job["stop"].is_set():
                raise Failed("It was cancelled.")
            _fetch(job, p["repo"], p["path"], base, total, str(sdserver.models_dir() / p["category"]),
                   p["path"].split("/")[-1])
            base += int(p.get("size") or 0)
        sdserver.api("POST", "/sdcpp/v1/models/refresh")
        tool = "generate_video" if "video_frames" in (recipe.get("defaults") or {}) else "generate_image"
        return said + [f"{recipe.get('name') or recipe['id']} is ready ({total / 1e9:.1f} GB fetched): {tool} uses it now."]
    return work


def pick_chat_file(name: str) -> Dict[str, Any]:
    """{repo, filename, size_gb} for words, a repo or repo/file.gguf; {error} when nothing fits."""
    from . import llmserver
    name = re.sub(r"^https?://(www\.)?huggingface\.co/", "", (name or "").strip()).strip("/")
    parts = name.split("/")
    if name.lower().endswith(".gguf") and len(parts) >= 3:
        return {"repo": "/".join(parts[:2]), "filename": "/".join(p for p in parts[2:] if p not in ("resolve", "main", "blob"))}
    repo = name if len(parts) == 2 else ""
    if not repo:
        hits = [h for h in llmserver.search_huggingface(name, limit=5) if h.get("hf_repo")]
        if not hits:
            return {"error": f"Hugging Face has no GGUF for '{name}'."}
        repo = hits[0]["hf_repo"]
    files = [f for f in llmserver.list_hf_files(repo) if not f.get("error")]
    if not files:
        return {"error": f"{repo} has no GGUF files to fetch."}
    good = [f for f in files if f.get("recommended")] or [f for f in files if f.get("fit_tier") == "good"]
    if not good:
        return {"error": f"Nothing in {repo} fits this PC well. Sizes there run from "
                         f"{min(f.get('size_gb') or 0 for f in files)} GB; name a smaller model or a file."}
    return {"repo": repo, "filename": good[0]["filename"], "size_gb": good[0].get("size_gb")}


def _chat_work(pick: Optional[Dict[str, Any]]) -> Callable[[Dict[str, Any]], List[str]]:
    def work(job: Dict[str, Any]) -> List[str]:
        from . import llmserver
        said = []
        if not llmserver.find_native_llama_server():
            job["halt"] = llmserver.cancel_llama_download
            r = llmserver.download_llama_runtime(on_progress=lambda d, t: _progress(job, d, t))
            if not r.get("ok"):
                raise Failed(str(r.get("error")))
            llmserver.reset_native_cache()
            said.append(f"The chat engine is installed ({r.get('tag')}).")
        if not pick:
            return said + ["what_can_i_do(area='chat') suggests a model that fits."]
        size = int(float(pick.get("size_gb") or 0) * 1e9)
        _fits_disk(str(llmserver.MODELS_DIR), size)
        _fetch(job, pick["repo"], pick["filename"], 0, size)
        file = pick["filename"].split("/")[-1]
        return said + [f"{file} is downloaded. load_model loads it, with their yes."]
    return work


def _voice_work(job: Dict[str, Any]) -> List[str]:
    from . import listen, voice
    if not voice.models_present() and not voice.download_models(lambda _s, d, t: _progress(job, d, t),
                                                                job["stop"].is_set):
        raise Failed(voice.status().get("last_load_error") or "the voice files didn't download")
    if not listen.model_present() and not listen.ensure_model():
        raise Failed(listen.status().get("last_load_error") or "the speech model didn't download")
    return ["Voice is ready, speaking and listening."]


def _agent_work(aid: str, label: str) -> Callable[[Dict[str, Any]], List[str]]:
    def work(job: Dict[str, Any]) -> List[str]:
        from . import external_agents as xa
        job["halt"] = lambda: xa.cancel_job(aid)
        r = xa.install(aid)
        if not r.get("ok"):
            raise Failed(str(r.get("error")))
        return [f"{label} is installed. They sign in to it once, on its first run or from Settings; delegate reaches it after that."]
    return work


def set_up(what: str, name: str = "") -> str:
    """Start installing what's missing. Returns at once; the report comes as a notification."""
    what = (what or "").strip().lower()
    name = (name or "").strip()
    if what == "images":
        from . import sdserver
        if not name and sdserver.installed():
            return "The image engine is already installed. Name a model: what_can_i_do(area='images') lists them."
        label = f"Setting up {name}" if name else "Installing the image engine"
        return _start(f"images-{name}".rstrip("-"), label, _image_work(name))
    if what == "chat":
        from . import llmserver
        pick = None
        if name:
            pick = pick_chat_file(name)
            if pick.get("error"):
                return f"Error: {pick['error']}"
        elif llmserver.find_native_llama_server():
            return "The chat engine is already installed. Name a model: what_can_i_do(area='chat') suggests one."
        label = f"Fetching {pick['filename'].split('/')[-1]}" if pick else "Installing the chat engine"
        return _start(f"chat-{name}".rstrip("-"), label, _chat_work(pick))
    if what == "voice":
        return _start("voice", "Fetching the voice files", _voice_work)
    if what == "agent":
        from . import external_agents as xa
        want = name.lower()
        rows = xa.missing()
        row = next((a for a in rows if want in (a["id"], a["label"].lower())), None)
        if row is None:
            if any(want in (a["id"], a["label"].lower()) for a in xa.available()):
                return f"{name} is already installed."
            can = [a["id"] for a in rows if a["get"] in ("setup", "install")]
            return f"Error: name is one of: {', '.join(can)}."
        if row["get"] == "site":
            return f"Error: {row['label']} comes from its maker: {row['site']}. Inglen can't install it."
        return _start(f"agent-{row['id']}", f"Installing {row['label']}", _agent_work(row["id"], row["label"]))
    return "Error: what is images, chat, voice or agent."
