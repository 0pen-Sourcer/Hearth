"""Hearth desktop app backend — HTTP server.

Powers the desktop UI (`python -m hearth.desktop`) and also runs standalone
as a browser-based app (`python -m hearth.web` opens the UI in your default
browser). Stdlib-only — no Flask, no FastAPI, no extra deps.

Endpoints
---------

GET  /                          → serves ui.html
GET  /api/state                 → snapshot: model, model state, tools, mem, gpu, settings
GET  /api/models                → list ALL models LM Studio sees (loaded + downloaded)
POST /api/models/load           → {id} load a model (tries REST then `lms` CLI)
POST /api/models/eject          → unload currently-loaded model
POST /chat                      → {prompt, think} stream NDJSON events from headless.run_once
GET  /api/memory                → list memory entries (name, type, description)
GET  /api/memory/{name}         → get one entry's full body
DELETE /api/memory/{name}       → delete one entry
GET  /api/files?path=...        → list workspace files at path
POST /api/upload                → {name, content_b64} save to workspace
GET  /api/file?path=...&op=read → read file or summarize via tools
GET  /api/logs?lines=N          → tail activity.jsonl
GET  /api/settings              → load ~/Jarvis/settings.json
POST /api/settings              → save settings
GET  /api/gpu                   → quick nvidia-smi snapshot (cached 2s)
GET  /api/tools                 → tool definitions (for docs / debug)
GET  /api/persona               → current system prompt (for settings preview)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from . import system_prompt, TOOL_DEFINITIONS, memory, execute_tool
from .headless import run_once
from .tools import WORKSPACE, LOGS_DIR

# Voice modules are optional — guard imports so a missing dep doesn't kill the server.
try:
    from . import voice as _voice
except Exception:
    _voice = None
try:
    from . import listen as _listen
except Exception:
    _listen = None
try:
    from . import realtime_voice as _rt_voice
except Exception:
    _rt_voice = None

# Realtime caption + utterance queue. Filled by the recorder callbacks and
# drained by the SSE-style /api/voice/realtime/stream handler.
import queue as _queue_mod
_rt_event_queue: "_queue_mod.Queue[dict]" = _queue_mod.Queue()

# Window reference for single-instance focus. desktop.py sets this to its
# pywebview window object so /api/focus can bring it to front when a second
# launch attempt occurs.
_window_ref = None


def set_window_ref(win) -> None:
    """desktop.py calls this so /api/focus can surface the existing window."""
    global _window_ref
    _window_ref = win


def _focus_window() -> dict:
    """Best-effort: bring the desktop window to the foreground."""
    win = _window_ref
    if win is None:
        return {"ok": False, "reason": "no window ref (running headless?)"}
    try:
        # pywebview API: restore() un-minimizes; show() makes visible;
        # on_top toggle flashes it to the front.
        if hasattr(win, "restore"):
            try: win.restore()
            except Exception: pass
        if hasattr(win, "show"):
            try: win.show()
            except Exception: pass
        # Toggle on_top to force front on Windows (the only reliable way
        # without win32gui).
        if hasattr(win, "on_top"):
            try:
                win.on_top = True
                import time as _time; _time.sleep(0.1)
                win.on_top = False
            except Exception:
                pass
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}

_HERE = os.path.dirname(os.path.abspath(__file__))
_UI_PATH = os.path.join(_HERE, "ui.html")
LOCAL_API_BASE = os.getenv("LOCAL_API_BASE", "http://localhost:1234/v1")
LM_STUDIO_V0 = LOCAL_API_BASE.replace("/v1", "/api/v0")
SETTINGS_PATH = os.path.join(WORKSPACE, "settings.json")
CONVOS_DIR = os.path.join(WORKSPACE, "conversations")

# Set when the user hits Stop in the GUI; run_once checks it between turns and
# bails. Cleared at the start of each /chat. Single-user local GUI = one
# generation at a time, so a single shared flag is enough.
_CANCEL = threading.Event()

# Suppress the brief cmd console flash on every subprocess we spawn under
# a GUI/tray context. Worthless when running as CLI, harmless either way.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
os.makedirs(CONVOS_DIR, exist_ok=True)


def _convo_path(cid: str) -> str:
    # Sanitize id to a safe filename. UI sends `c_<rand>` style ids.
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cid)[:80]
    return os.path.join(CONVOS_DIR, f"{safe}.json")


def _list_convos() -> List[Dict]:
    out: List[Dict] = []
    if not os.path.isdir(CONVOS_DIR):
        return out
    for fn in os.listdir(CONVOS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(CONVOS_DIR, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "id": data.get("id", fn[:-5]),
            "title": data.get("title", "Untitled"),
            "updated": data.get("updated", 0),
            "created": data.get("created", 0),
            "msg_count": len(data.get("messages", [])),
        })
    out.sort(key=lambda x: -x.get("updated", 0))
    return out


def _load_convo(cid: str) -> Optional[Dict]:
    p = _convo_path(cid)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_convo(data: Dict) -> bool:
    cid = data.get("id")
    if not cid:
        return False
    p = _convo_path(cid)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False


def _delete_convo(cid: str) -> bool:
    p = _convo_path(cid)
    if not os.path.isfile(p):
        return False
    try:
        os.remove(p)
        return True
    except OSError:
        return False

# ---------------------------------------------------------------------------
# Small caches so the UI's status polling doesn't melt anything
# ---------------------------------------------------------------------------

_gpu_cache: Dict[str, Any] = {"ts": 0, "data": None}
_models_cache: Dict[str, Any] = {"ts": 0, "data": None}

# ----- Permission system -----
# When a risky tool fires during /chat, the bridge calls permission_check
# which emits a `permission_request` NDJSON event, parks on a per-id queue,
# and waits for the user to POST /api/permission with their decision.
# `_always_allow` / `_always_deny` persist to disk (same file the CLI uses) so
# [a]lways / [N]ever survive restarts and are shared between CLI and GUI.
import queue as _queue
_permission_queues: Dict[str, _queue.Queue] = {}
_PERMS_FILE = os.path.join(WORKSPACE, "permissions.json")


def _load_perms_from_disk():
    allow, deny = set(), set()
    try:
        with open(_PERMS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, v in data.items():
            if v == "always": allow.add(name)
            elif v == "never": deny.add(name)
    except (OSError, ValueError):
        pass
    return allow, deny


def _save_perms_to_disk():
    data = {n: "always" for n in _always_allow}
    data.update({n: "never" for n in _always_deny})
    try:
        os.makedirs(os.path.dirname(_PERMS_FILE), exist_ok=True)
        with open(_PERMS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


_always_allow, _always_deny = _load_perms_from_disk()


def _make_permission_check(emit_fn):
    """Returns a callback the bridge can hand each risky tool call to. Also
    wires up the extend-workspace callback used by tools._resolve_write so
    out-of-workspace writes prompt the user instead of silently raising."""
    def _check(name: str, args: Dict) -> str:
        if name in _always_allow: return "allow"
        if name in _always_deny:  return "deny"
        req_id = f"perm_{int(time.time()*1000)}_{name}"
        _permission_queues[req_id] = _queue.Queue()
        try:
            emit_fn("permission_request", id=req_id, name=name, args=args)
            try:
                decision = _permission_queues[req_id].get(timeout=180.0)
            except _queue.Empty:
                decision = "deny"
        finally:
            _permission_queues.pop(req_id, None)
        if decision == "always": _always_allow.add(name); _save_perms_to_disk(); return "allow"
        if decision == "never":  _always_deny.add(name);  _save_perms_to_disk(); return "deny"
        return decision

    def _extend(path: str) -> bool:
        # Re-uses the same permission queue + emit channel. The GUI keys
        # off name="__extend_workspace__" to render a different prompt
        # ("allow JARVIS to write outside ~/Jarvis?") with the path as args.
        # "always" decision adds the parent dir to EXTRA_WORKSPACES via
        # tools._resolve_write's own logic; we just return True here.
        req_id = f"perm_{int(time.time()*1000)}_extend"
        _permission_queues[req_id] = _queue.Queue()
        try:
            emit_fn("permission_request", id=req_id,
                    name="__extend_workspace__",
                    args={"path": path,
                          "parent": os.path.dirname(path) or path})
            try:
                decision = _permission_queues[req_id].get(timeout=180.0)
            except _queue.Empty:
                decision = "deny"
        finally:
            _permission_queues.pop(req_id, None)
        return decision in ("allow", "always")

    try:
        from . import tools as _t
        _t.set_extend_workspace_callback(_extend)
    except Exception:
        pass
    return _check


def _resolve_permission(req_id: str, decision: str) -> bool:
    q = _permission_queues.get(req_id)
    if q is None:
        return False
    try:
        q.put_nowait(decision)
        return True
    except _queue.Full:
        return False


# ----- ask_user system -----
# Mirrors the permission pattern: the ask_user tool emits an `ask_user_request`
# event, parks on a per-id queue, and waits for the user's POST /api/ask.
_ask_queues: Dict[str, _queue.Queue] = {}


def _make_ask_user_bridge(emit_fn):
    """Return the sync callback hearth.tools._ask_user will invoke. We emit
    an event into the running chat stream, park on a queue, and block the
    worker thread until the user clicks an option in the GUI modal."""
    def _ask(question: str, options: list, allow_other: bool) -> Dict[str, Any]:
        req_id = f"ask_{int(time.time()*1000)}"
        _ask_queues[req_id] = _queue.Queue()
        try:
            emit_fn("ask_user_request", id=req_id, question=question,
                    options=options, allow_other=allow_other)
            try:
                # 180s — same ceiling as permissions; long enough for the user
                # to actually read the question.
                answer = _ask_queues[req_id].get(timeout=180.0)
            except _queue.Empty:
                return {"ok": False, "error": "user did not answer within 3 minutes"}
        finally:
            _ask_queues.pop(req_id, None)
        return answer  # already a dict shaped {ok, choice, other}


    return _ask


def _resolve_ask_user(req_id: str, answer: Dict[str, Any]) -> bool:
    q = _ask_queues.get(req_id)
    if q is None:
        return False
    try:
        q.put_nowait(answer)
        return True
    except _queue.Full:
        return False


def _http_get_json(url: str, timeout: float = 3,
                   headers: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    try:
        req = urllib.request.Request(url, headers=(headers or {}))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _http_post_json(url: str, body: Dict, timeout: float = 30,
                    headers: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    data = json.dumps(body).encode("utf-8")
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _models_probe_endpoint() -> str:
    """Where should the chat-model dropdown look?

    Honest rule: the dropdown shows whatever the CURRENT brain serves. If
    you're on Grok, you see Grok's models (grok-4.3, grok-build-0.1, ...);
    if you're on local LM Studio, you see local GGUFs. Don't silently
    auto-redirect — that confused users into thinking 'I switched to Grok
    but I see Hermes? what's loaded?'.

    The Models tab ALSO shows the local-on-disk models (for switching
    brains) via the separate `disk_models` field in /api/llmserver/status.
    That keeps the two concerns clean:
      - dropdown / topbar = current brain (cloud or local)
      - Models tab "On your machine" list = local GGUFs (always)
    """
    return LOCAL_API_BASE or "http://localhost:1234/v1"


def _list_models() -> List[Dict]:
    """Return model list from whichever endpoint is currently active.

    LM Studio: pull from /api/v0/models (rich metadata).
    Builtin llama-cpp-python server: synthesize a single entry from _proc_info
        (the v1/models endpoint exists but exposes almost nothing useful).
    Anything else (Ollama, cloud, custom): use plain /v1/models and mark loaded.
    Cached 4s either way.
    """
    now = time.time()
    if _models_cache["data"] is not None and now - _models_cache["ts"] < 4:
        return _models_cache["data"]

    out: List[Dict] = []
    # The endpoint we'll actually probe — may differ from LOCAL_API_BASE if
    # the chat is currently routed to a cloud endpoint. See _models_probe_endpoint.
    probe_base = _models_probe_endpoint()

    # 1) Built-in llama-cpp server is the active endpoint?
    # Match on port (not full URL) so we tolerate localhost vs 127.0.0.1
    # being written into LOCAL_API_BASE vs builtin_url after a start. We've
    # been bitten by exact-string matches before.
    try:
        from . import llmserver
        st = llmserver.status(LOCAL_API_BASE)
        builtin_url = st.get("builtin_url") or ""
        same_port = False
        if st.get("builtin_running") and builtin_url:
            try:
                from urllib.parse import urlparse
                same_port = urlparse(LOCAL_API_BASE).port == urlparse(builtin_url).port
            except Exception:
                same_port = (LOCAL_API_BASE == builtin_url)
        if st.get("builtin_running") and same_port:
            path = st.get("builtin_model") or ""
            mid = os.path.basename(path) or "hearth-builtin"
            # Surface the context the builtin server was launched with so
            # the footer `ctx` pill shows the real value instead of staying
            # stuck at "—". `_proc_info["ctx"]` is set in start_builtin.
            from . import llmserver as _ls
            builtin_ctx = (_ls._proc_info or {}).get("ctx")
            out.append({
                "id": mid,
                "type": "llm",
                "arch": "gguf",
                "publisher": "llama.cpp (Hearth built-in)",
                "state": "loaded",
                "loaded_context_length": builtin_ctx,
                "max_context_length": builtin_ctx,
                "quantization": "",
                "capabilities": [],
            })
            _models_cache["ts"] = now
            _models_cache["data"] = out
            return out
    except Exception:
        pass

    # 2) LM Studio v0 (rich metadata path). Pass the Bearer key so an
    # auth-gated builtin/cloud server doesn't 401-storm the log on every
    # GUI poll (this was 5-10 spurious 401s in server-log per second
    # because the GUI polls /api/models every 9s + Hearth's internal probes).
    v0_url = probe_base.replace("/v1", "/api/v0") + "/models"
    _v0_key = os.environ.get("LOCAL_API_KEY") or "hearth-builtin"
    data = _http_get_json(v0_url, headers={"Authorization": f"Bearer {_v0_key}"})
    if data and isinstance(data, dict) and data.get("data"):
        # Cross-reference with /v1/models to verify "loaded" state isn't stale.
        # LM Studio v0 sometimes reports state="loaded" for a model the user
        # has just ejected (it caches per-model state, doesn't auto-clear).
        # The /v1/models endpoint only lists actually-loaded models, so we
        # use that as the truth for liveness.
        live_ids = set()
        try:
            v1_data = _http_get_json(probe_base + "/models",
                                     headers={"Authorization": f"Bearer {_v0_key}"}) or {}
            for lm in v1_data.get("data", []):
                if lm.get("id"):
                    live_ids.add(lm["id"])
        except Exception:
            live_ids = set()
        for m in data.get("data", []):
            if m.get("type") == "embeddings":
                continue
            # Trust v1 over v0 for liveness — kills the "phantom Gemma loaded"
            # bug where v0 still reports state=loaded for an ejected model.
            v0_state = m.get("state")
            actual_state = v0_state if (m.get("id") in live_ids or not live_ids) else ""
            # Forward the model's on-disk path when LM Studio gives it to us
            # so the GUI's topbar dropdown can dedupe against the disk-scan
            # list (LM Studio renames models — "harmonic-hermes-9b" → file
            # "Qwen3.5-9B-Harmonic.Q4_K_M.gguf" — so we can't dedupe by name).
            out.append({
                "id": m.get("id"),
                "type": m.get("type"),
                "arch": m.get("arch"),
                "publisher": m.get("publisher"),
                "state": actual_state,
                "loaded_context_length": m.get("loaded_context_length"),
                "max_context_length": m.get("max_context_length"),
                "quantization": m.get("quantization"),
                "capabilities": m.get("capabilities", []),
                "path": m.get("path") or m.get("modelPath") or "",
            })
        _models_cache["ts"] = now
        _models_cache["data"] = out
        return out

    # 3) Generic OpenAI-compatible /v1/models — assume the first one is loaded.
    # Try with the configured API key so an auth-gated server (builtin /
    # Gemini / etc.) actually answers instead of silently returning empty.
    api_key = os.environ.get("LOCAL_API_KEY") or "hearth-builtin"
    data = _http_get_json(f"{probe_base}/models",
                          headers={"Authorization": f"Bearer {api_key}"}) or {"data": []}
    # Skip non-chat models — xAI lists grok-imagine-image / grok-imagine-video
    # alongside chat models, but they 400 on /v1/chat/completions because
    # they use /v1/images/generations and /v1/videos/generations instead.
    # Hide them from the chat-model picker; they're still callable via the
    # generate_image / generate_video TOOLS once a chat model is selected.
    NON_CHAT_PATTERNS = ("imagine-image", "imagine-video", "imagine-audio",
                         "embedding", "embed-")
    # Collect the chat-capable rows first, then flag the one the user actually
    # picked (settings.llm_model) as "loaded" — NOT just index 0. The old
    # "loaded if i==0" hardcode meant the topbar sticker reverted to the
    # provider's first model on every poll, so a swap to grok-4.3 wouldn't
    # stick. Fall back to the first row only if the saved pick isn't listed.
    try:
        _picked = (_load_settings().get("llm_model") or "").strip().lower()
    except Exception:
        _picked = ""
    rows = []
    for m in data.get("data", []):
        raw_id = m.get("id") or ""
        if any(p in raw_id.lower() for p in NON_CHAT_PATTERNS):
            continue
        # llama_cpp.server identifies models by the FULL --model path. Strip
        # to basename here so the topbar shows "model.gguf" not "C:\Users\...".
        clean_id = os.path.basename(raw_id.replace("\\", "/")) or raw_id
        rows.append((clean_id, m))
    _loaded_idx = 0
    for idx, (cid, _m) in enumerate(rows):
        if _picked and cid.lower() == _picked:
            _loaded_idx = idx
            break
    for idx, (clean_id, m) in enumerate(rows):
        out.append({
            "id": clean_id,
            "type": "llm",
            "arch": "",
            "publisher": m.get("owned_by") or "",
            "state": "loaded" if idx == _loaded_idx else "",
            "loaded_context_length": None,
            "max_context_length": None,
            "quantization": "",
            "capabilities": [],
        })
    _models_cache["ts"] = now
    _models_cache["data"] = out
    return out


def _detect_loaded() -> Optional[Dict]:
    for m in _list_models():
        if m.get("state") == "loaded":
            return m
    return None


def _load_model(model_id: str) -> Dict:
    """Try REST first (POST /api/v0/models/load); fall back to `lms load`.
    Defensive — every branch returns a dict with `ok` and `error` or `via`."""
    # Cloud short-circuit: if the active endpoint is a cloud provider
    # (xai, openai, anthropic, gemini, openrouter), there is no local
    # model to load — the model id rides on each /chat call to the
    # provider. Anything that called us here is misrouted; return ok
    # without calling LM Studio so we don't surface a confusing
    # "lms ls" error.
    try:
        from urllib.parse import urlparse
        _host = (urlparse(_active_base()).hostname or "").lower()
        if _host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0", ""):
            return {"ok": True, "via": "cloud-noop",
                    "note": f"endpoint {_host} is cloud — model '{model_id}' "
                            f"is served by the provider, no local load."}
    except Exception:
        pass
    # 1) REST attempt (LM Studio v0 may or may not expose this)
    rest_resp = _http_post_json(f"{LM_STUDIO_V0}/models/load", {"model": model_id}, timeout=120)
    if isinstance(rest_resp, dict) and not rest_resp.get("error"):
        # Trust REST only if a model actually went into "loaded" state.
        _models_cache["data"] = None
        if _detect_loaded():
            return {"ok": True, "via": "rest", "result": rest_resp}

    # 2) CLI fallback (more reliable on most LM Studio installs)
    lms = _find_lms_cli()
    if not lms:
        rest_err = rest_resp.get("error", "REST returned unexpected payload") if isinstance(rest_resp, dict) else "no REST response"
        return {"ok": False, "error": (
            f"Couldn't load via LM Studio REST ({rest_err}) AND `lms` CLI is not on PATH. "
            f"Install it: open LM Studio → Developer tab → 'install lms'. "
            f"Then restart this app."
        )}
    try:
        proc = subprocess.run(
            [lms, "load", model_id, "--yes"],
            capture_output=True, timeout=240, creationflags=_NO_WINDOW,
        )
        stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            msg = (stderr.strip() or stdout.strip() or f"lms exit {proc.returncode}")
            return {"ok": False, "error": msg}
        _models_cache["data"] = None
        # Wait briefly for the loaded state to appear
        for _ in range(8):
            if _detect_loaded():
                break
            time.sleep(0.5)
        return {"ok": True, "via": "lms-cli", "stdout": stdout.strip()[:400]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "lms load timed out after 240s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _eject_model() -> Dict:
    """Free the currently-loaded model — for real, not just from our list.

    Path 1: Hearth's BUILTIN llama-cpp server is the active endpoint → kill
    the subprocess. llama.cpp releases all VRAM + RAM the instant the
    process exits. Verified by nvidia-smi dropping back to ~0.

    Path 2: LM Studio is the active endpoint → use its REST unload, falling
    back to the `lms unload --all` CLI.
    """
    # Python rule: `global X` must come before ANY reference to X in the
    # function body. Hoist it to the very top so the read on the next line
    # doesn't make Python treat LOCAL_API_BASE as a local.
    global LOCAL_API_BASE
    from . import llmserver
    # Builtin path — if our own server is running, killing the subprocess
    # is the ONLY way to actually free VRAM. The "unload" REST on LM Studio
    # doesn't exist for llama_cpp.server.
    builtin_url = (llmserver._proc_info or {}).get("url") if llmserver._proc is not None else None
    if builtin_url and llmserver._proc.poll() is None and LOCAL_API_BASE == builtin_url:
        result = llmserver.stop_builtin()
        _models_cache["data"] = None
        # Revert endpoint back to the user's configured default so the next
        # message doesn't hit a dead URL.
        from . import headless as _hl
        settings = _load_settings()
        saved_url = (settings.get("llm_url") or "").strip()
        new_base = saved_url or "http://localhost:1234/v1"
        LOCAL_API_BASE = new_base
        _hl.LOCAL_API_BASE = new_base
        os.environ["LOCAL_API_BASE"] = new_base
        return {"ok": bool(result.get("ok")), "via": "builtin-stop",
                "freed_vram": True, **result}

    loaded = _detect_loaded()
    if not loaded:
        return {"ok": True, "msg": "nothing loaded"}
    mid = loaded["id"]
    r = _http_post_json(f"{LM_STUDIO_V0}/models/unload", {"model": mid}, timeout=60)
    if r and not r.get("error"):
        _models_cache["data"] = None
        return {"ok": True, "via": "rest"}
    lms = _find_lms_cli()
    if not lms:
        return {"ok": False, "error": "no REST + no `lms` CLI"}
    try:
        proc = subprocess.run([lms, "unload", "--all"],
                              capture_output=True, text=True, timeout=60,
                              creationflags=_NO_WINDOW)
        _models_cache["data"] = None
        return {"ok": proc.returncode == 0, "via": "lms-cli",
                "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _find_lms_cli() -> Optional[str]:
    """Locate the `lms` CLI executable on PATH (Windows uses `lms.exe`)."""
    for name in ("lms.exe", "lms"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    # LM Studio's default install location
    candidates = [
        os.path.expanduser("~/.cache/lm-studio/bin/lms.exe"),
        os.path.expanduser("~/.cache/lm-studio/bin/lms"),
        os.path.expanduser("~/.lmstudio/bin/lms.exe"),
        os.path.expanduser("~/.lmstudio/bin/lms"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _gpu_snapshot() -> Dict:
    now = time.time()
    if _gpu_cache["data"] is not None and now - _gpu_cache["ts"] < 2:
        return _gpu_cache["data"]
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW,
        )
        if r.returncode != 0:
            data = {"available": False, "error": "nvidia-smi failed"}
        else:
            gpus = []
            for line in r.stdout.strip().splitlines():
                bits = [p.strip() for p in line.split(",")]
                if len(bits) >= 5:
                    gpus.append({
                        "name": bits[0],
                        "util_pct": int(bits[1]),
                        "mem_used_mb": int(bits[2]),
                        "mem_total_mb": int(bits[3]),
                        "temp_c": int(bits[4]),
                    })
            data = {"available": True, "gpus": gpus}
    except FileNotFoundError:
        data = {"available": False, "error": "nvidia-smi not on PATH"}
    except Exception as e:
        data = {"available": False, "error": f"{type(e).__name__}: {e}"}
    _gpu_cache["ts"] = now
    _gpu_cache["data"] = data
    return data


def _memory_index() -> List[Dict]:
    """Walk ~/Jarvis/memory/*.md and return [{name, description, type}, ...]."""
    out: List[Dict] = []
    mem_dir = memory.MEM_DIR
    if not os.path.isdir(mem_dir):
        return out
    for fn in sorted(os.listdir(mem_dir)):
        if not fn.endswith(".md") or fn == "MEMORY.md":
            continue
        path = os.path.join(mem_dir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()[:15]
        except OSError:
            continue
        name, desc, typ, sub_cat = fn[:-3], "", "", ""
        in_fm = False
        for ln in lines:
            s = ln.strip()
            if s == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if in_fm:
                if s.startswith("name:"):
                    name = s.split(":", 1)[1].strip()
                elif s.startswith("description:"):
                    desc = s.split(":", 1)[1].strip()
                elif s.startswith("type:"):
                    typ = s.split(":", 1)[1].strip()
                elif s.startswith("sub_category:"):
                    sub_cat = s.split(":", 1)[1].strip()
        # Fallback for old memories that haven't been migrated yet — classify
        # on the fly so the GUI tree never has orphans. Cheap (regex only).
        if not sub_cat:
            try:
                from .memory_classify import classify_or_default
                sub_cat = classify_or_default(typ or "user", desc)
            except Exception:
                sub_cat = ""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        out.append({"name": name, "description": desc, "type": typ,
                    "sub_category": sub_cat,
                    "path": path, "mtime": mtime})
    out.sort(key=lambda x: -x["mtime"])
    return out


def _load_settings() -> Dict:
    defaults = {
        "think_default": False,
        "auto_load_model": True,
        "voice_tts": False,
        "voice_stt": False,
        "stt_device": "cpu",   # cpu / cuda
        "tts_device": "cpu",   # cpu / cuda / dml
        "voice_speed": 1.5,    # Kokoro playback rate (0.5x-2.5x). 1.5 is the snappier default that doesn't sound like a turtle.
        "voice_name": "am_michael",  # Kokoro voice id (am_/af_/bm_/bf_ + name). am_michael = Jarvis-leaning baseline.
        "agent_name": "JARVIS",      # User-renameable persona. Settings → Behavior. Drives chat avatar letter (first char), assistant label, persona prompt, AND workspace folder (~/<agent_name>/). Rename rules in /api/agent/rename.
        "stt_model": "base.en",  # base.en / small.en / medium.en
        "theme": "warm-flame",
        "preferred_model": "",
        "llm_provider": "local",
        "llm_url": "",
        "llm_key": "",
        "llm_model": "",
        # Built-in server config exposed via the Settings → Server pane.
        # Apply on the next start_builtin (settings persist; restart to act).
        "server_autoboot": True,        # auto-load preferred_model on Hearth launch
        "server_default_ctx": 24576,    # 24K. Persona+tools = ~18K; needs headroom for chat+output. 16K was tight, 8K silently OOM'd.
        "keep_local_warm": False,       # when switching to cloud, keep the local builtin loaded? Off = free VRAM (default). On = leave it warm so quick swap-back doesn't reload (~10s tax). Power-user toggle.
        # ---- Toast quiet-mode toggles (user-controlled noise level) ----
        "toast_facts_saved": True,      # "Remembered N facts" on auto-extract
        "toast_compacted":   True,      # "Context compacted, now X%"
        "toast_errors":      True,      # Hard errors (always recommended)
        "server_idle_min": 15,          # auto-unload after N idle minutes (0 = never)
        "server_port": 1234,            # builtin HTTP port
        # Forge / SD-WebUI install path for local image gen. "" = either
        # not installed or use whatever _autodetect_forge_dir picks. Set
        # via Settings → Behavior or the Detect button.
        "forge_dir": "",
    }
    if not os.path.isfile(SETTINGS_PATH):
        return defaults
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        defaults.update(saved)
    except Exception:
        pass
    # Apply device prefs as env vars BEFORE models load
    os.environ["JARVIS_STT_DEVICE"] = defaults.get("stt_device", "cpu")
    os.environ["JARVIS_STT_MODEL"]  = defaults.get("stt_model",  "base.en")
    os.environ["JARVIS_TTS_DEVICE"] = defaults.get("tts_device", "cpu")
    os.environ["JARVIS_VOICE_SPEED"] = str(defaults.get("voice_speed", 1.5))
    os.environ["JARVIS_VOICE"]      = defaults.get("voice_name", "am_michael")
    # Agent name → persona module reads this at import-time via
    # HEARTH_PERSONA_NAME. Setting it here means the very next chat turn
    # picks up the user's renamed agent. On boot, this fires before any
    # chat call so the persona is correct from turn 1.
    os.environ["HEARTH_PERSONA_NAME"] = (defaults.get("agent_name") or "JARVIS").strip() or "JARVIS"
    return defaults


def _save_settings(d: Dict) -> Dict:
    cur = _load_settings()
    # Detect a cloud→local or local→cloud brain switch BEFORE writing — the
    # disk-model cache populated under cloud is shorter (cached scan) and
    # needs a fresh rescan when the user goes back to local. Don't block
    # the settings save; kick the rescan async + report it back via status.
    _prev_provider = (cur.get("llm_provider") or "").strip().lower()
    _new_provider = (d.get("llm_provider") or _prev_provider or "").strip().lower()
    _CLOUD = {"grok", "xai", "gemini", "google", "openai", "anthropic",
              "openrouter", "custom"}
    _was_cloud = _prev_provider in _CLOUD
    _is_cloud_now = _new_provider in _CLOUD
    cur.update({k: v for k, v in d.items() if v is not None})
    os.makedirs(os.path.dirname(SETTINGS_PATH) or WORKSPACE, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
    # Brain switched cloud → local: ditch the cached disk_models and rescan
    # in the background so the Models tab reflects what's actually on disk
    # without making the user think the GUI froze.
    if _was_cloud and not _is_cloud_now:
        try:
            from . import llmserver as _ls
            _ls.force_local_rescan()
        except Exception:
            pass
    # Re-apply env vars + reload voice/listen so new device picks effect
    os.environ["JARVIS_STT_DEVICE"] = cur.get("stt_device", "cpu")
    os.environ["JARVIS_STT_MODEL"]  = cur.get("stt_model",  "base.en")
    os.environ["JARVIS_TTS_DEVICE"] = cur.get("tts_device", "cpu")
    os.environ["JARVIS_VOICE_SPEED"] = str(cur.get("voice_speed", 1.5))
    os.environ["JARVIS_VOICE"]      = cur.get("voice_name", "am_michael")
    # Keep the runtime chat model in sync with the saved pick. Without this a
    # cloud model swap (grok-4.20 -> grok-4.3) saved to disk but the next
    # /chat + the status poller still used the old LOCAL_MODEL, so the topbar
    # reverted. Empty llm_model (local probe-picks) leaves LOCAL_MODEL unset.
    _picked_model = (cur.get("llm_model") or "").strip()
    if _picked_model:
        os.environ["LOCAL_MODEL"] = _picked_model
    # Agent rename: hot-update the persona module's NAME constant so the
    # very next chat turn uses the new name in system prompt, signatures,
    # and tone rules — no restart needed. Folder rename happens via a
    # separate /api/agent/rename endpoint (it's slow + interrupts the tray).
    new_agent_name = (cur.get("agent_name") or "JARVIS").strip() or "JARVIS"
    os.environ["HEARTH_PERSONA_NAME"] = new_agent_name
    try:
        from . import persona as _persona
        _persona.NAME = new_agent_name
    except Exception:
        pass
    if _voice:
        # Apply speed live (no engine reload needed — it's a per-call param
        # the speak() handler reads from DEFAULT_SPEED). The voice id picks
        # up on the next speak() call too.
        try:
            _voice.set_speed(float(cur.get("voice_speed", 1.5)))
        except Exception:
            pass
        try:
            _voice.set_default_voice(str(cur.get("voice_name", "am_michael")))
        except Exception:
            pass
        try: _voice.reload()
        except Exception: pass
    return cur


def _list_files(rel_path: str = "") -> Dict:
    """List files in workspace/<rel_path>. Safe — never escapes workspace."""
    rel = (rel_path or "").strip().strip("/").strip("\\")
    base = os.path.normpath(os.path.join(WORKSPACE, rel))
    if not base.startswith(WORKSPACE):
        return {"error": "path escapes workspace"}
    if not os.path.isdir(base):
        return {"error": f"not a directory: {rel}"}
    out: List[Dict] = []
    for entry in sorted(os.listdir(base)):
        p = os.path.join(base, entry)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({
            "name": entry,
            "is_dir": os.path.isdir(p),
            "size": st.st_size,
            "mtime": st.st_mtime,
        })
    out.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {"path": rel, "abs": base, "entries": out}


def _tail_log(n: int = 100) -> List[Dict]:
    log_path = os.path.join(LOGS_DIR, "activity.jsonl")
    if not os.path.isfile(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-max(1, min(n, 1000)):]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class HearthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet — only print errors
        if args and isinstance(args[1], str) and args[1].startswith(("4", "5")):
            sys.stderr.write(f"[hearth.web] {self.address_string()} - {fmt % args}\n")

    # -------- helpers --------

    def _send_json(self, code: int, body: Any) -> None:
        payload = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            # Browser tab closed / refresh / fast-clicked. Normal noise; the
            # default BaseHTTPRequestHandler trace was scaring users. Silenced.
            pass

    def _read_json(self) -> Dict:
        length = int(self.headers.get("content-length", "0") or "0")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _query(self) -> Dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        return {k: v[0] for k, v in q.items()}

    # -------- routing --------

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html", "/ui"):
            return self._serve_ui()
        if path.startswith("/assets/"):
            return self._serve_asset(path[len("/assets/"):])
        if path == "/api/state":
            return self._send_state()
        if path == "/api/context-budget":
            # Live read of the current brain's context window so the GUI
            # ring + bottom bar update instantly on provider swap (don't
            # have to wait for the next chat turn to fire context_budget
            # SSE events). Same helper the chat path uses, so they agree.
            try:
                from . import headless as _hl
                # Sync helper's view of base/model to whatever GUI has set.
                s = _load_settings()
                model = (s.get("llm_model") or "").strip()
                tokens, source = _hl.resolve_context_tokens(model)
                # Tool-schema overhead — same math as the chat path.
                try:
                    tool_tokens = len(json.dumps(TOOL_DEFINITIONS)) // 4
                except Exception:
                    tool_tokens = 0
                effective = max(2048, tokens - tool_tokens)
                return self._send_json(200, {
                    "total": tokens, "tools": tool_tokens,
                    "effective": effective, "source": source,
                    "model": model,
                })
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/conversations":
            return self._send_json(200, {"conversations": _list_convos()})
        if path.startswith("/api/conversations/"):
            cid = urllib.parse.unquote(path[len("/api/conversations/"):])
            data = _load_convo(cid)
            if data is None:
                return self._send_json(404, {"error": "not found"})
            return self._send_json(200, data)
        if path == "/api/models":
            return self._send_json(200, {"models": _list_models()})
        if path == "/api/mcp/config":
            # Outbound MCP config — pasted from a standard mcp.json. Stored
            # at ~/Jarvis/mcp.json. Empty file → empty config (valid).
            mcp_path = os.path.join(WORKSPACE, "mcp.json")
            try:
                if os.path.isfile(mcp_path):
                    with open(mcp_path, "r", encoding="utf-8") as f:
                        body = f.read()
                else:
                    body = ""
                return self._send_json(200, {"json": body, "path": mcp_path})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/forge/detect":
            try:
                from . import tools as _t
                detected = _t._autodetect_forge_dir()
                return self._send_json(200, {"path": detected or ""})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/reminders":
            try:
                from . import reminders as _r
                items = _r.list_reminders(include_fired=False)
                return self._send_json(200, {"items": items})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/migrate/probe":
            # Report which sources have data on disk so the UI can grey out
            # buttons for ones that aren't installed. Cheap stat-only call.
            try:
                from . import migrate as _m
                hermes_home = _m._hermes_home()
                hermes_mem = _m._hermes_active_memory_dir(hermes_home)
                openclaw_ws = _m._openclaw_workspace_dir()
                return self._send_json(200, {
                    "hermes": {
                        "home": str(hermes_home),
                        "found": (hermes_mem / "USER.md").is_file()
                              or (hermes_mem / "MEMORY.md").is_file(),
                    },
                    "openclaw": {
                        "workspace": str(openclaw_ws),
                        "found": (openclaw_ws / "MEMORY.md").is_file()
                              or (openclaw_ws / "memory").is_dir(),
                    },
                })
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/mcp/status":
            # Live bridge status. Each row reports actual subprocess state
            # ('starting' / 'connected' / 'error'), tool count, and uptime.
            try:
                from . import mcp_client
                return self._send_json(200,
                    {"bridges": mcp_client.list_bridges()})
            except Exception as e:
                return self._send_json(500,
                    {"bridges": [{"name": "(client error)", "state": "error",
                                  "error": f"{type(e).__name__}: {e}"}]})
        if path == "/api/memory":
            return self._send_json(200, {"memories": _memory_index()})
        if path.startswith("/api/memory/"):
            name = urllib.parse.unquote(path[len("/api/memory/"):])
            return self._send_memory_one(name)
        if path == "/api/files":
            q = self._query()
            return self._send_json(200, _list_files(q.get("path", "")))
        if path == "/api/file":
            return self._send_file_op()
        if path == "/file":
            # Binary file serve for inline media render in the chat (generated
            # images, videos). Restricted to WORKSPACE — so a malicious chat
            # response can't `<img src="/file?path=C:/Windows/...">` and exfil.
            return self._serve_binary_file()
        if path == "/api/logs":
            q = self._query()
            try:
                n = int(q.get("lines", "100"))
            except ValueError:
                n = 100
            return self._send_json(200, {"events": _tail_log(n)})
        if path == "/api/logs/download":
            return self._download_logs()
        if path == "/api/logs/server":
            # Tail the llama_cpp.server stdout/stderr log so the GUI Logs tab
            # can show what the builtin server is doing in real time (model
            # load progress, request lines, OOM tracebacks). When the server
            # has never been started in this session the file may not exist —
            # return an honest empty result instead of 500'ing.
            q = self._query()
            try:
                tail_kb = max(1, min(256, int(q.get("kb", "32"))))
            except ValueError:
                tail_kb = 32
            log_path = os.path.join(WORKSPACE, "logs", "llamaserver.log")
            text = ""
            running = False
            pid = None
            try:
                from . import llmserver as _ls
                running = _ls._proc is not None and _ls._proc.poll() is None
                if running and _ls._proc:
                    pid = _ls._proc.pid
            except Exception:
                pass
            try:
                if os.path.exists(log_path):
                    with open(log_path, "rb") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(0, size - tail_kb * 1024))
                        text = f.read().decode("utf-8", errors="replace")
                    # If we landed mid-line, trim the first partial line.
                    if size > tail_kb * 1024 and "\n" in text:
                        text = text[text.index("\n") + 1:]
            except OSError as e:
                text = f"[hearth] couldn't read {log_path}: {e}"
            return self._send_json(200, {
                "log_path": log_path,
                "exists": os.path.exists(log_path),
                "running": running,
                "pid": pid,
                "text": text,
            })
        if path == "/api/voice/status":
            tts_status = {"available": False, "reason": "voice module missing"}
            stt_status = {"ready": False, "reason": "listen module missing"}
            rt_status = {"available": False}
            if _voice:
                try:
                    tts_status = _voice.status()
                except Exception as e:
                    tts_status = {"available": False, "reason": f"{type(e).__name__}: {e}"}
            if _listen:
                try:
                    stt_status = _listen.status()
                except Exception as e:
                    stt_status = {"ready": False, "reason": f"{type(e).__name__}: {e}"}
            if _rt_voice:
                try:
                    rt_status = _rt_voice.status()
                except Exception as e:
                    rt_status = {"available": False, "reason": f"{type(e).__name__}: {e}"}
            return self._send_json(200, {"tts": tts_status, "stt": stt_status, "realtime": rt_status})
        if path == "/api/voice/realtime/stream":
            return self._realtime_stream()
        if path == "/api/permissions":
            return self._send_json(200, {
                "always_allow": sorted(_always_allow),
                "always_deny":  sorted(_always_deny),
                "pending":      list(_permission_queues.keys()),
            })
        if path == "/api/settings":
            return self._send_json(200, _load_settings())
        if path == "/api/gpu":
            return self._send_json(200, _gpu_snapshot())
        if path == "/api/tools":
            return self._send_json(200, {"tools": TOOL_DEFINITIONS})
        if path == "/api/persona":
            return self._send_json(200, {"system_prompt": system_prompt()})
        if path == "/api/llmserver/status":
            from . import llmserver
            return self._send_json(200, llmserver.status(LOCAL_API_BASE))
        if path == "/api/subagent/pending":
            # Idle-poll surface for the GUI: returns pending background
            # subagent completions WITHOUT draining the queue.
            try:
                from . import subagents as _sa
                return self._send_json(200, {
                    "pending": _sa.peek_pending_notifications(),
                })
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        if path == "/api/subagent/activity":
            # Recent subagent runs (from ~/Jarvis/subagents/*.jsonl) for
            # the Logs tab's "agents" subview. Newest first, capped.
            try:
                from . import subagents as _sa
                lim = int(self._query().get("limit", "50") or 50)
                return self._send_json(200, {
                    "agents": _sa.list_subagent_activity(limit=lim),
                })
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        if path == "/api/subagent/transcript":
            # Full JSONL transcript for one agent — for the Logs detail view.
            try:
                q = self._query()
                aid = q.get("agent_id", "").strip()
                from . import subagents as _sa
                tpath = _sa._transcript_path(aid) if aid else None
                if tpath and tpath.is_file():
                    return self._send_json(200, {
                        "agent_id": aid,
                        "content": tpath.read_text(encoding="utf-8")[:200_000],
                    })
                return self._send_json(404, {"error": "no such transcript"})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        if path == "/api/llmserver/rescan":
            # Manual trigger for the user to force a fresh local-model sweep
            # — e.g. after dropping a new GGUF into ~/Jarvis/models/. Returns
            # immediately; status() reports `scanning: true` until done.
            from . import llmserver
            return self._send_json(200, llmserver.force_local_rescan())
        if path == "/api/llmserver/progress":
            # Live load-progress snapshot for the GUI's progress bar in both
            # the Models modal AND the bottom status pill. Returns the parsed
            # llama_cpp.server phase + a 0..100 percent so the user sees
            # "loading_weights 45%" instead of an indeterminate spinner.
            from . import llmserver
            return self._send_json(200, llmserver.get_load_progress())
        if path == "/api/llmserver/log":
            log_path = os.path.join(WORKSPACE, "logs", "llamaserver.log")
            try:
                if os.path.exists(log_path):
                    with open(log_path, "rb") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(0, size - 8000))
                        tail = f.read().decode("utf-8", errors="replace")
                    return self._send_json(200, {"path": log_path, "tail": tail})
                return self._send_json(200, {"path": log_path, "tail": ""})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/llmserver/hf-search":
            # ?q=qwen returns HF GGUF results
            from . import llmserver
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            return self._send_json(200, {"results": llmserver.search_huggingface(q)})
        if path == "/api/llmserver/hf-files":
            # ?repo=user/model lists the .gguf files in that repo
            from . import llmserver
            repo = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("repo", [""])[0]
            return self._send_json(200, {"files": llmserver.list_hf_files(repo)})
        self.send_error(404, "not found")

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/memory/"):
            name = urllib.parse.unquote(path[len("/api/memory/"):])
            return self._delete_memory(name)
        if path.startswith("/api/conversations/"):
            cid = urllib.parse.unquote(path[len("/api/conversations/"):])
            ok = _delete_convo(cid)
            return self._send_json(200 if ok else 404, {"ok": ok})
        self.send_error(404, "not found")

    def do_POST(self) -> None:
        # Hoisted: the brain-switch branch reassigns LOCAL_API_BASE, and other
        # branches (e.g. /api/memory/import) read it — so the global must be
        # declared before any use in this function, not mid-body.
        global LOCAL_API_BASE
        path = urllib.parse.urlparse(self.path).path
        if path == "/chat":
            return self._stream_chat()
        if path == "/api/cancel":
            _CANCEL.set()  # run_once checks this between turns and bails out
            return self._send_json(200, {"cancelled": True})
        if path == "/api/file/reveal":
            # Open the OS file explorer with the given file selected. Safety:
            # same workspace-sandbox check as /file so a chat-injected path
            # can't pop arbitrary Explorer windows. Windows uses
            # `explorer /select,<path>`, mac uses `open -R`, linux uses
            # whatever xdg-open does for the parent dir (no select equivalent).
            return self._reveal_in_folder()
        if path == "/api/agent/rename":
            return self._rename_agent()
        if path == "/api/models/load":
            body = self._read_json()
            mid = (body.get("id") or "").strip()
            if not mid:
                return self._send_json(400, {"error": "missing id"})
            try:
                from urllib.parse import urlparse
                _host = (urlparse(_active_base()).hostname or "").lower()
                if _host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0", ""):
                    return self._send_json(200, {
                        "ok": True, "via": "cloud-noop",
                        "note": f"endpoint {_host} is cloud — no local load needed; the model id is sent on each /chat call.",
                    })
            except Exception:
                pass
            return self._send_json(200, _load_model(mid))
        if path == "/api/models/eject":
            return self._send_json(200, _eject_model())
        if path == "/api/settings":
            return self._send_json(200, _save_settings(self._read_json()))
        if path == "/api/migrate/run":
            body = self._read_json()
            source = (body.get("source") or "").strip().lower()
            if source not in ("hermes", "openclaw", "md"):
                return self._send_json(400, {"error": "source must be hermes, openclaw, or md"})
            apply_ = bool(body.get("apply"))
            include_skills = bool(body.get("include_skills"))
            include_config = bool(body.get("include_config"))
            override_path = (body.get("path") or "").strip() or None
            # Migrator's main() reads sys.argv; build the same arg vector
            # and capture stdout so the UI gets the same dry-run text the
            # CLI sees. Runs synchronously; for typical Hermes/OpenClaw
            # imports this is well under a second.
            import io as _io, contextlib as _ctx
            argv = ["--from", source]
            if apply_:           argv.append("--apply")
            if include_skills:   argv.append("--include-skills")
            if include_config:   argv.append("--include-config")
            if override_path:    argv += ["--path", override_path]
            buf = _io.StringIO()
            old_argv = sys.argv
            sys.argv = ["hearth.migrate"] + argv
            try:
                from . import migrate as _migrate
                with _ctx.redirect_stdout(buf):
                    rc = _migrate.main()
            except SystemExit as e:
                rc = int(getattr(e, "code", 0) or 0)
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
            finally:
                sys.argv = old_argv
            return self._send_json(200, {
                "ok": rc == 0, "rc": rc, "log": buf.getvalue(),
            })
        if path == "/api/memory/import":
            # Paste a memory dump from ChatGPT/Claude/any AI → run it through the
            # same fact extractor (with dedup) the CLI's /import-memory uses, so
            # the GUI has parity. Synchronous; one short LLM call.
            body = self._read_json()
            text = (body.get("text") or "").strip()
            if not text:
                return self._send_json(400, {"ok": False, "error": "no text"})
            try:
                from . import memory_extract as _mx
                import openai as _oai
                _key = os.environ.get("LOCAL_API_KEY") or "hearth-builtin"
                # Resolve the active model: saved pick → env → loaded probe.
                _model = (_load_settings().get("llm_model") or "").strip() \
                    or os.environ.get("LOCAL_MODEL", "").strip()
                if not _model:
                    try:
                        import urllib.request as _ur
                        _req = _ur.Request(
                            f"{LM_STUDIO_V0}/models",
                            headers={"Authorization": f"Bearer {_key}"})
                        with _ur.urlopen(_req, timeout=3) as r:
                            for m in json.loads(r.read().decode()).get("data", []):
                                if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm"):
                                    _model = m.get("id"); break
                    except Exception:
                        pass
                if not _model:
                    return self._send_json(200, {
                        "ok": False,
                        "error": "no model loaded — load a model first, then import."})
                _sync = _oai.OpenAI(api_key=_key, base_url=LOCAL_API_BASE)
                _llm = _mx.make_openai_llm_call(_sync, _model, max_tokens=900)
                _msgs = [{"role": "user",
                          "content": "Here is everything another AI remembered about me — "
                                     "save the durable facts:\n\n" + text}]
                saved, _warns = _mx.extract_and_save(_msgs, _llm, recent_turns=1)
                return self._send_json(200, {
                    "ok": True,
                    "saved": [f.get("title", "") for f in (saved or [])],
                    "count": len(saved or []),
                })
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/voice/device":
            # One-click STT device flip (the onboarding "switch to GPU" CTA).
            # Flips it live + eager-loads so a missing-CUDA failure surfaces now
            # rather than on the first /listen. Full persistence happens when the
            # user saves Settings; this is the immediate in-session switch.
            body = self._read_json()
            dev = (body.get("device") or "").strip().lower()
            if dev not in ("cpu", "cuda"):
                return self._send_json(400, {"ok": False, "error": "device must be cpu or cuda"})
            try:
                from . import listen as _listen
                if dev == "cuda" and not _listen.cuda_available():
                    return self._send_json(200, {
                        "ok": False, "device": _listen.DEVICE,
                        "error": "No usable CUDA device — the GPU build of "
                                 "ctranslate2 isn't installed. Staying on CPU."})
                _listen.set_device(dev)
                loaded = _listen._try_load_model() is not None
                return self._send_json(200, {
                    "ok": loaded, "device": _listen.DEVICE,
                    "error": _listen._last_load_error})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/reminders/snooze":
            try:
                from . import reminders as _r
                body = self._read_json()
                rid = (body.get("id") or "").strip()
                mins = int(body.get("minutes", 10))
                if not rid:
                    return self._send_json(400, {"ok": False, "error": "missing id"})
                return self._send_json(200, _r.snooze_reminder(rid, mins))
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/reminders/cancel":
            try:
                from . import reminders as _r
                body = self._read_json()
                rid = (body.get("id") or "").strip()
                if not rid:
                    return self._send_json(400, {"ok": False, "error": "missing id"})
                ok = _r.cancel_reminder(rid)
                return self._send_json(200, {"ok": ok, "id": rid})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/mcp/config":
            body = self._read_json()
            txt = (body.get("json") or "").strip()
            if txt:
                try:
                    json.loads(txt)
                except Exception as e:
                    return self._send_json(400, {"ok": False, "error": f"JSON: {e}"})
            mcp_path = os.path.join(WORKSPACE, "mcp.json")
            try:
                os.makedirs(os.path.dirname(mcp_path) or WORKSPACE, exist_ok=True)
                with open(mcp_path, "w", encoding="utf-8") as f:
                    f.write(txt)
                return self._send_json(200, {"ok": True, "path": mcp_path})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/conversations":
            data = self._read_json()
            if not data.get("id"):
                return self._send_json(400, {"error": "missing id"})
            ok = _save_convo(data)
            return self._send_json(200 if ok else 500, {"ok": ok})
        if path == "/api/logs/clear":
            # Clears AGENT log (activity.jsonl). The Logs view's clear button
            # passes which=server when the dropdown is showing server logs,
            # so the button no longer cross-wires onto the wrong file.
            q = self._query()
            which = (q.get("which") or "agent").lower()
            if which == "server":
                log_path = os.path.join(WORKSPACE, "logs", "llamaserver.log")
                bak_prefix = "llamaserver"
                bak_ext = "log"
            else:
                log_path = os.path.join(LOGS_DIR, "activity.jsonl")
                bak_prefix = "activity"
                bak_ext = "jsonl"
            try:
                if os.path.isfile(log_path):
                    bak = os.path.join(os.path.dirname(log_path),
                                       f"{bak_prefix}.{int(time.time())}.{bak_ext}.bak")
                    os.rename(log_path, bak)
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("")
                return self._send_json(200, {"ok": True, "cleared": which})
            except OSError as e:
                return self._send_json(500, {"error": str(e)})
        if path == "/api/upload":
            return self._upload()
        if path == "/api/run_tool":
            # Direct tool invocation from the UI — for "summarize this file" buttons etc.
            body = self._read_json()
            tname = (body.get("tool") or "").strip()
            targs = body.get("args") or {}
            if not tname:
                return self._send_json(400, {"error": "missing tool"})
            try:
                result = execute_tool(tname, targs)
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
            return self._send_json(200, {"result": result})
        if path == "/api/tts":
            return self._tts()
        if path == "/api/stt":
            return self._stt()
        if path == "/api/title":
            return self._title()
        if path == "/api/voice/reset":
            # Called by the GUI at the start of a fresh listening cycle so the
            # abort flag set by the previous barge-in / stop() doesn't keep
            # silencing TTS on the next turn.
            try:
                _voice.reset_abort()
            except Exception:
                pass
            return self._send_json(200, {"ok": True})
        if path == "/api/voice/reload":
            # Apply new TTS/STT device or whisper size live. Whisper downloads
            # the new model on first transcribe if missing - we kick that off
            # here so the user doesn't have to wait at the first /listen.
            settings = _load_settings()
            tts_dev = settings.get("tts_device", "cpu")
            stt_dev = settings.get("stt_device", "cpu")
            stt_mdl = settings.get("stt_model", "base.en")
            os.environ["JARVIS_TTS_DEVICE"] = tts_dev
            os.environ["JARVIS_STT_DEVICE"] = stt_dev
            os.environ["JARVIS_STT_MODEL"]  = stt_mdl
            details = {}
            if _voice:
                try:
                    _voice.reload(); details["tts"] = "reloaded"
                except Exception as e:
                    details["tts"] = f"{type(e).__name__}: {e}"
            if _listen:
                try:
                    _listen.set_model(stt_mdl)
                    # set_device flips the in-process DEVICE constant + env
                    # AND clears the cached WhisperModel so the next load
                    # actually uses the new device. Without this, the env
                    # changes but the model stays pinned to whatever it
                    # loaded on at import.
                    _listen.set_device(stt_dev)
                    # Eagerly load so the download happens NOW, not on first /listen
                    _listen._try_load_model()
                    details["stt"] = f"whisper {stt_mdl} on {stt_dev}"
                except Exception as e:
                    details["stt"] = f"{type(e).__name__}: {e}"
            # Realtime voice recorder: kill so it rebuilds with new model next time
            if _rt_voice:
                try:
                    _rt_voice.stop_continuous()
                    _rt_voice._recorder = None
                    details["realtime"] = "will rebuild on next start"
                except Exception as e:
                    details["realtime"] = f"{type(e).__name__}: {e}"
            return self._send_json(200, {"ok": True, "details": details})
        if path == "/api/voice/realtime/stop":
            if _rt_voice:
                try:
                    _rt_voice.stop_continuous()
                except Exception:
                    pass
            try:
                _rt_event_queue.put({"type": "stopped"})
            except Exception:
                pass
            return self._send_json(200, {"ok": True})
        if path == "/api/focus":
            # Single-instance hook — another launch attempt asked us to surface
            # the existing window instead of spawning a duplicate.
            return self._send_json(200, _focus_window())
        if path == "/api/open-rules":
            rules_path = os.path.join(WORKSPACE, "rules.md")
            try:
                if not os.path.exists(rules_path):
                    with open(rules_path, "w", encoding="utf-8") as f:
                        f.write(
                            "# Your house rules for Jarvis\n\n"
                            "This file is re-read every turn. Add anything Jarvis should always do\n"
                            "or never do.\n"
                        )
                os.startfile(rules_path)  # type: ignore[attr-defined]
                return self._send_json(200, {"ok": True, "path": rules_path})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/voice/stop":
            if _voice:
                try:
                    _voice.stop()
                except Exception:
                    pass
            return self._send_json(200, {"ok": True})
        if path == "/api/permission":
            body = self._read_json()
            rid = (body.get("id") or "").strip()
            dec = (body.get("decision") or "").strip()
            if dec not in ("allow", "deny", "always", "never"):
                return self._send_json(400, {"error": "decision must be allow/deny/always/never"})
            ok = _resolve_permission(rid, dec)
            return self._send_json(200 if ok else 404, {"ok": ok})
        if path == "/api/permission/clear":
            # User wants to reset always_allow / always_deny — also wipe disk so
            # they don't come back on restart.
            _always_allow.clear()
            _always_deny.clear()
            _save_perms_to_disk()
            return self._send_json(200, {"ok": True})
        if path == "/api/ask":
            # Response to an ask_user_request event. Body shape:
            # { id, choice, other? }. `other=true` means the user picked the
            # free-text Other option and `choice` is their typed reply.
            body = self._read_json()
            rid = (body.get("id") or "").strip()
            choice = (body.get("choice") or "").strip()
            other = bool(body.get("other"))
            if not rid or not choice:
                return self._send_json(400, {"error": "id and choice are required"})
            ok = _resolve_ask_user(rid, {"ok": True, "choice": choice, "other": other})
            return self._send_json(200 if ok else 404, {"ok": ok})
        if path == "/api/llm-endpoint":
            # Switch the active LLM endpoint at runtime (e.g. local <-> Gemini).
            # Mutates the module-level constants in both web.py and headless.py
            # so the very next /chat call uses the new server. Persists to
            # ~/Jarvis/settings.json so the choice survives restart.
            from . import headless as _hl
            body = self._read_json()
            url = (body.get("url") or "").strip()
            key = (body.get("key") or "").strip()
            model = (body.get("model") or "").strip()
            provider = (body.get("provider") or "").strip()
            if not url:
                return self._send_json(400, {"ok": False, "error": "url required"})
            LOCAL_API_BASE = url
            _hl.LOCAL_API_BASE = url
            if key:
                _hl.LOCAL_API_KEY = key
            else:
                # No key supplied: keep harmless default so local servers still work
                _hl.LOCAL_API_KEY = "not-needed"
            os.environ["LOCAL_API_BASE"] = url
            if key:
                os.environ["LOCAL_API_KEY"] = key
            else:
                # When switching to cloud / non-builtin, drop the old builtin
                # key from env so polls don't keep authenticating against an
                # unrelated server.
                os.environ.pop("LOCAL_API_KEY", None)
            if model:
                os.environ["LOCAL_MODEL"] = model
            else:
                os.environ.pop("LOCAL_MODEL", None)
            # Persist the user's choice so Settings + the Models tab pill
            # both show the right thing on restart. Without this the pill
            # said "Built-in server" even after the user switched to Grok.
            try:
                _saved = _load_settings()
                _saved["llm_provider"] = provider or _saved.get("llm_provider", "")
                _saved["llm_url"] = url
                _saved["llm_key"] = key
                _saved["llm_model"] = model
                # If user moved to a non-local provider, stop preferring the
                # built-in autoboot — saves them the 8-15s "why is it loading
                # a model I'm not even using" surprise on next launch.
                if provider and provider != "local":
                    _saved["server_autoboot"] = False
                _save_settings(_saved)
            except Exception:
                pass
            # If we're switching to a CLOUD provider, free the built-in's
            # 5+ GB of VRAM — it would just sit idle. Skipped for local
            # endpoints since the user might be swapping between LM Studio
            # and the builtin on purpose. Power-users can opt out with the
            # `keep_local_warm` setting — useful if they bounce between
            # cloud and local during one session and don't want the 8-15s
            # reload tax.
            stopped_builtin = False
            keep_warm = bool(_load_settings().get("keep_local_warm", False))
            if provider and provider not in ("local", "") and not keep_warm:
                try:
                    from . import llmserver
                    if llmserver._proc and llmserver._proc.poll() is None:
                        llmserver.stop_builtin()
                        stopped_builtin = True
                except Exception:
                    pass
            _models_cache["ts"] = 0  # force the next /api/models to repopulate
            # For local: probe the URL so the apply doesn't silently succeed
            # when nothing's listening. Empty chat responses on first send
            # had no surface explanation otherwise. Three outcomes:
            #   - LM Studio responding: prefer it (more robust than builtin)
            #   - Our builtin responding
            #   - Nothing: surface a hint to start one
            local_probe = None
            if (provider or "").lower() == "local":
                try:
                    from . import llmserver as _ls
                    if _ls.external_server_running(url, timeout=1.0, api_key=key or None):
                        # Distinguish LM Studio from our own builtin — the
                        # builtin is recognizable via the PID we track.
                        is_ours = (_ls._proc is not None and _ls._proc.poll() is None
                                   and (_ls._proc_info or {}).get("url") == url)
                        local_probe = {
                            "reachable": True,
                            "kind": "builtin" if is_ours else "lmstudio",
                        }
                    else:
                        local_probe = {"reachable": False, "kind": "none"}
                except Exception:
                    pass
            return self._send_json(200, {
                "ok": True, "url": url, "provider": provider,
                "stopped_builtin": stopped_builtin,
                "local_probe": local_probe,
            })
        if path == "/api/llmserver/start":
            # Boot the optional built-in llama-cpp-python server with the user's
            # picked model file + load config. The GUI surfaces every llama.cpp
            # knob (n_gpu_layers, ctx, KV cache quant, threads, flash attn)
            # — see the Models tab inline expansion.
            from . import llmserver
            body = self._read_json()
            model_path = (body.get("model_path") or "").strip()
            # Body wins (Models tab can override per-load); else user's saved
            # Settings → Default context window; else 24K as final fallback.
            ctx        = int(body.get("ctx") or _load_settings().get("server_default_ctx") or 24576)
            n_gpu      = int(body.get("n_gpu_layers") if body.get("n_gpu_layers") is not None else -1)
            n_threads  = body.get("n_threads")
            n_threads  = int(n_threads) if n_threads not in (None, "", "auto") else None
            ck         = (body.get("cache_type_k") or "").strip() or None
            cv         = (body.get("cache_type_v") or "").strip() or None
            flash      = bool(body.get("flash_attn", True))
            # `force=true` from the GUI bypasses the VRAM guardrail — opt-in
            # only, after the user clicks "Force load anyway" in the modal.
            # llama.cpp will spill weights to system RAM (slow but boots).
            force      = bool(body.get("force", False))
            result = llmserver.start_builtin(
                model_path, ctx=ctx, n_gpu_layers=n_gpu,
                n_threads=n_threads, cache_type_k=ck, cache_type_v=cv,
                flash_attn=flash, force=force,
            )
            # On successful start, retarget the GLOBAL LLM endpoint to the
            # builtin URL so the topbar dropdown + /api/models + chat all
            # talk to it. Without this, the topbar keeps showing "no model
            # loaded" because it still queries LM Studio's port.
            # `global LOCAL_API_BASE` is already declared earlier in this
            # do_POST (under /api/llm-endpoint) so we don't redeclare it.
            if result.get("ok") and result.get("url"):
                # `_hl` (hearth.headless) is imported lazily inside the
                # /api/llm-endpoint branch above, so it's not visible to us
                # here. Import locally so the chat client picks up the new
                # base. Cheap — Python caches.
                from . import headless as _hl
                LOCAL_API_BASE = result["url"]
                _hl.LOCAL_API_BASE = LOCAL_API_BASE
                _hl.LOCAL_API_KEY  = "hearth-builtin"
                os.environ["LOCAL_API_BASE"] = LOCAL_API_BASE
                # Builtin uses a fixed key; bake it in so chat works without
                # the user touching settings.
                os.environ["LOCAL_API_KEY"] = "hearth-builtin"
                # Pop LOCAL_MODEL so chat falls back to probing the
                # builtin's /v1/models for the loaded id. Without this,
                # switching from a cloud brain to the builtin would
                # forward the cloud model id to the new server and the
                # server would 404 it, producing empty completions.
                # Mirrored in /api/llm-endpoint for the case where the
                # provider switches without an explicit model body.
                os.environ.pop("LOCAL_MODEL", None)
                # Mirror the brain change into settings so the GUI's
                # llm_provider pill + Chat brain dropdown reflect reality
                # — otherwise the GUI keeps showing "Grok" after the
                # retarget to the builtin.
                try:
                    _s = _load_settings()
                    _s["llm_provider"] = "local"
                    _s["llm_url"]      = LOCAL_API_BASE
                    _s["llm_key"]      = ""  # builtin key isn't user-facing
                    _s["llm_model"]    = ""  # let probe pick it
                    _save_settings(_s)
                except Exception:
                    pass
                _models_cache["ts"] = 0  # force /api/models to repopulate
                # Sticky default: remember the model the user just picked so
                # the NEXT launch auto-boots it (see _auto_boot_preferred_model_async).
                # Setting persists in ~/Jarvis/settings.json as preferred_model.
                try:
                    saved = _load_settings()
                    if saved.get("preferred_model") != model_path:
                        saved["preferred_model"] = model_path
                        os.makedirs(os.path.dirname(SETTINGS_PATH) or WORKSPACE, exist_ok=True)
                        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                            json.dump(saved, f, indent=2)
                except Exception:
                    pass  # best-effort — preference is a nice-to-have
            return self._send_json(200, result)
        if path == "/api/llmserver/stop":
            from . import llmserver
            stop_result = llmserver.stop_builtin()
            # Revert endpoint to whatever was configured before (LM Studio
            # default) so subsequent chats don't try to hit a dead URL.
            from . import headless as _hl
            settings = _load_settings()
            saved_url = (settings.get("llm_url") or "").strip()
            new_base = saved_url or "http://localhost:1234/v1"
            LOCAL_API_BASE = new_base
            _hl.LOCAL_API_BASE = new_base
            os.environ["LOCAL_API_BASE"] = new_base
            # Clear preferred_model so the NEXT launch doesn't auto-re-load
            # what the user just explicitly ejected. The user reported the
            # builtin restarting itself after Eject — that was the autoboot
            # picking up the still-set preferred_model on next boot. Eject
            # should mean "stop AND stop wanting this".
            try:
                if settings.get("preferred_model"):
                    settings["preferred_model"] = ""
                    _save_settings(settings)
            except Exception:
                pass
            _models_cache["ts"] = 0  # force /api/models to repopulate
            return self._send_json(200, stop_result)
        if path == "/api/llmserver/download-hf":
            # Stream a download from an arbitrary HF repo + filename (user
            # picked it via the search UI). Mirrors /api/llmserver/download
            # but for non-curated picks.
            from . import llmserver
            body = self._read_json()
            repo = (body.get("repo") or "").strip()
            filename = (body.get("filename") or "").strip()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            _last_emit = [0.0]

            def emit(obj: Dict[str, Any]) -> None:
                try:
                    self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def on_progress(done: int, total: int) -> None:
                now = time.time()
                if now - _last_emit[0] < 0.2 and done != total:
                    return
                _last_emit[0] = now
                emit({"type": "progress", "done": done, "total": total})

            try:
                result = llmserver.download_from_hf_repo(repo, filename, on_progress=on_progress)
                emit({"type": "done", **result})
            except Exception as e:
                emit({"type": "done", "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/llmserver/download":
            # Download a curated pick from HF, streaming progress as NDJSON so
            # the GUI can render a real progress bar. {type:"progress", done, total}
            # events while downloading, then a final {type:"done", ok, path|error}.
            from . import llmserver
            body = self._read_json()
            pick_id = (body.get("pick_id") or "").strip()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            _last_emit = [0.0]

            def emit(obj: Dict[str, Any]) -> None:
                try:
                    self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def on_progress(done: int, total: int) -> None:
                now = time.time()
                # Throttle to ~5 events/sec so we don't drown the socket.
                if now - _last_emit[0] < 0.2 and done != total:
                    return
                _last_emit[0] = now
                emit({"type": "progress", "done": done, "total": total})

            try:
                result = llmserver.download_model(pick_id, on_progress=on_progress)
                emit({"type": "done", **result})
            except Exception as e:
                emit({"type": "done", "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        self.send_error(404, "not found")

    # -------- handlers --------

    def _send_state(self) -> None:
        loaded = _detect_loaded()
        self._send_json(200, {
            "model": loaded,
            "endpoint": LOCAL_API_BASE,
            "tools": len(TOOL_DEFINITIONS),
            "memories": len(_memory_index()),
            "workspace": WORKSPACE,
            "lms_cli": bool(_find_lms_cli()),
            # For the "Hearth as MCP server" snippet — the real interpreter +
            # full script path so the config doesn't rely on bare `python` being
            # on PATH or on `-m` resolving the package from some unknown cwd.
            "python_exe": sys.executable,
            "mcp_server_path": os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py"),
            "frozen": bool(getattr(sys, "frozen", False)),
        })

    def _send_memory_one(self, name: str) -> None:
        # 1) try exact filename
        path = os.path.join(memory.MEM_DIR, f"{name}.md")
        if not os.path.isfile(path):
            # 2) try slug fallback (display name → file basename)
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            alt = os.path.join(memory.MEM_DIR, f"{slug}.md")
            if os.path.isfile(alt):
                path = alt
            else:
                # 3) scan: match by frontmatter name OR by basename starts-with
                for fn in os.listdir(memory.MEM_DIR) if os.path.isdir(memory.MEM_DIR) else []:
                    if not fn.endswith(".md") or fn == "MEMORY.md":
                        continue
                    cand = os.path.join(memory.MEM_DIR, fn)
                    try:
                        with open(cand, "r", encoding="utf-8") as f:
                            head = f.read(800)
                        if (f"\nname: {name}\n" in head or
                            fn[:-3].lower() == slug.lower() or
                            f"name: {name}\n" in head[:200]):
                            path = cand
                            break
                    except OSError:
                        continue
                else:
                    return self._send_json(404, {"error": f"no such memory: {name}"})
        try:
            with open(path, "r", encoding="utf-8") as f:
                body = f.read()
        except OSError as e:
            return self._send_json(500, {"error": str(e)})
        self._send_json(200, {"name": name, "body": body, "path": path})

    def _delete_memory(self, name: str) -> None:
        try:
            execute_tool("memory_forget", {"name": name})
        except Exception as e:
            return self._send_json(500, {"error": str(e)})
        self._send_json(200, {"ok": True, "name": name})

    def _reveal_in_folder(self) -> None:
        """Open the OS file explorer with the given file selected. Workspace-
        sandboxed (same rule as _serve_binary_file). Non-blocking spawn."""
        from .tools import list_extra_workspaces
        body = self._read_json()
        path = (body.get("path") or "").strip()
        if not path:
            return self._send_json(400, {"error": "missing path"})
        try:
            real = os.path.realpath(path)
        except Exception:
            return self._send_json(400, {"error": "bad path"})
        ws_real = os.path.realpath(WORKSPACE)
        allowed_roots = [ws_real] + [os.path.realpath(p) for p in list_extra_workspaces()]
        if not any(real.lower().startswith(root.lower() + os.sep) or
                   real.lower() == root.lower() for root in allowed_roots):
            return self._send_json(403, {"error": "path outside allowed roots"})
        if not os.path.exists(real):
            return self._send_json(404, {"error": "not found"})
        try:
            if sys.platform == "win32":
                # explorer /select,<path> pops Explorer with file highlighted
                subprocess.Popen(["explorer", "/select,", real],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", real])
            else:
                # Linux: no portable "select file" — open the parent dir.
                parent = real if os.path.isdir(real) else os.path.dirname(real)
                subprocess.Popen(["xdg-open", parent])
            return self._send_json(200, {"ok": True})
        except Exception as e:
            return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

    def _rename_agent(self) -> None:
        """Rename the agent end-to-end: persona NAME, settings.agent_name,
        AND the workspace folder (~/Jarvis -> ~/<NewName>). The folder move
        forces a tray restart because WORKSPACE / SETTINGS_PATH / MEM_DIR /
        CONVOS_DIR are bound at import time; a helper subprocess does the
        rename + relaunch AFTER this process exits so no file is locked."""
        import re as _re
        import shutil as _shutil
        body = self._read_json()
        new_name = (body.get("new_name") or "").strip()
        if not new_name:
            return self._send_json(400, {"error": "missing new_name"})
        # Strict whitelist: letters / digits / spaces, 1-20 chars. Rejects
        # path-traversal (../), reserved chars (\\ / : * ? " < > |), and any
        # leading/trailing whitespace funkiness.
        if not _re.match(r"^[A-Za-z0-9 ]{1,20}$", new_name):
            return self._send_json(400, {"error": "name must be 1-20 chars, letters/digits/spaces only"})
        current_name = (_load_settings().get("agent_name") or "JARVIS").strip() or "JARVIS"
        if new_name == current_name:
            return self._send_json(200, {"ok": True, "noop": True, "name": new_name})
        # Compute target workspace. Use the PARENT of current WORKSPACE so a
        # user who already set $JARVIS_WORKSPACE=D:\Stuff\Jarvis gets the
        # rename done in-place (D:\Stuff\Cortana), not yanked back to ~.
        cur_ws = os.path.realpath(WORKSPACE)
        parent = os.path.dirname(cur_ws)
        new_ws = os.path.join(parent, new_name)
        if os.path.exists(new_ws):
            return self._send_json(409, {"error": f"target folder already exists: {new_ws}"})
        # Persist the new name FIRST. settings.json lives inside the current
        # workspace, so the rename carries it along — when the relaunched
        # tray reads settings, it sees agent_name=NewName already.
        try:
            _save_settings({"agent_name": new_name})
        except Exception as e:
            return self._send_json(500, {"error": f"settings save failed: {e}"})
        # Stop the built-in LLM server (if any) so its file handles in
        # WORKSPACE don't block the rename. Best-effort; cloud-only users
        # have nothing to stop.
        try:
            from . import llmserver as _ls
            if _ls._proc is not None and _ls._proc.poll() is None:
                _ls.stop_builtin()
        except Exception:
            pass
        # Schedule the rename + respawn AFTER this HTTP response flushes.
        # The helper waits for our PID to be gone before touching the dir.
        def _do_rename_then_respawn():
            import time as _t
            _t.sleep(1.5)  # let the HTTP response reach the browser
            # Build a tiny self-contained helper command. We can't do the
            # rename from this process because settings.json / DB files may
            # still hold a handle on Windows. Spawn a detached python that
            # outlives us, waits for our PID to exit, then does the move +
            # relaunches the tray with JARVIS_WORKSPACE pointing at the
            # new folder. Same drive => os.rename (instant). Cross-drive
            # => copytree + rmtree fallback.
            here = os.path.dirname(os.path.abspath(__file__))
            repo_root = os.path.dirname(here)
            venv_pyw = os.path.join(repo_root, ".venv", "Scripts", "pythonw.exe")
            venv_py  = os.path.join(repo_root, ".venv", "Scripts", "python.exe")
            py = venv_pyw if os.path.isfile(venv_pyw) else (
                 venv_py  if os.path.isfile(venv_py)  else sys.executable)
            our_pid = os.getpid()
            helper = (
                "import os, sys, time, shutil, subprocess\n"
                f"old={cur_ws!r}\n"
                f"new={new_ws!r}\n"
                f"pid={our_pid}\n"
                "for _ in range(200):\n"
                "    try:\n"
                "        if sys.platform == 'win32':\n"
                "            import ctypes\n"
                "            PROCESS_QUERY = 0x1000\n"
                "            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY, False, pid)\n"
                "            if not h: break\n"
                "            ctypes.windll.kernel32.CloseHandle(h)\n"
                "        else:\n"
                "            os.kill(pid, 0)\n"
                "    except Exception:\n"
                "        break\n"
                "    time.sleep(0.1)\n"
                "time.sleep(0.5)\n"
                "try:\n"
                "    os.rename(old, new)\n"
                "except OSError:\n"
                "    try:\n"
                "        shutil.copytree(old, new)\n"
                "        shutil.rmtree(old, ignore_errors=True)\n"
                "    except Exception as e:\n"
                "        print('rename failed:', e); sys.exit(1)\n"
                "env = os.environ.copy()\n"
                "env['JARVIS_WORKSPACE'] = new\n"
                f"env['HEARTH_PERSONA_NAME'] = {new_name!r}\n"
                "flags = 0\n"
                "if sys.platform == 'win32':\n"
                "    flags = 0x08000000 | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008\n"
                f"subprocess.Popen([{py!r}, '-m', 'hearth.tray', '--open'],\n"
                "                 env=env, creationflags=flags, close_fds=True)\n"
            )
            try:
                flags = 0
                if sys.platform == "win32":
                    flags = (0x08000000  # CREATE_NO_WINDOW
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                             | 0x00000008)  # DETACHED_PROCESS
                subprocess.Popen([py, "-c", helper],
                                 creationflags=flags, close_fds=True)
            except Exception:
                pass
            # Step down. The helper takes over.
            _t.sleep(0.3)
            os._exit(0)
        threading.Thread(target=_do_rename_then_respawn, daemon=True).start()
        return self._send_json(200, {
            "ok": True,
            "name": new_name,
            "old_workspace": cur_ws,
            "new_workspace": new_ws,
            "restarting_in_ms": 1500,
        })

    def _serve_binary_file(self) -> None:
        """Stream a raw file with correct Content-Type so the chat can render
        <img src="/file?path=..."> and <video src="/file?path=..."> inline.

        Hard restriction: the path MUST resolve inside WORKSPACE (or
        list_extra_workspaces()). Without this, a chat that includes
        __JARVIS_IMAGE__ C:/Windows/System32/config/SAM could try to slurp
        arbitrary files via the browser fetch. Reject anything that escapes.
        """
        import mimetypes
        from .tools import list_extra_workspaces
        q = self._query()
        path = q.get("path", "")
        if not path:
            return self._send_json(400, {"error": "missing path"})
        try:
            real = os.path.realpath(path)
        except Exception:
            return self._send_json(400, {"error": "bad path"})
        ws_real = os.path.realpath(WORKSPACE)
        allowed_roots = [ws_real] + [os.path.realpath(p) for p in list_extra_workspaces()]
        if not any(real.lower().startswith(root.lower() + os.sep) or
                   real.lower() == root.lower() for root in allowed_roots):
            return self._send_json(403, {"error": "path outside allowed roots"})
        if not os.path.isfile(real):
            return self._send_json(404, {"error": "not found"})
        ctype, _ = mimetypes.guess_type(real)
        ctype = ctype or "application/octet-stream"
        try:
            size = os.path.getsize(real)
            with open(real, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                # Stream in chunks so a big video doesn't hold the whole
                # bytes object in memory.
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
        except OSError as e:
            return self._send_json(500, {"error": str(e)})

    def _send_file_op(self) -> None:
        q = self._query()
        path = q.get("path", "")
        op = q.get("op", "read")
        if not path:
            return self._send_json(400, {"error": "missing path"})
        try:
            if op == "summarize":
                result = execute_tool("summarize_file", {"path": path})
            elif op == "list_archive":
                result = execute_tool("list_archive", {"path": path})
            else:
                result = execute_tool("read_file", {"path": path})
        except Exception as e:
            return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        self._send_json(200, {"path": path, "op": op, "content": result})

    def _upload(self) -> None:
        body = self._read_json()
        name = (body.get("name") or "").strip().replace("\\", "/").split("/")[-1]
        b64 = body.get("content_b64") or ""
        if not name or not b64:
            return self._send_json(400, {"error": "need name + content_b64"})
        try:
            blob = base64.b64decode(b64, validate=True)
        except Exception as e:
            return self._send_json(400, {"error": f"bad base64: {e}"})
        if len(blob) > 200 * 1024 * 1024:
            return self._send_json(400, {"error": "file too large (>200MB) — use a workspace path instead"})
        dest = os.path.join(WORKSPACE, "uploads", name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob)
        self._send_json(200, {"ok": True, "path": dest, "size": len(blob)})

    def _stream_chat(self) -> None:
        body = self._read_json()
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return self._send_json(400, {"error": "empty prompt"})
        think = bool(body.get("think"))
        model = body.get("model") or None
        history = body.get("history") or []
        # Diagnostic — print received history shape so we can see when the
        # GUI client is sending an empty or malformed history despite the
        # user being on turn 2+. Only logs when non-empty so it doesn't
        # spam on the first turn of every chat.
        if history:
            try:
                _shape = [(h.get("role"), len((h.get("content") or "")))
                          for h in history]
                print(f"[hearth.chat] /chat got {len(history)} history msgs: {_shape}", flush=True)
            except Exception:
                pass
        # Optional inline auto-load before chat. SKIP when we're on a cloud
        # endpoint — `_load_model` calls LM Studio's `lms load <id>` which
        # has no idea what "grok-4.3" is and errors with "Model not found".
        # Cloud models are served by their providers; no local load step.
        try:
            from urllib.parse import urlparse
            _host = (urlparse(_active_base()).hostname or "").lower()
            _is_local_endpoint = _host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")
        except Exception:
            _is_local_endpoint = True
        if _is_local_endpoint and model and (loaded := _detect_loaded()) and loaded.get("id") != model:
            settings = _load_settings()
            if settings.get("auto_load_model"):
                _load_model(model)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event_type: str, **fields: Any) -> None:
            line = json.dumps({"type": event_type, **fields}, ensure_ascii=False, default=str) + "\n"
            try:
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        _CANCEL.clear()  # fresh generation — clear any prior stop request
        # Wire the GUI as the ask_user surface for the lifetime of THIS chat
        # turn — the tool dispatcher will route ask_user calls through here,
        # blocking until the user clicks an option in the modal.
        try:
            from .tools import set_ask_user_callback
            set_ask_user_callback(_make_ask_user_bridge(emit))
        except Exception:
            pass
        try:
            asyncio.run(run_once(
                prompt, emit=emit, think=think, model=model, history=history,
                permission_check=_make_permission_check(emit),
                should_cancel=_CANCEL.is_set,
            ))
        except Exception as e:
            emit("error", message=f"{type(e).__name__}: {e}")
        finally:
            # Tear down the ask_user binding so a later headless / CLI run
            # doesn't unexpectedly route through a dead emit closure.
            try:
                from .tools import set_ask_user_callback
                set_ask_user_callback(None)
            except Exception:
                pass

    # ----- Voice + title endpoints -----

    def _realtime_stream(self) -> None:
        """Streaming voice loop — silero VAD + faster-whisper.

        Streams NDJSON events:
          {"type":"partial","text":"..."}  — live partial transcript while user speaks
          {"type":"final","text":"..."}    — finalized utterance after silence
          {"type":"stopped"}               — recorder halted, client should close
          {"type":"error","message":"..."} — fatal init error
        """
        if not _rt_voice or not _rt_voice.is_available():
            return self._send_json(503, {
                "error": "realtime voice unavailable",
                "detail": "pip install RealtimeSTT silero-vad",
            })

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(obj: dict) -> bool:
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        # Drain any leftover events from a prior session.
        while True:
            try:
                _rt_event_queue.get_nowait()
            except Exception:
                break

        # Wire callbacks into the shared queue.
        def _on_partial(text: str) -> None:
            try:
                _rt_event_queue.put({"type": "partial", "text": text})
            except Exception:
                pass

        def _on_final(text: str) -> None:
            try:
                _rt_event_queue.put({"type": "final", "text": text})
            except Exception:
                pass

        def _on_barge() -> None:
            try:
                _rt_event_queue.put({"type": "barge"})
            except Exception:
                pass

        try:
            _rt_voice.set_caption_callback(_on_partial)
            _rt_voice.set_barge_callback(_on_barge)
            msg = _rt_voice.start_continuous(_on_final)
            emit({"type": "started", "detail": msg})
        except Exception as e:
            emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            return

        # Drain events until client disconnects or stop posted.
        try:
            while True:
                try:
                    ev = _rt_event_queue.get(timeout=15.0)
                except Exception:
                    if not emit({"type": "heartbeat"}):
                        break
                    continue
                if not emit(ev):
                    break
                if ev.get("type") == "stopped":
                    break
        finally:
            try:
                _rt_voice.set_caption_callback(None)
                _rt_voice.set_barge_callback(None)
                _rt_voice.stop_continuous()
            except Exception:
                pass

    def _tts(self) -> None:
        body = self._read_json()
        text = (body.get("text") or "").strip()
        if not text:
            return self._send_json(400, {"error": "empty text"})
        if not _voice or not _voice.is_available():
            reason = _voice.status() if _voice else {"reason": "voice module missing"}
            return self._send_json(503, {"error": "TTS not available", "detail": reason})
        # Speak through system speakers — non-blocking so the request returns fast
        try:
            r = _voice.speak(text, blocking=False)
            return self._send_json(200, {"ok": True, "result": r})
        except Exception as e:
            return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

    def _stt(self) -> None:
        body = self._read_json()
        audio_b64 = body.get("audio_b64") or ""
        sample_rate = int(body.get("sample_rate") or 16000)
        if not audio_b64:
            return self._send_json(400, {"error": "missing audio_b64"})
        if not _listen or not _listen.is_available():
            reason = _listen.status() if _listen else {"reason": "listen module missing"}
            return self._send_json(503, {"error": "STT not available", "detail": reason})
        try:
            raw = base64.b64decode(audio_b64, validate=True)
        except Exception as e:
            return self._send_json(400, {"error": f"bad base64: {e}"})
        # The browser sends raw 16-bit PCM, mono. Convert to float32 numpy
        # at the whisper-expected 16kHz.
        try:
            import numpy as np  # type: ignore
        except ImportError:
            return self._send_json(503, {"error": "numpy not installed"})
        try:
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if sample_rate != 16000:
                # Cheap linear resample — fine for STT
                ratio = 16000 / sample_rate
                new_len = int(len(pcm) * ratio)
                if new_len > 0:
                    idx = np.linspace(0, len(pcm) - 1, new_len).astype(np.int64)
                    pcm = pcm[idx]
            model = _listen._try_load_model()
            if model is None:
                return self._send_json(503, {"error": _listen._last_load_error or "whisper not ready"})
            segments, _info = model.transcribe(pcm, language="en", beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            return self._send_json(200, {"text": text})
        except Exception as e:
            return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

    def _title(self) -> None:
        """Generate a 3-5 word chat title from the first user message.
        Always calls the model when one is loaded so short messages still
        get a real title instead of an echoed first-N-words slice. Falls
        back to first-N-words only when no model is loaded at all."""
        body = self._read_json()
        first = (body.get("text") or "").strip()
        if not first:
            return self._send_json(400, {"error": "empty text"})

        def _fallback(text: str) -> str:
            words = text.split()
            if len(words) <= 6:
                return text[:50]
            return " ".join(words[:6]) + "…"

        # Pick a model: prefer the active brain (cloud or local). The old
        # path only checked _detect_loaded() (local servers) and fell back
        # to a verbatim truncate when on cloud — title would be "draw me a
        # glowing logo" instead of "Hearth Logo Design". Now: use whatever
        # the chat is actually pointed at.
        settings = _load_settings()
        provider = (settings.get("llm_provider") or "").lower()
        is_cloud = provider not in ("", "local", "lmstudio", "builtin")
        model_id = (settings.get("llm_model") or "").strip()
        if not model_id:
            loaded = _detect_loaded()
            if loaded:
                model_id = loaded.get("id") or ""
        if not model_id and not is_cloud:
            return self._send_json(200, {"title": _fallback(first)})
        if not model_id and is_cloud:
            # Cloud provider with no model_id saved — pick a sensible default
            model_id = {
                "grok": "grok-4.3",
                "gemini": "gemini-2.5-flash",
                "openai": "gpt-4o-mini",
            }.get(provider, "gpt-4o-mini")
        prompt = (
            f"Output ONLY a 3-5 word title (no quotes, no preamble) for this "
            f"chat message. Nothing else.\n\n"
            f"Message: {first[:300]}\n\n"
            f"Title:"
        )
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0.3,
            "stream": False,
        }
        # Pass Authorization header so cloud endpoints (and our authed
        # builtin) actually accept the call instead of 401'ing.
        api_key = (os.environ.get("LOCAL_API_KEY") or
                   settings.get("llm_key") or "hearth-builtin")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = _http_post_json(f"{LOCAL_API_BASE}/chat/completions", payload,
                               timeout=20, headers=headers)
        if not isinstance(resp, dict) or resp.get("error"):
            return self._send_json(200, {"title": _fallback(first)})
        try:
            title = resp["choices"][0]["message"]["content"].strip()
            # Strip wrappers / "Title:" prefix from chatty models
            title = title.strip('"\'`*').strip()
            for prefix in ("Title:", "title:", "TITLE:", "Here is", "Here's"):
                if title.startswith(prefix):
                    title = title[len(prefix):].strip(' :"\'`*').strip()
            title = title.split("\n", 1)[0]
            # Hard cap at 6 words — anything more is the model echoing the prompt
            words = title.split()
            if len(words) > 6:
                title = " ".join(words[:6])
            title = title[:50].strip(' .,:;')
            return self._send_json(200, {"title": title or _fallback(first)})
        except Exception:
            return self._send_json(200, {"title": _fallback(first)})

    def _download_logs(self) -> None:
        log_path = os.path.join(LOGS_DIR, "activity.jsonl")
        if not os.path.isfile(log_path):
            return self.send_error(404, "no log file")
        try:
            with open(log_path, "rb") as f:
                payload = f.read()
        except OSError as e:
            return self.send_error(500, str(e))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Disposition", 'attachment; filename="hearth-activity.jsonl"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_ui(self) -> None:
        if not os.path.isfile(_UI_PATH):
            return self.send_error(500, f"UI file missing at {_UI_PATH}")
        with open(_UI_PATH, "rb") as f:
            payload = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_asset(self, rel: str) -> None:
        """Serve files from <repo-root>/assets/ — icon, logo, etc.
        Path-traversal guarded: rel can't escape the assets dir."""
        repo_root = os.path.dirname(_HERE)
        assets_dir = os.path.join(repo_root, "assets")
        target = os.path.normpath(os.path.join(assets_dir, rel))
        if not target.startswith(assets_dir) or not os.path.isfile(target):
            return self.send_error(404, "asset not found")
        ext = os.path.splitext(target)[1].lower()
        mime = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            # JS/CSS for vendored libraries (d3, mermaid in future). Without
            # the correct Content-Type, the browser refuses to execute d3
            # under strict-mime-sniffing.
            ".js": "application/javascript", ".css": "text/css",
            ".json": "application/json", ".woff": "font/woff", ".woff2": "font/woff2",
        }.get(ext, "application/octet-stream")
        with open(target, "rb") as f:
            payload = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(payload)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def _preload_whisper_async() -> None:
    """Warm up the whisper model in a background thread so the first STT call
    is fast instead of paying 2-3s cold-load cost."""
    def _go():
        if _listen and _listen.is_available():
            try:
                _listen._try_load_model()
            except Exception:
                pass
    threading.Thread(target=_go, daemon=True).start()


def _start_reminder_watcher() -> None:
    """Background reminder watcher. Fires Windows toast notifications via
    plyer (lightweight) or falls back to print + voice.speak()."""
    try:
        from . import reminders as _rem
    except Exception:
        return

    def _notify(title: str, body: str) -> None:
        # 1. Try plyer (cross-platform desktop notifications)
        try:
            from plyer import notification  # type: ignore
            notification.notify(title=title, message=body, app_name="Hearth", timeout=10)
            return
        except Exception:
            pass
        # 2. Try win10toast (Windows-specific)
        try:
            from win10toast import ToastNotifier  # type: ignore
            ToastNotifier().show_toast(title, body, duration=8, threaded=True)
            return
        except Exception:
            pass
        # 3. Always also speak + log
        print(f"[reminder] {title}: {body}", flush=True)
        if _voice and _voice.is_available():
            try:
                _voice.speak(f"Reminder: {body}", blocking=False)
            except Exception:
                pass

    _rem.start_watcher(_notify)


def _apply_saved_llm_endpoint() -> None:
    """At boot: (1) if the user previously picked a cloud provider via Settings
    -> LLM endpoint, apply it. (2) Otherwise, auto-detect whatever local server
    is actually running (LM Studio, Ollama, llama.cpp). Survives restart via
    ~/Jarvis/settings.json."""
    global LOCAL_API_BASE
    try:
        s = _load_settings()
        url = (s.get("llm_url") or "").strip()
        # If no saved endpoint, sniff for any local server already running.
        if not url:
            try:
                from . import llmserver as _ls
                detected = _ls.detect_running_server(LOCAL_API_BASE)
                if detected and detected != LOCAL_API_BASE:
                    url = detected
                    print(f"  [hearth.web] auto-detected local LLM server: {url}", flush=True)
            except Exception:
                pass
        if not url:
            return
        key = (s.get("llm_key") or "").strip()
        model = (s.get("llm_model") or "").strip()
        LOCAL_API_BASE = url
        from . import headless as _hl
        _hl.LOCAL_API_BASE = url
        _hl.LOCAL_API_KEY = key or "not-needed"
        os.environ["LOCAL_API_BASE"] = url
        if key:
            os.environ["LOCAL_API_KEY"] = key
        if model:
            os.environ["LOCAL_MODEL"] = model
    except Exception:
        pass


_apply_saved_llm_endpoint()


def _auto_boot_preferred_model_async() -> None:
    """If the user picked a default model in Settings (`preferred_model`) and
    nothing's already serving on the configured endpoint, boot Hearth's
    built-in llama-cpp server with that model in the background.

    Without this, "default model" was a stored string nothing read on boot
    — the user had to click Use this in the Models tab every launch. Now
    Hearth honors the setting automatically. Skip with JARVIS_NO_AUTOBOOT=1.

    Match logic: the saved `preferred_model` may be the LM Studio short id
    ('harmonic-hermes-9b'), a filename ('Qwen3.5-9B-Harmonic.Q4_K_M.gguf'),
    or a full path. We try in order: exact path, exact filename, then
    case-insensitive slug-match against each disk model's filename.
    """
    if os.environ.get("JARVIS_NO_AUTOBOOT") in ("1", "true", "yes"):
        return
    # Honor the user's Settings → Server → "Auto-boot last model on launch"
    # toggle. Default True so the existing behavior is preserved.
    try:
        settings = _load_settings()
        if not settings.get("server_autoboot", True):
            print("  [hearth.web] autoboot disabled in Settings; skipping", flush=True)
            return
        # If the user's last brain was a cloud provider, DON'T autoboot a
        # local model under them. They explicitly picked cloud; loading a
        # local model would silently hijack the brain back to local and the
        # Models tab would show LM Studio instead of Grok (the bug they
        # reported). When the user wants local back, they pick it via
        # Settings → Chat brain.
        prov = (settings.get("llm_provider") or "").strip().lower()
        if prov and prov not in ("local", "lmstudio", "builtin", ""):
            print(f"  [hearth.web] cloud brain '{prov}' selected — skipping local autoboot", flush=True)
            return
    except Exception:
        pass

    def _go():
        # Hoist `global` to the top of the function so Python doesn't infer
        # LOCAL_API_BASE as a local from the assignment below — same trap that
        # bit `_eject_model` earlier. Has to come BEFORE any read or write.
        global LOCAL_API_BASE
        try:
            from . import llmserver, headless as _hl
            settings = _load_settings()
            wanted = (settings.get("preferred_model") or "").strip()
            if not wanted:
                return

            # Don't fight an external server on the BUILTIN port (1234). The
            # old check probed LOCAL_API_BASE which might point at Grok/Gemini
            # (set by last CLI session) — fails to detect LM Studio sitting at
            # localhost:1234, then start_builtin's pre-flight correctly refuses
            # because of the conflict. Result the user saw: GUI launches, says
            # "Port 1234 already taken", Models tab looks empty.
            # New behavior: check 1234 first; if LM Studio is there with a
            # model loaded, retarget the GUI to it instead of trying to boot.
            builtin_api = f"http://127.0.0.1:{llmserver.BUILTIN_PORT}/v1"
            if llmserver.external_server_running(builtin_api, timeout=0.8, api_key=None):
                # Something IS on the builtin port — must NOT be us (we'd
                # already be in llmserver._proc). It's LM Studio / Ollama /
                # something else. Retarget the GUI endpoint to it and walk
                # away. The user explicitly started that server; honor it.
                if LOCAL_API_BASE != builtin_api:
                    LOCAL_API_BASE = builtin_api
                    _hl.LOCAL_API_BASE = LOCAL_API_BASE
                    os.environ["LOCAL_API_BASE"] = LOCAL_API_BASE
                    # External servers like LM Studio don't require a key;
                    # clear ours so we don't 401 against them.
                    os.environ.pop("LOCAL_API_KEY", None)
                    _models_cache["ts"] = 0
                    print(
                        f"  [hearth.web] external server on port {llmserver.BUILTIN_PORT} "
                        f"(likely LM Studio) — retargeted GUI to it, skipping autoboot",
                        flush=True,
                    )
                else:
                    print(
                        f"  [hearth.web] external server already serving at {LOCAL_API_BASE} — "
                        f"skipping autoboot",
                        flush=True,
                    )
                return
            # Already serving via builtin? Nothing to do.
            if llmserver._proc is not None and llmserver._proc.poll() is None:
                return
            disk = llmserver.scan_disk_for_models()
            if not disk:
                return
            def _slug(s):
                return ''.join(c for c in (s or '').lower() if c.isalnum())
            want_slug = _slug(wanted)
            pick = None
            for m in disk:
                if m.get("path") == wanted:
                    pick = m; break
                if m.get("filename") == wanted:
                    pick = m; break
                # Slug match must be BIDIRECTIONAL — a curated pick id like
                # "harmonic-hermes-9b" doesn't slug-contain "Qwen3.5-9B-Harmonic"
                # one way, but DOES the other. Check both directions plus a
                # shared-substring tier for the messy LM-Studio-renamed cases.
                file_slug = _slug(m.get("filename"))
                if not want_slug or not file_slug:
                    continue
                if want_slug in file_slug or file_slug in want_slug:
                    pick = m; break
                # Last resort: any 6+ char run that appears in both — handles
                # "harmonic-hermes-9b" ↔ "Qwen3.5-9B-Harmonic" via the shared
                # "harmonic" token (LM Studio repackaged the same Hermes weights
                # under a Qwen3.5-prefixed filename).
                for n in range(min(len(want_slug), 16), 5, -1):
                    if any(want_slug[i:i+n] in file_slug
                           for i in range(len(want_slug) - n + 1)):
                        pick = m; break
                if pick: break
            if not pick:
                # Surface what we scanned so the next time this triggers the
                # user (or we) can see WHY it missed — saves the "but the
                # model is right there" round-trip.
                names = ", ".join(m.get("filename","?") for m in disk[:6])
                print(
                    f"  [hearth.web] preferred_model {wanted!r} not matched "
                    f"against any of {len(disk)} disk models: {names}"
                    f"{'...' if len(disk) > 6 else ''} — skipping autoboot",
                    flush=True,
                )
                return
            # Load saved per-model config (ctx, n_gpu_layers, KV cache, ...)
            cfg = llmserver.get_model_config(pick["path"]) or {}
            # Per-model cfg WINS — some models need different ctx based on
            # VRAM. Settings → Default context window is just the fallback
            # for models without a saved cfg. User clarified: floor logic
            # was wrong because it ignored per-model VRAM realities.
            if "ctx" not in cfg:
                cfg["ctx"] = int(s.get("server_default_ctx", 24576))
            print(f"  [hearth.web] auto-booting {pick['filename']} "
                  f"({pick.get('size_gb','?')} GB, ctx={cfg.get('ctx')})…", flush=True)
            r = llmserver.start_builtin(pick["path"], **cfg)
            if r.get("ok"):
                # Retarget the GLOBAL endpoint to the builtin URL so the GUI
                # topbar + chat all pick it up. Mirror what the manual Use
                # this flow does in /api/llmserver/start.
                LOCAL_API_BASE = r["url"]
                _hl.LOCAL_API_BASE = LOCAL_API_BASE
                os.environ["LOCAL_API_BASE"] = LOCAL_API_BASE
                os.environ["LOCAL_API_KEY"] = "hearth-builtin"
                # Pop stale LOCAL_MODEL so chat probes the builtin for
                # the actually-loaded id. Without this, a previous
                # brain's model id can carry over in env and get sent
                # to the new server, which 404s it.
                os.environ.pop("LOCAL_MODEL", None)
                _models_cache["ts"] = 0
                print(f"  [hearth.web] builtin up at {r['url']} — endpoint switched", flush=True)
            else:
                print(f"  [hearth.web] autoboot failed: {r.get('error')}", flush=True)
        except Exception as e:
            print(f"  [hearth.web] autoboot skipped: {type(e).__name__}: {e}", flush=True)

    threading.Thread(target=_go, daemon=True).start()


def _maybe_learn_environment_async() -> None:
    """On first run (no memory yet), detect hardware/models/drive-map into memory
    so the GUI model has the same machine context the CLI gets from onboarding.
    Runs in a background thread so it never delays server startup. Skip with
    JARVIS_NO_ONBOARDING=1."""
    if os.environ.get("JARVIS_NO_ONBOARDING") in ("1", "true", "yes"):
        return
    try:
        from hearth.tools import MEMORY_DIR
        idx = os.path.join(MEMORY_DIR, "MEMORY.md")
        if os.path.exists(idx):
            with open(idx, "r", encoding="utf-8") as f:
                if sum(1 for ln in f if ln.strip().startswith("-")) >= 2:
                    return  # already onboarded
    except OSError:
        return

    def _go():
        try:
            from hearth.environment import learn_environment
            print(f"  [hearth.web] {learn_environment(endpoint=LOCAL_API_BASE)}", flush=True)
        except Exception as e:
            print(f"  [hearth.web] machine scan skipped: {e}", flush=True)

    threading.Thread(target=_go, daemon=True).start()


class _QuietHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server that quietly swallows connection-aborted errors.

    Browsers close + reopen sockets aggressively; socketserver's default
    handle_error dumps a full traceback per drop, which looks like Hearth is
    crashing when it's not. We silence ONLY the connection-class errors and
    keep tracebacks for everything else."""
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError,
                            ConnectionResetError)):
            return  # normal — client disconnected mid-write
        # Real bug — let it bubble up so we see it in the terminal
        super().handle_error(request, client_address)


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Start the server (non-blocking) and return the server instance.
    Used by hearth.desktop to embed the same backend in a PyWebView window."""
    server = _QuietHTTPServer((host, port), HearthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _preload_whisper_async()
    _start_reminder_watcher()
    _maybe_learn_environment_async()
    _auto_boot_preferred_model_async()
    # MCP client: spawn the servers configured in ~/Jarvis/mcp.json and
    # register their tools. Each server runs in its own subprocess; their
    # tools surface as 'mcp_<server>_<tool>' in to_openai_tools(). Safe
    # to call even with no config (returns servers=0 immediately).
    try:
        from . import mcp_client
        mcp_client.bootstrap()
    except Exception as e:
        print(f"[hearth.web] MCP client bootstrap failed: {e}", flush=True)
    # Sync subagents should bail when the user hits Stop. Background
    # subagents intentionally survive — that's the whole point of background.
    try:
        from . import subagents as _sa
        _sa.set_parent_cancel_check(_CANCEL.is_set)
    except Exception:
        pass
    return server


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.web",
        description="Hearth desktop UI — runs in your browser.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if not os.path.isfile(_UI_PATH):
        print(f"[hearth.web] FATAL: UI file missing at {_UI_PATH}", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer((args.host, args.port), HearthHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"\n  Hearth UI on {url}")
    print(f"  LM Studio: {LOCAL_API_BASE}")
    print(f"  Workspace: {WORKSPACE}")
    print(f"  Ctrl-C to stop.\n")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    _preload_whisper_async()
    _start_reminder_watcher()
    _maybe_learn_environment_async()
    _auto_boot_preferred_model_async()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[hearth.web] stopping.")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
