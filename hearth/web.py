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
    """Returns a callback the bridge can hand each risky tool call to."""
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


def _http_get_json(url: str, timeout: float = 3) -> Optional[Dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _http_post_json(url: str, body: Dict, timeout: float = 30) -> Optional[Dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _list_models() -> List[Dict]:
    """Pull rich model list from LM Studio's v0 API. Cached 4s."""
    now = time.time()
    if _models_cache["data"] is not None and now - _models_cache["ts"] < 4:
        return _models_cache["data"]
    data = _http_get_json(f"{LM_STUDIO_V0}/models") or {"data": []}
    out: List[Dict] = []
    for m in data.get("data", []):
        if m.get("type") == "embeddings":
            continue
        out.append({
            "id": m.get("id"),
            "type": m.get("type"),
            "arch": m.get("arch"),
            "publisher": m.get("publisher"),
            "state": m.get("state"),
            "loaded_context_length": m.get("loaded_context_length"),
            "max_context_length": m.get("max_context_length"),
            "quantization": m.get("quantization"),
            "capabilities": m.get("capabilities", []),
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
    """Try REST eject, fall back to `lms unload --all`."""
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
        name, desc, typ = fn[:-3], "", ""
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
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        out.append({"name": name, "description": desc, "type": typ,
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
        "stt_model": "base.en",  # base.en / small.en / medium.en
        "theme": "warm-flame",
        "preferred_model": "",
        "llm_provider": "local",
        "llm_url": "",
        "llm_key": "",
        "llm_model": "",
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
    return defaults


def _save_settings(d: Dict) -> Dict:
    cur = _load_settings()
    cur.update({k: v for k, v in d.items() if v is not None})
    os.makedirs(os.path.dirname(SETTINGS_PATH) or WORKSPACE, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
    # Re-apply env vars + reload voice/listen so new device picks effect
    os.environ["JARVIS_STT_DEVICE"] = cur.get("stt_device", "cpu")
    os.environ["JARVIS_STT_MODEL"]  = cur.get("stt_model",  "base.en")
    os.environ["JARVIS_TTS_DEVICE"] = cur.get("tts_device", "cpu")
    if _voice:
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
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

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
        if path == "/api/logs":
            q = self._query()
            try:
                n = int(q.get("lines", "100"))
            except ValueError:
                n = 100
            return self._send_json(200, {"events": _tail_log(n)})
        if path == "/api/logs/download":
            return self._download_logs()
        if path == "/api/voice/status":
            tts_status = {"available": False, "reason": "voice module missing"}
            stt_status = {"ready": False, "reason": "listen module missing"}
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
            return self._send_json(200, {"tts": tts_status, "stt": stt_status})
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
        path = urllib.parse.urlparse(self.path).path
        if path == "/chat":
            return self._stream_chat()
        if path == "/api/cancel":
            _CANCEL.set()  # run_once checks this between turns and bails out
            return self._send_json(200, {"cancelled": True})
        if path == "/api/models/load":
            body = self._read_json()
            mid = (body.get("id") or "").strip()
            if not mid:
                return self._send_json(400, {"error": "missing id"})
            return self._send_json(200, _load_model(mid))
        if path == "/api/models/eject":
            return self._send_json(200, _eject_model())
        if path == "/api/settings":
            return self._send_json(200, _save_settings(self._read_json()))
        if path == "/api/conversations":
            data = self._read_json()
            if not data.get("id"):
                return self._send_json(400, {"error": "missing id"})
            ok = _save_convo(data)
            return self._send_json(200 if ok else 500, {"ok": ok})
        if path == "/api/logs/clear":
            log_path = os.path.join(LOGS_DIR, "activity.jsonl")
            try:
                if os.path.isfile(log_path):
                    # Move to a dated backup, start fresh
                    bak = os.path.join(LOGS_DIR, f"activity.{int(time.time())}.jsonl.bak")
                    os.rename(log_path, bak)
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("")
                return self._send_json(200, {"ok": True})
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
            if not url:
                return self._send_json(400, {"ok": False, "error": "url required"})
            global LOCAL_API_BASE
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
            if model:
                os.environ["LOCAL_MODEL"] = model
            return self._send_json(200, {"ok": True, "url": url})
        if path == "/api/llmserver/start":
            # Boot the optional built-in llama-cpp-python server with a chosen
            # model file. Returns {ok, url, pid} or {ok: False, error}.
            from . import llmserver
            body = self._read_json()
            model_path = (body.get("model_path") or "").strip()
            ctx = int(body.get("ctx") or 8192)
            n_gpu = int(body.get("n_gpu_layers") if body.get("n_gpu_layers") is not None else -1)
            return self._send_json(200, llmserver.start_builtin(model_path, ctx=ctx, n_gpu_layers=n_gpu))
        if path == "/api/llmserver/stop":
            from . import llmserver
            return self._send_json(200, llmserver.stop_builtin())
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
        if len(blob) > 50 * 1024 * 1024:
            return self._send_json(400, {"error": "file too large (>50MB)"})
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
        # Optional inline auto-load before chat
        if model and (loaded := _detect_loaded()) and loaded.get("id") != model:
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
        try:
            asyncio.run(run_once(
                prompt, emit=emit, think=think, model=model, history=history,
                permission_check=_make_permission_check(emit),
                should_cancel=_CANCEL.is_set,
            ))
        except Exception as e:
            emit("error", message=f"{type(e).__name__}: {e}")

    # ----- Voice + title endpoints -----

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
        ALWAYS calls the model when one is loaded — short messages still
        get a real AI title (was previously fallback-on-short, which the
        user flagged as 'sidebar just shows my first message'). Only
        falls back to first-N-words when no model is loaded at all."""
        body = self._read_json()
        first = (body.get("text") or "").strip()
        if not first:
            return self._send_json(400, {"error": "empty text"})

        def _fallback(text: str) -> str:
            words = text.split()
            if len(words) <= 6:
                return text[:50]
            return " ".join(words[:6]) + "…"

        loaded = _detect_loaded()
        if not loaded:
            return self._send_json(200, {"title": _fallback(first)})
        prompt = (
            f"Output ONLY a 3-5 word title (no quotes, no preamble) for this "
            f"chat message. Nothing else.\n\n"
            f"Message: {first[:300]}\n\n"
            f"Title:"
        )
        payload = {
            "model": loaded.get("id"),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0.3,
            "stream": False,
        }
        resp = _http_post_json(f"{LOCAL_API_BASE}/chat/completions", payload, timeout=20)
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
    """At boot, if the user previously picked a cloud provider via Settings ->
    LLM endpoint, apply it to LOCAL_API_BASE / LOCAL_API_KEY before the first
    /chat call. Settings live in ~/Jarvis/settings.json so this survives every
    restart and matches what the user sees in the UI."""
    try:
        s = _load_settings()
        url = (s.get("llm_url") or "").strip()
        if not url:
            return
        key = (s.get("llm_key") or "").strip()
        model = (s.get("llm_model") or "").strip()
        global LOCAL_API_BASE
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


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Start the server (non-blocking) and return the server instance.
    Used by hearth.desktop to embed the same backend in a PyWebView window."""
    server = ThreadingHTTPServer((host, port), HearthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _preload_whisper_async()
    _start_reminder_watcher()
    _maybe_learn_environment_async()
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

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[hearth.web] stopping.")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
