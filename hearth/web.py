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
GET  /api/gpu                   → load and memory per graphics card, any vendor (cached 2s)
GET  /api/tools                 → tool definitions (for docs / debug)
GET  /api/persona               → current system prompt (for settings preview)
"""

from __future__ import annotations

import argparse
from . import env as _env
from . import live_bus
live_bus.IN_WINDOW = True              # this process serves /api/events
import asyncio
import base64
import io
import json
import os
import re
import subprocess
import sys
import secrets
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from .local_engine import local_base as _local_base

from . import system_prompt, TOOL_DEFINITIONS, memory, execute_tool
from .headless import run_once
from .tools import (WORKSPACE, LOGS_DIR,
                    WRITE_TEMP_PREFIX as _WRITE_TEMP_PREFIX,
                    is_write_temp as _is_write_temp,
                    sweep_write_temps as _sweep_write_temps,
                    atomic_write_json as _atomic_write_json)

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

# Per-stream event queues for /api/voice/realtime/stream.
import queue as _queue_mod

# Window reference for single-instance focus. desktop.py sets this to its
# pywebview window object so /api/focus can bring it to front when a second
# launch attempt occurs.
_window_ref = None

# One-shot flag: the tray sets this (POST /api/voice/open) when the wake word
# fires but a window is ALREADY open, so it can't spawn a fresh ?voice=1 window.
# The GUI's /api/state poll reads-and-clears it and enters voice mode in place.
_pending_voice_open = False
# A chat the tray asked the open window to show (an id, or "new"); one-shot.
_pending_open_chat = ""

# Same shape for "surface the window": a second launch POSTs /api/focus, but the
# window lives in the desktop_attach SUBPROCESS so this process's _window_ref is
# None and _focus_window() can't raise it. The GUI (running IN the window) reads
# this flag off /api/state and calls its own pywebview focus bridge, which is the
# only caller that beats Windows' foreground lock.
_pending_focus = False


def set_window_ref(win) -> None:
    """desktop.py calls this so /api/focus can surface the existing window."""
    global _window_ref
    _window_ref = win


# The tray registers a callback here that OPENS the desktop window if none is
# open. Closing the window to the tray kills the window subprocess, so the
# `_pending_focus` flag alone can't surface anything — nobody's polling it. When
# a second launch asks us to focus, we call this so the tray spawns a fresh
# window (it no-ops if a window is already up). Backend + tray share a process,
# so this direct call works.
_surface_cb = None


def set_surface_callback(cb) -> None:
    """Tray hook: called on /api/focus to open the window if it's tray-hidden."""
    global _surface_cb
    _surface_cb = cb


def _surface_window() -> None:
    cb = _surface_cb
    if cb is None:
        return
    try:
        cb()
    except Exception:
        pass


def _take_pending_open_chat() -> str:
    global _pending_open_chat
    cid, _pending_open_chat = _pending_open_chat, ""
    return cid


def _take_pending_voice_open() -> bool:
    """Read-and-clear the wake-into-voice flag. The GUI's /api/state poll uses
    this to enter voice mode in an already-open window (the tray can't spawn a
    second ?voice=1 window when one is up)."""
    global _pending_voice_open
    if _pending_voice_open:
        _pending_voice_open = False
        return True
    return False


def _take_pending_focus() -> bool:
    """Read-and-clear the surface-the-window flag. The GUI's /api/state poll uses
    this to raise its own native window (via the pywebview bridge) when a second
    launch asked the primary to come forward — the backend can't do it itself
    because the window is in another process."""
    global _pending_focus
    if _pending_focus:
        _pending_focus = False
        return True
    return False


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
_ROOM_JS = os.path.join(_HERE, "room.js")
_CALL_AUDIO_JS = os.path.join(_HERE, "call_audio.js")
LOCAL_API_BASE = os.getenv("LOCAL_API_BASE", _local_base())
LM_STUDIO_V0 = LOCAL_API_BASE.replace("/v1", "/api/v0")
SETTINGS_PATH = os.path.join(WORKSPACE, "settings.json")
CONVOS_DIR = os.path.join(WORKSPACE, "conversations")

# Set when the user hits Stop in the GUI; run_once checks it between turns and
# bails. Cleared at the start of each /chat. Single-user local GUI = one
# generation at a time, so a single shared flag is enough.
_CANCEL = threading.Event()

# What a turn in this window is doing, in the words the window's own status
# uses ("thinking", "calling read_file", "writing", "idle"), and a tool waiting
# on a yes. The phone's room shows JARVIS from it.
_TURN_NOW: Dict[str, Any] = {"state": "idle", "waiting": "", "at": 0.0}


def _turn_saw(event: str, fields: Dict[str, Any]) -> None:
    if event == "permission_request":
        _TURN_NOW["waiting"] = str(fields.get("name") or "")
    elif event == "tool_call":
        _TURN_NOW.update(state=f"calling {fields.get('name') or ''}", waiting="")
    elif event == "assistant_chunk":
        _TURN_NOW.update(state="writing", waiting="")
    elif event == "tool_result":
        _TURN_NOW.update(state="thinking", waiting="")
    _TURN_NOW["at"] = time.time()


def room_now() -> Dict[str, Any]:
    """JARVIS's state for the phone's room: this window's turn, and one
    running in the terminal."""
    state, waiting = _TURN_NOW["state"], _TURN_NOW["waiting"]
    if state != "idle" and time.time() - _TURN_NOW["at"] > 900:     # a turn that never said it ended
        state, waiting = "idle", ""
    from . import activity
    return {"state": state, "waiting": waiting, "elsewhere": activity.current(not_from="window")}


# Steering queue: messages the user typed while a generation was in flight.
# run_once drains it between tool iterations and injects them as user turns.
# Single shared queue (one generation at a time in the local GUI), guarded by a
# lock since the HTTP handler thread appends while the run_once thread drains.
_STEER_Q: List[str] = []
_STEER_LOCK = threading.Lock()


def _drain_steer() -> List[str]:
    """Return and clear any queued steering messages. Passed to run_once."""
    with _STEER_LOCK:
        if not _STEER_Q:
            return []
        out = list(_STEER_Q)
        _STEER_Q.clear()
        return out

# Suppress the brief cmd console flash on every subprocess we spawn under
# a GUI/tray context. Worthless when running as CLI, harmless either way.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
os.makedirs(CONVOS_DIR, exist_ok=True)

# Phone-bridge subprocesses started from the GUI Connect pane (so the user
# clicks "Start" instead of running `python -m hearth.X_bridge`). Tracked by
# channel; each is the standalone bridge module run in its own process so it can
# block on its gateway/poll loop without touching the web server.
_BRIDGE_PROCS: dict = {}
_BRIDGE_MODULES = {
    "telegram": "hearth.telegram_bridge",
    "discord": "hearth.discord_bridge",
    "whatsapp": "hearth.whatsapp_bridge",
    "relay": "hearth.relay_bridge",          # Plus: the phone app, through Hearth's relay
}


def _bridge_running(ch: str) -> bool:
    p = _BRIDGE_PROCS.get(ch)
    return bool(p and p.poll() is None)


def _stop_bridge(ch: str) -> None:
    p = _BRIDGE_PROCS.pop(ch, None)
    try:
        if p and p.poll() is None:
            p.terminate()
    except Exception:
        pass


def _bridge_config(ch: str) -> dict:
    """Read a bridge's saved config from ~/.hearth, {} if none."""
    fn = {"telegram": "phone_bridge.json", "discord": "discord_bridge.json",
          "whatsapp": "whatsapp_bridge.json"}.get(ch)
    if not fn:
        return {}
    try:
        with open(os.path.join(os.path.expanduser("~"), ".hearth", fn), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _bridge_configured(ch: str, cfg: Optional[dict] = None) -> bool:
    if ch == "relay":
        # Plus on this PC and a phone paired (or being paired) with it.
        from . import plus as _plus, relay_bridge as _rb
        return bool(_rb.relay_url() and _plus.active() and (_rb.phones() or _rb.pairing_status()["state"] != "none"))
    cfg = cfg if cfg is not None else _bridge_config(ch)
    if ch == "whatsapp":
        return bool(cfg.get("allowed_numbers") or cfg.get("allow_self_chat"))
    return bool((cfg.get("bot_token") or "").strip())


def _spawn_bridge(ch: str) -> Tuple[bool, Optional[str]]:
    """Launch a bridge as its own process. Returns (running, error)."""
    mod = _BRIDGE_MODULES.get(ch)
    extra: list = []
    if not mod and ch.startswith("custom:"):
        # A connector of the person's own, from <workspace>/connectors/<name>.py.
        from . import custom_connector as _cust
        name = ch.split(":", 1)[1]
        if name not in _cust.available():
            return False, f"no connector called {name}"
        mod, extra = "hearth.custom_connector", [name]
    if not mod:
        return False, "unknown channel"
    if _bridge_running(ch):
        return True, None
    args = [sys.executable, "-m", mod] + extra
    if getattr(sys, "frozen", False):
        # In the packaged app sys.executable is Hearth.exe; the sentinel
        # runs the bundled interpreter against the module.
        args = [sys.executable, "--hearth-run-python", "-m", mod] + extra
    # Pin the bridge to the CURRENT brain from settings, overriding any launch
    # env (e.g. grok.ps1). Otherwise a bridge stays on the old provider after a
    # GUI brain switch — the "auth failed on Telegram after switching" bug.
    env = dict(os.environ)
    try:
        _s = _load_settings()
        if _s.get("llm_url"):   env["LOCAL_API_BASE"] = _s["llm_url"]
        if _s.get("llm_key"):   env["LOCAL_API_KEY"] = _s["llm_key"]
        if _s.get("llm_model"): env["LOCAL_MODEL"] = _s["llm_model"]
    except Exception:
        pass
    # The child writes to its log file, never to a pipe: nothing drains a pipe
    # once the bridge is up, and a full one blocks the bridge's next print for
    # good. The same file is what the log view shows, and its fresh tail is
    # what explains a bridge that died on start.
    log_path = _bridge_log_path(ch)
    env["PYTHONUNBUFFERED"] = "1"
    # Where this window answers, for a bridge that changes the brain or settings
    # on someone's behalf (the phone app) through the window's own routes.
    try:
        from . import claude_brain as _cb
        if _cb._bridge_url:
            env["HEARTH_WINDOW_URL"] = _cb._bridge_url
    except Exception:
        pass
    # It ends when this process does (bridge_status.follow_parent).
    env["HEARTH_PARENT_PID"] = str(os.getpid())
    # The log is read as UTF-8; Windows would otherwise write its own code page.
    env["PYTHONIOENCODING"] = "utf-8"
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting {ch} bridge ---\n")
        log.flush()
        start = log.tell()
        p = subprocess.Popen(args, creationflags=_NO_WINDOW, env=env,
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    _BRIDGE_PROCS[ch] = p
    time.sleep(1.6)  # let it fail-fast on a bad token / missing dep
    if p.poll() is None:
        return True, None
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            f.seek(start)
            out = f.read()
    except OSError:
        out = ""
    return False, _bridge_failure_reason(ch, out)


_BRIDGE_LOG_KEEP = 512 * 1024


def _bridge_log_path(ch: str) -> str:
    """<workspace>/logs/<channel>_bridge.log, trimmed to its newest part."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, f"{ch.replace(':', '_')}_bridge.log")
    try:
        if os.path.getsize(path) > 2 * _BRIDGE_LOG_KEEP:
            with open(path, "rb") as f:
                f.seek(-_BRIDGE_LOG_KEEP, os.SEEK_END)
                tail = f.read()
            with open(path, "wb") as f:
                f.write(tail[tail.find(b"\n") + 1:])
    except OSError:
        pass
    return path


def _bridge_failure_reason(ch: str, out: str) -> str:
    """Turn the bridge's dying output into something worth acting on."""
    low = (out or "").lower()
    tail = " ".join((out or "").strip().splitlines()[-3:])[-300:]
    if "modulenotfounderror" in low or "no module named" in low:
        mod = ""
        m = re.search(r"no module named ['\"]([\w.]+)['\"]", low)
        if m:
            mod = m.group(1)
        pkg = {"discord": "discord.py", "telegram": "python-telegram-bot",
               "whatsapp": "the WhatsApp dependency"}.get(ch, mod or "a dependency")
        return (f"the {ch} bridge is missing a Python package"
                + (f" ({mod})" if mod else "")
                + f". Install {pkg} and start it again.")
    if any(k in low for k in ("unauthorized", "401", "invalid token", "improperbadtoken",
                              "logintfailure", "loginfailure", "invalid bot token")):
        return (f"the {ch} bridge was rejected by the service, so the token is "
                f"wrong or revoked. Paste a fresh one in Settings, Connectors.")
    if "conflict" in low or "terminated by other getupdates" in low:
        return (f"another copy of the {ch} bridge is already using this token. "
                f"Stop that one, or use a separate bot token.")
    if any(k in low for k in ("temporary failure in name resolution", "getaddrinfo",
                              "connection refused", "network is unreachable", "ssl")):
        return f"the {ch} bridge could not reach the network. Check the connection and retry."
    if tail:
        return f"the {ch} bridge exited right away: {tail}"
    return (f"the {ch} bridge exited right away and said nothing. Check the token "
            f"and that its dependency is installed.")


def _autostart_bridges() -> None:
    """On launch, bring any configured bridge ONLINE so the user never has to
    flip a Start toggle each session. A bridge is auto-started when it has a
    saved config AND `autostart` isn't explicitly turned off. Best-effort and
    silent — a bad token just leaves that one channel offline."""
    if os.environ.get("HEARTH_NO_BRIDGES") == "1":
        return                          # a test instance must not answer the real bots
    for ch in _BRIDGE_MODULES:
        try:
            cfg = _bridge_config(ch)
            if not _bridge_configured(ch, cfg):
                continue
            if cfg.get("autostart") is False:
                continue
            running, err = _spawn_bridge(ch)
            if not running and err:
                print(f"[hearth] {ch} bridge auto-start skipped: {err}", file=sys.stderr)
        except Exception as e:
            print(f"[hearth] {ch} bridge auto-start error: {type(e).__name__}: {e}", file=sys.stderr)


def _convo_path(cid: str) -> str:
    # Sanitize id to a safe filename. UI sends `c_<rand>` style ids.
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cid)[:80]
    return os.path.join(CONVOS_DIR, f"{safe}.json")


# The listing is rebuilt from the files, but a file is only parsed again when
# its size or modified time changes: parsing every chat on each call took
# 400 ms at 50 chats and grew with every chat kept.
_CONVO_META: Dict[str, Tuple[Tuple[float, int], Dict]] = {}
_CONVO_META_LOCK = threading.Lock()


def _list_convos() -> List[Dict]:
    out: List[Dict] = []
    if not os.path.isdir(CONVOS_DIR):
        return out
    seen = set()
    for fn in os.listdir(CONVOS_DIR):
        # A staged write ends in .json too, and one left behind by a killed save
        # holds a complete conversation, so it listed as a second copy of the
        # chat it was about to become.
        if not fn.endswith(".json") or _is_write_temp(fn):
            continue
        path = os.path.join(CONVOS_DIR, fn)
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = (st.st_mtime, st.st_size)
        seen.add(fn)
        with _CONVO_META_LOCK:
            hit = _CONVO_META.get(fn)
        if hit and hit[0] == key:
            out.append(dict(hit[1]))
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        meta = {
            "id": data.get("id", fn[:-5]),
            "title": data.get("title", "Untitled"),
            "updated": data.get("updated", 0),
            "created": data.get("created", 0),
            "msg_count": len(data.get("messages", [])),
            "pinned": bool(data.get("pinned")),
            "source": data.get("source") or "",       # "terminal" or "reminder" for chats made elsewhere
            "unread": bool(data.get("unread")),
        }
        with _CONVO_META_LOCK:
            _CONVO_META[fn] = (key, meta)
        out.append(dict(meta))
    with _CONVO_META_LOCK:
        for gone in set(_CONVO_META) - seen:
            _CONVO_META.pop(gone, None)
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


# Named and swept in tools, so the chat search index and anything else reading
# these folders applies the same rule.
_TMP_PREFIX = _WRITE_TEMP_PREFIX


def _sweep_stale_temp_files(directory: str, older_than_s: float = 300.0) -> int:
    return _sweep_write_temps(directory, older_than_s)


def _save_convo(data: Dict) -> bool:
    cid = data.get("id")
    if not cid:
        return False
    p = _convo_path(cid)
    try:
        _atomic_write_json(p, data, indent=2, ensure_ascii=False)
        live_bus.publish({"kind": "chat", "chat": cid})
        return True
    except OSError:
        return False


def _delete_convo(cid: str) -> bool:
    p = _convo_path(cid)
    if not os.path.isfile(p):
        return False
    try:
        os.remove(p)
        live_bus.publish({"kind": "chat", "chat": cid, "gone": True})
        return True
    except OSError:
        return False

# ---------------------------------------------------------------------------
# Small caches so the UI's status polling doesn't melt anything
# ---------------------------------------------------------------------------

_gpu_cache: Dict[str, Any] = {"ts": 0, "data": None}
_models_cache: Dict[str, Any] = {"ts": 0, "data": None}
# Set when a CLOUD model-list fetch fails/empties (e.g. xAI 403s) so the GUI can
# explain why the picker only shows the active model, instead of looking broken.
_models_notice: str = ""

# ----- Permission system -----
# When a risky tool fires during /chat, the bridge calls permission_check
# which emits a `permission_request` NDJSON event, parks on a per-id queue,
# and waits for the user to POST /api/permission with their decision.
# `_always_allow` / `_always_deny` persist to disk (same file the CLI uses) so
# [a]lways / [N]ever survive restarts and are shared between CLI and GUI.
import queue as _queue
_permission_queues: Dict[str, _queue.Queue] = {}
_PERMS_FILE = os.path.join(WORKSPACE, "permissions.json")
# Background GPU-voice install state (the Settings "Install GPU support" button).
_VOICE_INSTALL: Dict[str, Any] = {"running": False}


_VOICE_INSTALL_CANCEL = threading.Event()


def _install_voice_models_worker() -> None:
    """Download the Kokoro weights and the speech model, publishing byte-level
    progress on _VOICE_INSTALL so the downloads overlay can show size, speed and
    ETA and offer Cancel, the same as every other download."""
    global _VOICE_INSTALL
    _VOICE_INSTALL_CANCEL.clear()
    # `done` stays the completion flag the existing poller checks; byte counts
    # get their own keys so the two never mean different things.
    _VOICE_INSTALL = {"running": True, "device": "models", "pct": 0,
                      "bytes_done": 0, "bytes_total": 0, "step": "starting"}

    def _prog(step, done, total):
        _VOICE_INSTALL["step"] = step
        _VOICE_INSTALL["bytes_done"] = done
        _VOICE_INSTALL["bytes_total"] = total
        _VOICE_INSTALL["pct"] = (done * 100 // total) if total else None

    def _cancelled():
        return _VOICE_INSTALL_CANCEL.is_set()

    try:
        ok = bool(_voice and _voice.download_models(_prog, _cancelled))
        err = ""
        try:
            err = (_voice.status().get("last_load_error") or "") if _voice else ""
        except Exception:
            pass
        # The speech model downloads on the first mic use the same way, which
        # blocks that first use with nothing on screen. Fetch it here too so one
        # action sets up both halves of voice.
        if ok and _listen is not None and not _cancelled():
            try:
                if not _listen.model_present():
                    _prog("speech model", 0, 0)
                    if not _listen.ensure_model():
                        ok = False
                        err = (_listen.status().get("last_load_error")
                               or "speech model download failed")
            except Exception as e:
                ok = False
                err = f"speech model: {type(e).__name__}: {e}"
        cancelled = _cancelled()
        _VOICE_INSTALL = {"running": False, "done": True,
                          "ok": ok and not cancelled, "cancelled": cancelled,
                          "device": "models", "pct": 100 if ok else None,
                          "msg": ("Voice files ready." if (ok and not cancelled)
                                  else ("Cancelled." if cancelled
                                        else (err or "Download failed.")))[-400:]}
    except Exception as e:
        _VOICE_INSTALL = {"running": False, "done": True, "ok": False,
                          "device": "models",
                          "msg": f"{type(e).__name__}: {e}"[-400:]}


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

# Voice turns: a risky step goes back to the brain to be asked about in its own words.
# Its card stays on screen meanwhile (id -> the step); a tap on Allow there lets that
# exact step run when the brain calls it again, as a spoken yes does for the rest.
_VOICE_ASKS: Dict[str, Dict[str, Any]] = {}
_VOICE_TAPPED: set = set()


def _step_key(name: str, args: Any) -> str:
    from .call import step_key
    return step_key(name, args)


def _brain_says_why() -> bool:
    from .call import brain_says_why
    return brain_says_why()


def _voice_permission_check(check, emit_fn, prompt: str, history) -> Any:
    from .call import voice_permission_check
    return voice_permission_check(check, emit_fn, prompt, history, asks=_VOICE_ASKS, tapped=_VOICE_TAPPED,
                                  always_allow=_always_allow, always_deny=_always_deny, says_why=_brain_says_why)


def _answer_voice_ask(rid: str, decision: str) -> bool:
    """A tap on a voice turn's card. Allow lets that exact step run when the brain
    calls it again; the page then sends the yes (or the no) as the answer."""
    ask = _VOICE_ASKS.pop(rid, None)
    if ask is None:
        return False
    if decision in ("allow", "always"):
        _VOICE_TAPPED.add(ask["key"])
        if decision == "always":
            _always_allow.add(ask["name"]); _always_deny.discard(ask["name"]); _save_perms_to_disk()
    elif decision == "never":
        _always_deny.add(ask["name"]); _always_allow.discard(ask["name"]); _save_perms_to_disk()
    return True


def _make_permission_check(emit_fn):
    """Returns a callback the bridge can hand each risky tool call to. Also
    wires up the extend-workspace callback used by tools._resolve_write so
    out-of-workspace writes prompt the user instead of silently raising."""
    def _check(name: str, args: Dict) -> str:
        if name in _always_deny:  return "deny"
        # The approval mode picked in the chat bar (Ask / Auto-edit / Auto).
        _out = ""
        try:
            from . import tools as _tl
            if _tl.mode_allows(name, args):
                return "allow"
            _out = _tl.sends_out(name, args)
        except Exception:
            pass
        # A standing yes for the tool doesn't cover sending data out after reading from outside.
        if name in _always_allow and not _out: return "allow"
        req_id = f"perm_{int(time.time()*1000)}_{name}"
        _permission_queues[req_id] = _queue.Queue()
        try:
            try:
                from . import auto_review as _ar
                _why = _ar.why(name, args)        # the reviewer's or the floor's reason, '' for none
            except Exception:
                _why = ""
            emit_fn("permission_request", id=req_id, name=name, args=args, **({"why": _why} if _why else {}))
            try:
                decision = _permission_queues[req_id].get(timeout=180.0)
            except _queue.Empty:
                # No click in time. This is NOT a decline - the user just didn't
                # answer. Report it distinctly so the model can't read a silent
                # timeout as "approved" and claim the action happened.
                decision = "timeout"
        finally:
            _permission_queues.pop(req_id, None)
        _reason = ""
        if isinstance(decision, dict):
            _reason = (decision.get("reason") or "").strip()
            decision = decision.get("decision") or "deny"
        if decision == "always" and _out: return "allow"          # this request only
        if decision == "always": _always_allow.add(name); _save_perms_to_disk(); return "allow"
        if decision == "never":  _always_deny.add(name);  _save_perms_to_disk(); return "deny"
        if decision == "aside":
            # Something else was said while this waited (voice): not a no. A brain that
            # can't hear why still gets the words, as a note in the turn.
            from .call import aside_note
            if not _brain_says_why() and _reason:
                with _STEER_LOCK:
                    _STEER_Q.append(_reason)
            return {"decision": "aside", "reason": aside_note(_reason)}
        # A decline WITH the user's reason → hand both back so the model is told why.
        if decision == "deny" and _reason:
            return {"decision": "deny", "reason": _reason}
        return decision

    def _extend(path: str) -> bool:
        # Re-uses the same permission queue + emit channel. The GUI keys
        # off name="__extend_workspace__" to render a different prompt
        # ("allow the assistant to write outside ~/Jarvis?") with the path as args.
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


def _resolve_permission(req_id: str, decision: str, reason: str = "") -> bool:
    q = _permission_queues.get(req_id)
    if q is None:
        return False
    try:
        # Carry the user's typed reason with a decline so the model gets it in the
        # tool result (a plain string when there's no reason keeps back-compat).
        q.put_nowait({"decision": decision, "reason": reason} if reason else decision)
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


def load_local_brain(model_path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load a model file on the built-in engine and make it the brain, with the
    settings the person tuned for it last time (body overrides any of them).
    The Models tab's Use this and the load_model tool both come here."""
    global LOCAL_API_BASE
    from . import llmserver
    body = body or {}
    model_path = (model_path or body.get("model_path") or "").strip()
    # Start from the saved per-model config the user tuned last time, so a
    # plain "Use this" honors their saved 32/32 / context / quant instead of
    # silently dropping to Auto. The request body still wins per field (the
    # Models tab expansion can override any of these for this one load);
    # then Settings → Default context window; then 32K as the final floor.
    saved = llmserver.get_model_config(model_path) or {}
    def _cfg(k, d=None):
        v = body.get(k)
        return v if v is not None else saved.get(k, d)
    ctx        = int(_cfg("ctx") or _load_settings().get("server_default_ctx") or 32768)
    _ng        = _cfg("n_gpu_layers", -1)
    n_gpu      = int(_ng if _ng is not None else -1)
    n_threads  = _cfg("n_threads")
    n_threads  = int(n_threads) if n_threads not in (None, "", "auto") else None
    ck         = (_cfg("cache_type_k") or "").strip() or None
    cv         = (_cfg("cache_type_v") or "").strip() or None
    flash      = bool(_cfg("flash_attn", True))
    extra_args = (_cfg("extra_args") or "").strip() or None
    # `force=true` from the GUI bypasses the VRAM guardrail — opt-in
    # only, after the user clicks "Force load anyway" in the modal.
    # llama.cpp will spill weights to system RAM (slow but boots).
    force      = bool(body.get("force", False))
    result = llmserver.start_builtin(
        model_path, ctx=ctx, n_gpu_layers=n_gpu,
        n_threads=n_threads, cache_type_k=ck, cache_type_v=cv,
        flash_attn=flash, force=force, extra_args=extra_args,
    )
    # On successful start, retarget the GLOBAL LLM endpoint to the
    # builtin URL so the topbar dropdown + /api/models + chat all
    # talk to it. Without this, the topbar keeps showing "no model
    # loaded" because it still queries LM Studio's port.
    if result.get("ok") and result.get("url"):
        # `_hl` (hearth.headless) is imported lazily inside the
        # /api/llm-endpoint branch above, so it's not visible to us
        # here. Import locally so the chat client picks up the new
        # base. Cheap — Python caches.
        from . import headless as _hl
        LOCAL_API_BASE = result["url"]
        _hl.LOCAL_API_BASE = LOCAL_API_BASE
        # Drop the model-list cache so the sticker reflects the model
        # that just loaded instead of serving the previous one until
        # the TTL lapses.
        _models_cache["ts"] = 0
        _models_cache["data"] = None
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
        llmserver.invalidate_status_cache()  # instant-fresh status/state
        # Sticky default: remember the model the user just picked so
        # the NEXT launch auto-boots it (see _auto_boot_preferred_model_async).
        # Setting persists in ~/Jarvis/settings.json as preferred_model.
        try:
            saved = _load_settings()
            if saved.get("preferred_model") != model_path or saved.get("last_local_model") != model_path:
                saved["preferred_model"] = model_path
                # Kept through an eject: what to start when voice or
                # chat needs a model and none is set to autoboot.
                saved["last_local_model"] = model_path
                os.makedirs(os.path.dirname(SETTINGS_PATH) or WORKSPACE, exist_ok=True)
                _atomic_write_json(SETTINGS_PATH, saved, indent=2)
        except Exception:
            pass  # best-effort — preference is a nice-to-have
    return result


def _local_kept() -> str:
    try:
        from . import llmserver as _ls
        if not _load_settings().get("keep_local_warm") or not (_ls._proc and _ls._proc.poll() is None):
            return ""
        path = (_ls._proc_info or {}).get("model_path") or ""
        return os.path.splitext(os.path.basename(path))[0]
    except Exception:
        return ""


_GET_REFUSED: Dict[str, str] = {}      # url -> what the server said when it refused


def _http_get_json(url: str, timeout: float = 3,
                   headers: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    try:
        req = urllib.request.Request(url, headers=(headers or {}))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _GET_REFUSED.pop(url, None)
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Kept so the page can say why ("API key not valid"), not guess.
        try:
            body = json.loads(e.read().decode("utf-8", "replace") or "{}")
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, list) and err:
                err = err[0]
            msg = (err.get("message") if isinstance(err, dict) else err) or body.get("message") or ""
        except Exception:
            msg = ""
        _GET_REFUSED[url] = str(msg or f"HTTP {e.code}")[:200]
        return None
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


def _active_base() -> str:
    """The endpoint the chat is ACTUALLY routed to right now.

    Single source of truth for "where do requests go". Mirrors the live
    module global (updated atomically by every brain-switch / start / stop /
    eject path) and falls back to the env var, then the LM Studio default.
    Used to decide local-vs-cloud behavior consistently across /chat,
    _load_model, _eject_model, and /api/state so they never disagree."""
    return (LOCAL_API_BASE or os.getenv("LOCAL_API_BASE")
            or _local_base())


def _provider_key(provider: str) -> str:
    """The key saved for a cloud provider, wherever it was saved: the one in
    use, the ones kept from earlier switches, or the terminal's own file."""
    provider = (provider or "").strip().lower()
    if not provider:
        return ""
    s = _load_settings()
    if (s.get("llm_provider") or "").strip().lower() == provider and (s.get("llm_key") or "").strip():
        return s["llm_key"].strip()
    banked = (s.get("provider_keys") or {}).get(provider)
    if banked:
        return str(banked).strip()
    try:
        with open(os.path.join(WORKSPACE, "brain_keys.json"), encoding="utf-8") as f:
            return str(((json.load(f) or {}).get(provider) or {}).get("key") or "").strip()
    except (OSError, ValueError):
        return ""


def _saved_providers() -> List[Dict[str, Any]]:
    """The cloud providers a key is saved for, the one in use first. Never the keys."""
    from .headless import PROVIDERS
    s = _load_settings()
    active = (s.get("llm_provider") or "").strip().lower()
    out = []
    for pid, (label, url) in PROVIDERS.items():
        if _provider_key(pid):
            out.append({"id": pid, "label": label, "url": url, "active": pid == active,
                        "model": (s.get("llm_model") or "") if pid == active else ""})
    if active == "custom" and (s.get("llm_url") or "").strip():
        out.append({"id": "custom", "label": "Custom endpoint", "url": s["llm_url"].strip(), "active": True,
                    "model": s.get("llm_model") or ""})
    out.sort(key=lambda p: not p["active"])
    return out


def _facts_noticed() -> List[Dict[str, Any]]:
    try:
        from . import memory_extract as _mx
        return list(_mx.NOTICED[-5:])
    except Exception:
        return []


def status_snapshot() -> Dict[str, Any]:
    """What /status shows, the same for the window and the terminal: what is
    thinking and on what, the approval switch, each plan's usage windows as the
    agent reports them, the local engine, and the helpers."""
    from . import claude_brain as cb, headless as hl
    from .tools import approval_mode
    from .updater import HEARTH_VERSION
    s = _load_settings()
    agent = cb.current()
    if agent:
        info = cb.agent_models(agent)
        m = next((x for x in info["models"] if x["id"] == (info.get("model") or "")), None)
        brain = {"kind": "agent", "id": agent, "label": cb.BRAINS[agent],
                 "model": (m["label"] if m and m["id"] else "its default model"),
                 "where": f"your {cb.PLANS.get(agent, '')} plan, through {cb.BRAINS[agent]} on this PC"}
    else:
        pid = hl.provider_for(LOCAL_API_BASE)
        if pid == "local":
            loaded = _detect_loaded() or {}
            brain = {"kind": "local", "id": "local", "label": "This PC", "model": loaded.get("id") or "",
                     "where": LOCAL_API_BASE}
        else:
            brain = {"kind": "cloud", "id": pid or "custom",
                     "label": hl.PROVIDERS[pid][0] if pid in hl.PROVIDERS else "A server",
                     "model": s.get("llm_model") or "", "where": "your API key"}
    from . import usage as _usage
    try:
        limits = _usage.everything(_provider_key)
    except Exception:
        limits = []
    try:
        from . import subagents as _sa
        team = _sa.team_state()
    except Exception:
        team = []
    try:
        from . import llmserver as _ls
        st = _ls.status(LOCAL_API_BASE)
        hs = _ls.helper_status()
        local = {"running": bool(st.get("builtin_running") or st.get("external_running")),
                 "model": os.path.basename(str(st.get("builtin_model") or "")),
                 "ctx": (getattr(_ls, "_proc_info", {}) or {}).get("ctx"),
                 "helper": os.path.basename(str(hs.get("model") or "")) if hs.get("running") else ""}
    except Exception:
        local = {"running": False, "model": "", "ctx": None, "helper": ""}
    try:
        ctx, ctx_src = hl.resolve_context_tokens(brain.get("model") or "")
    except Exception:
        ctx, ctx_src = None, ""
    if agent:
        ctx = cb.context_window(agent) or ctx
    return {"version": HEARTH_VERSION, "brain": brain, "context": ctx, "context_source": ctx_src,
            "mode": approval_mode(), "limits": limits, "local": local,
            "helpers": {"working": sum(1 for a in team if a.get("status") == "working"),
                        "done": sum(1 for a in team if a.get("status") == "done")},
            "memories": len(_memory_index()), "workspace": WORKSPACE}


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
    return LOCAL_API_BASE or _local_base()


def _port_reachable(base_url: str, timeout: float = 0.6) -> bool:
    from . import headless as _hl
    return _hl.port_reachable(base_url, timeout)


def _list_models() -> List[Dict]:
    """Return model list from whichever endpoint is currently active.

    LM Studio: pull from /api/v0/models (rich metadata).
    Builtin llama-cpp-python server: synthesize a single entry from _proc_info
        (the v1/models endpoint exists but exposes almost nothing useful).
    Anything else (Ollama, cloud, custom): use plain /v1/models and mark loaded.
    Cached 4s either way.
    """
    global _models_notice
    now = time.time()
    # A list is only good for the endpoint it was read from: one worked out
    # while the brain was being switched belongs to the brain before.
    if (_models_cache["data"] is not None and now - _models_cache["ts"] < 4
            and _models_cache.get("base") == _models_probe_endpoint().rstrip("/")):
        return _models_cache["data"]

    _models_notice = ""  # cleared each fresh compute; set only if a cloud list fails
    out: List[Dict] = []
    # The endpoint we'll actually probe — may differ from LOCAL_API_BASE if
    # the chat is currently routed to a cloud endpoint. See _models_probe_endpoint.
    # rstrip the trailing slash — Gemini's base is ".../v1beta/openai/", so a
    # naive base + "/models" yields a double slash that Google 404s (the picker
    # then looked empty / stuck on a single preset model).
    probe_base = _models_probe_endpoint().rstrip("/")

    # Fast-fail a dead LOCAL endpoint before the multi-second probe chain below.
    # When no server is up, the status()/v0/v1 probes each stall ~2s on the
    # delayed-refuse (see _port_reachable), which was the real "Getting Hearth
    # ready" launch hang. Cloud endpoints answer on 443 and their /models is the
    # signal we still want, so skip the pre-check for them.
    _pb = probe_base.lower()
    _is_local_probe = any(h in _pb for h in
                          ("localhost", "127.0.0.1", "0.0.0.0", "::1"))
    if _is_local_probe and not _port_reachable(probe_base, timeout=0.6):
        _models_cache.update(ts=now, data=[], base=probe_base)
        return []

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
                "publisher": "llama.cpp (Inglen built-in)",
                "state": "loaded",
                "loaded_context_length": builtin_ctx,
                "max_context_length": builtin_ctx,
                "quantization": "",
                "capabilities": [],
            })
            _models_cache.update(ts=now, data=out, base=probe_base)
            return out
    except Exception:
        pass

    # 2) LM Studio v0 (rich metadata path). Pass the Bearer key so an
    # auth-gated builtin/cloud server doesn't 401-storm the log on every
    # GUI poll (this was 5-10 spurious 401s in server-log per second
    # because the GUI polls /api/models every 9s + Hearth's internal probes).
    # Only a server on this machine can be LM Studio: a cloud address isn't asked
    # (it would only 404, and it would be sent the key for nothing).
    v0_url = probe_base.replace("/v1", "/api/v0") + "/models"
    try:
        _v0_key = (_load_settings().get("llm_key") or "").strip() or os.environ.get("LOCAL_API_KEY") or "hearth-builtin"
    except Exception:
        _v0_key = os.environ.get("LOCAL_API_KEY") or "hearth-builtin"
    data = (_http_get_json(v0_url, headers={"Authorization": f"Bearer {_v0_key}"})
            if _is_local_probe else None)
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
        _models_cache.update(ts=now, data=out, base=probe_base)
        return out

    # 3) Generic OpenAI-compatible /v1/models — assume the first one is loaded.
    # Try with the configured API key so an auth-gated server (builtin /
    # Gemini / etc.) actually answers instead of silently returning empty.
    # Fall back to the saved brain key. Env vars don't survive a restart, but the
    # cloud key persists in settings (llm_key) — without this, after a relaunch a
    # cloud provider (Groq/OpenAI/...) gets "hearth-builtin", 401s, and the model
    # list comes back empty even though the brain itself works fine.
    # The brain saved in Settings is what the list is for, so its key goes first;
    # the environment's is for a server started from a terminal.
    try:
        api_key = (_load_settings().get("llm_key") or "").strip() or None
    except Exception:
        api_key = None
    api_key = api_key or os.environ.get("LOCAL_API_KEY") or "hearth-builtin"
    from .headless import model_list_request
    _list_url, _list_hdr = model_list_request(probe_base, api_key)
    _raw_models = _http_get_json(_list_url, headers=_list_hdr, timeout=6)
    data = _raw_models or {"data": []}
    # Skip non-chat models — xAI lists grok-imagine-image / grok-imagine-video
    # alongside chat models, but they 400 on /v1/chat/completions because
    # they use /v1/images/generations and /v1/videos/generations instead.
    # Hide them from the chat-model picker; they're still callable via the
    # generate_image / generate_video TOOLS once a chat model is selected.
    from .headless import NOT_CHAT as NON_CHAT_PATTERNS
    # Collect the chat-capable rows first, then flag the one the user actually
    # picked (settings.llm_model) as "loaded" — NOT just index 0. The old
    # "loaded if i==0" hardcode meant the topbar sticker reverted to the
    # provider's first model on every poll, so a swap to grok-4.3 wouldn't
    # stick. Fall back to the first row only if the saved pick isn't listed.
    try:
        _saved_now = _load_settings()
        _picked = (_saved_now.get("llm_model") or "").strip().lower()
    except Exception:
        _saved_now, _picked = {}, ""
    _picked_whole = _picked
    _h = probe_base.lower()
    _is_cloud = not any(x in _h for x in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))
    # Compare like-for-like. Ids below are basenamed (llama.cpp reports the full
    # --model path), but llm_model is often stored AS a full path, so the raw
    # comparison never matched and _loaded_idx silently kept its 0 default —
    # marking whatever the server happened to list first as the loaded one.
    _picked = os.path.basename(_picked.replace("\\", "/")) or _picked
    # The running built-in server is better evidence than the settings file,
    # which can still name the model that was loaded before the last swap.
    try:
        from . import llmserver as _lsv
        _bi = _lsv.status().get("builtin_model") or ""
        if _bi:
            _picked = os.path.basename(_bi.replace("\\", "/")).lower()
    except Exception:
        pass
    rows = []
    for m in data.get("data", []):
        raw_id = m.get("id") or ""
        if any(p in raw_id.lower() for p in NON_CHAT_PATTERNS):
            continue
        # llama_cpp.server identifies models by the FULL --model path. Strip
        # to basename here so the topbar shows "model.gguf" not "C:\Users\...".
        # A provider's id is kept whole: "vendor/model" is the name it answers to.
        clean_id = raw_id if _is_cloud else (os.path.basename(raw_id.replace("\\", "/")) or raw_id)
        rows.append((clean_id, m))
    # A known provider with no usable model saved (none, or a local file's
    # name) takes the first one it lists, so the name shown is the one used.
    _prov_now = (_saved_now.get("llm_provider") or "").strip().lower()
    from .headless import PROVIDERS as _known
    if rows and _is_cloud and _prov_now in _known and (not _picked_whole or _picked_whole.endswith(".gguf")):
        try:
            _save_settings({"llm_model": rows[0][0]})
            _picked = _picked_whole = rows[0][0].lower()
        except Exception:
            pass
    # Honest surface: if a CLOUD provider gave us nothing (403/no response), tell
    # the user WHY the picker is sparse instead of showing a misleading catalog.
    if not rows and _is_cloud:
        _prov = ("xAI Grok" if "api.x.ai" in _h else "OpenAI" if "api.openai.com" in _h
                 else "Gemini" if "googleapis" in _h else "Anthropic" if "anthropic" in _h else "the provider")
        _said = _GET_REFUSED.get(_list_url, "")
        if _said and "key" in _said.lower() and any(w in _said.lower() for w in ("invalid", "not valid", "incorrect")):
            _models_notice = f"{_prov} didn't accept the API key."
        elif _said:
            _models_notice = f"{_prov} didn't list its models: {_said}"
        else:
            _models_notice = f"{_prov} sent no model list. The model in use still works."
    else:
        _models_notice = ""
    _loaded_idx = 0
    for idx, (cid, _m) in enumerate(rows):
        if _picked and (cid.lower() in (_picked, _picked_whole) or cid.lower().rsplit("/", 1)[-1] == _picked):
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
    _models_cache.update(ts=now, data=out, base=probe_base)
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
        result = llmserver.stop_builtin()  # already invalidates the status cache
        _models_cache["ts"] = 0
        _models_cache["data"] = None
        # Revert endpoint back to the user's configured default so the next
        # message doesn't hit a dead URL.
        from . import headless as _hl
        settings = _load_settings()
        saved_url = (settings.get("llm_url") or "").strip()
        new_base = saved_url or _local_base()
        LOCAL_API_BASE = new_base
        _hl.LOCAL_API_BASE = new_base
        os.environ["LOCAL_API_BASE"] = new_base
        # Eject means "stop AND stop wanting this": clear preferred_model so
        # the next launch's autoboot doesn't silently re-load what the user
        # just ejected. (Mirrors /api/llmserver/stop.) Without this, the
        # builtin would resurrect itself on the next start and /api/state
        # would re-report a model the user thought they killed.
        try:
            if settings.get("preferred_model"):
                settings["preferred_model"] = ""
                _save_settings(settings)
        except Exception:
            pass
        return {"ok": bool(result.get("ok")), "via": "builtin-stop",
                "freed_vram": True, **result}

    loaded = _detect_loaded()
    if not loaded:
        return {"ok": True, "msg": "nothing loaded"}
    mid = loaded["id"]
    r = _http_post_json(f"{LM_STUDIO_V0}/models/unload", {"model": mid}, timeout=60)
    if r and not r.get("error"):
        _models_cache["ts"] = 0
        _models_cache["data"] = None
        # LM Studio eject doesn't flip our builtin flag, so the status cache
        # key is unchanged — invalidate explicitly or the Models tab keeps
        # showing the ejected model as "loaded" for up to the 8s TTL.
        llmserver.invalidate_status_cache()
        return {"ok": True, "via": "rest"}
    lms = _find_lms_cli()
    if not lms:
        return {"ok": False, "error": "no REST + no `lms` CLI"}
    try:
        proc = subprocess.run([lms, "unload", "--all"],
                              capture_output=True, text=True, timeout=60,
                              creationflags=_NO_WINDOW)
        _models_cache["ts"] = 0
        _models_cache["data"] = None
        llmserver.invalidate_status_cache()
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
    """Load, memory and temperature for every graphics card, any vendor."""
    now = time.time()
    if _gpu_cache["data"] is not None and now - _gpu_cache["ts"] < 2:
        return _gpu_cache["data"]
    try:
        from . import gpu as _gpu
        gpus = _gpu.snapshot()
        data = {"available": bool(gpus), "gpus": gpus} if gpus else {"available": False, "error": "no GPU detected"}
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
        "sounds": True,        # a soft cue when a turn finishes, a low one on an error
        "voice_mic_hint_dismissed": False,  # first-voice mic tip, "don't show again"
        "voice_window_call": False,  # True: voice mode listens and speaks in the page (echo cancelled)
        "voice_half_duplex": False,  # hold the mic closed while speaking, for speakers
        "voice_tones": True,         # two soft notes when voice mode is ready, and when it ends
        "stt_device": "cpu",   # cpu / cuda
        "tts_device": "cpu",   # cpu / cuda / dml
        "voice_speed": 1.0,    # Kokoro playback rate (0.5x-2.5x)
        "voice_name": "am_michael",  # Kokoro voice id (am_/af_/bm_/bf_ + name). am_michael = calm neutral baseline.
        "agent_name": __import__("hearth").default_agent_name(),  # User-renameable persona. Settings → Behavior. Drives chat avatar letter (first char), assistant label, persona prompt, AND workspace folder (~/<agent_name>/). Rename rules in /api/agent/rename.
        "stt_model": "base.en",  # a name ending .en holds English only
        "stt_language": "",      # blank = detect per utterance
        # Memory bookkeeping runs on most turns, so its cards can outnumber
        # the reply. Display only; the events are still recorded either way.
        "show_memory_tools": True,
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
        # Concurrent request slots: 0 = from graphics memory (llmserver.get_parallel_slots).
        # A number counts only with server_parallel_slots_chosen: the old default of 1 was
        # written to every settings file by the first save and then read as a choice.
        "server_parallel_slots": 0,
        "server_parallel_slots_chosen": False,
        "keep_local_warm": False,       # when switching to cloud, keep the local builtin loaded? Off = free VRAM (default). On = leave it warm so quick swap-back doesn't reload (~10s tax). Power-user toggle.
        # ---- Toast quiet-mode toggles (user-controlled noise level) ----
        "toast_facts_saved": True,      # "Remembered N facts" on auto-extract
        "toast_compacted":   True,      # "Context compacted, now X%"
        "toast_errors":      True,      # Hard errors (always recommended)
        "server_idle_min": 15,          # auto-unload after N idle minutes (0 = never)
        "server_port": 8790,            # builtin HTTP port (its own: LM Studio keeps 1234; see local_engine)
        # Forge / SD-WebUI install path for local image gen. "" = either
        # not installed or use whatever _autodetect_forge_dir picks. Set
        # via Settings → Behavior or the Detect button.
        "forge_dir": "",
    }
    from .call_me import DEFAULTS as _call_me   # whether and why the agent may call the phone
    defaults.update(_call_me)
    if not os.path.isfile(SETTINGS_PATH):
        return defaults
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        defaults.update(saved)
    except Exception:
        pass
    # Apply device prefs as env vars BEFORE models load
    _env.set("STT_DEVICE", defaults.get("stt_device", "cpu"))
    _env.set("STT_MODEL", defaults.get("stt_model",  "base.en"))
    _env.set("STT_LANGUAGE", defaults.get("stt_language", ""))
    _env.set("TTS_DEVICE", defaults.get("tts_device", "cpu"))
    _env.set("VOICE_SPEED", str(defaults.get("voice_speed", 1.0)))
    _env.set("VOICE", defaults.get("voice_name", "am_michael"))
    _apply_server_port(defaults.get("server_port"))
    # Selected mic index (-1 = OS default). Drives both STT paths via
    # listen.input_device_index(). The mic picker (Settings → Voice) writes it.
    _env.set("VOICE_INPUT_DEVICE", str(defaults.get("voice_input_device", -1)))
    # The same pick by name, which is what the mic is looked up by.
    _env.set("VOICE_INPUT_NAME", str(defaults.get("voice_input_name", "") or ""))
    # Agent name → persona module reads this at import-time via
    # HEARTH_PERSONA_NAME. Setting it here means the very next chat turn
    # picks up the user's renamed agent. On boot, this fires before any
    # chat call so the persona is correct from turn 1.
    os.environ["HEARTH_PERSONA_NAME"] = (defaults.get("agent_name") or __import__("hearth").default_agent_name()).strip() or __import__("hearth").default_agent_name()
    return defaults


_PORT_FROM_ENV = bool(_env.get("BUILTIN_PORT", ""))     # set before start: it wins over Settings


def _apply_server_port(port) -> None:
    """Settings, Built-in server, Port: the engine starts there next time (a running one
    keeps its port). An environment BUILTIN_PORT set before start wins."""
    from . import local_engine
    try:
        port = int(port or 0)
    except (TypeError, ValueError):
        return
    if _PORT_FROM_ENV:
        return
    if not (1024 <= port <= 65535) or port == local_engine.LEGACY_PORT:
        port = local_engine.DEFAULT_PORT
    _env.set("BUILTIN_PORT", str(port))
    _env.set("HELPER_PORT", str(port + 1))
    try:
        from . import llmserver
        if llmserver._proc is None:
            llmserver.BUILTIN_PORT, llmserver.HELPER_PORT = port, port + 1
    except Exception:
        pass


# The GUI saves several keys at once in places (the reasoning control writes
# two), and each save was a read, a merge and a write with nothing between
# them. Two overlapping saves both read the same starting state and the second
# write dropped the first one's change. Measured before this lock: six of six
# concurrent pairs lost a write. Serialise the whole read-modify-write.
_SETTINGS_WRITE_LOCK = threading.Lock()


_FAVICON_DIR = os.path.join(os.path.expanduser("~"), ".hearth", "favicons")
_ICON_MAX = 512_000          # bigger than any sane icon; a file this size is cut off, not kept
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _site_of(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d if _DOMAIN_RE.match(d) else ""


def _favicon_cached(domain: str):
    """What the disk already knows about a site's icon, without the network:
    (bytes, mime), False when it has none (looked up within a day), None when
    it hasn't been looked up yet."""
    d = _site_of(domain)
    if not d:
        return False
    path = os.path.join(_FAVICON_DIR, d)
    # 150,000 bytes was an older read limit: a file exactly that long was cut off.
    if os.path.isfile(path) and os.path.getsize(path) == 150_000:
        os.remove(path)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            data = f.read()
        return data, _icon_mime(data)
    miss = path + ".miss"
    if os.path.isfile(miss) and time.time() - os.path.getmtime(miss) < 86400:
        return False
    return None


def _favicon(domain: str):
    """A site's own icon, fetched from that site only and cached on disk, for
    showing which sites a search or a page read touched. (bytes, mime) or None.
    This waits on the network for a site not looked up yet (up to ~20 s); a
    request from the window uses _icon_now, which doesn't."""
    known = _favicon_cached(domain)
    if known is not None:
        return known or None
    d = _site_of(domain)
    os.makedirs(_FAVICON_DIR, exist_ok=True)
    path = os.path.join(_FAVICON_DIR, d)
    miss = path + ".miss"
    for url in (f"https://{d}/favicon.ico", f"https://www.{d}/favicon.ico",
                f"https://{d}/apple-touch-icon.png"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Inglen"})
            with urllib.request.urlopen(req, timeout=4) as r:
                data = r.read(_ICON_MAX + 1)
            if 64 < len(data) <= _ICON_MAX and _icon_mime(data) != "application/octet-stream":
                with open(path, "wb") as f:
                    f.write(data)
                return data, _icon_mime(data)
        except Exception:
            continue
    # Many sites keep their icon elsewhere and say so in the page itself.
    try:
        req = urllib.request.Request(f"https://{d}/", headers={"User-Agent": "Mozilla/5.0 Inglen"})
        with urllib.request.urlopen(req, timeout=5) as r:
            page = r.read(200_000).decode("utf-8", "replace")
            base = r.geturl()
        for tag in re.findall(r"<link\b[^>]*>", page, re.I):
            # Quoted or not: rel=icon href=/img/x.svg is valid HTML.
            if not re.search(r"""rel\s*=\s*(["'][^"']*icon|icon)""", tag, re.I):
                continue
            m = re.search(r"""href\s*=\s*(?:["']([^"']+)["']|([^\s>]+))""", tag, re.I)
            if not m:
                continue
            url = urllib.parse.urljoin(base, m.group(1) or m.group(2))
            if not url.startswith("https://"):
                continue
            ireq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Inglen"})
            with urllib.request.urlopen(ireq, timeout=4) as r:
                data = r.read(_ICON_MAX + 1)
            if 64 < len(data) <= _ICON_MAX and _icon_mime(data) != "application/octet-stream":
                with open(path, "wb") as f:
                    f.write(data)
                return data, _icon_mime(data)
    except Exception:
        pass
    open(miss, "w").close()
    return None


# Icons for the window: answered from disk at once; a site or brand not looked up yet
# answers "none" now and is fetched in the background, so the next ask finds it. At
# start the window asks for several at once, and each one waiting on the network held
# one of the browser's six connections for up to 20 s (C20).
_ICON_JOBS: Dict[Any, Any] = {}
_ICON_LOCK = threading.Lock()
_ICON_POOL = None


def _icon_now(cached, fetch, arg):
    icon = cached(arg)
    if icon is not None:
        return icon or None
    global _ICON_POOL
    key = (getattr(fetch, "__name__", ""), arg)
    with _ICON_LOCK:
        if key not in _ICON_JOBS:
            if _ICON_POOL is None:
                from concurrent.futures import ThreadPoolExecutor
                _ICON_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="icon-fetch")

            def run():
                try:
                    fetch(arg)
                except Exception:
                    pass
                finally:
                    with _ICON_LOCK:
                        _ICON_JOBS.pop(key, None)
            _ICON_JOBS[key] = _ICON_POOL.submit(run)
    return None


def _icon_fetching(fetch, arg) -> bool:
    with _ICON_LOCK:
        return (getattr(fetch, "__name__", ""), arg) in _ICON_JOBS


def _icon_mime(data: bytes) -> str:
    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:200].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:600].lower()):
        return "image/svg+xml"
    return "application/octet-stream"


def _team_choices() -> Dict[str, Any]:
    """Who can be started from the Team view: Hearth's own helper roles and
    the agent CLIs installed on this PC."""
    from . import subagents as _sa, external_agents as _xa
    roles = []
    for p in _sa.list_personas():
        if "error" in p or p.get("slug") in ("pdf_coordinator", "librarian"):
            continue                     # internal plumbing, not something to hand a task to
        desc = str(p.get("description") or "").split(". ")[0].strip()
        roles.append({"id": p["slug"], "label": p["slug"].replace("_", " ").title(), "about": desc[:120]})
    agents = [{"id": a["id"], "label": a["label"], "maker": a.get("maker", ""),
               "tested": a.get("tested", False), "billing": a.get("billing", ""),
               "refused": a.get("refused", "")} for a in _xa.available()]
    # Where Hearth's own helpers run when it isn't the local model, so the
    # dialog can say so.
    helper_brain = ""
    try:
        on = _sa._agent_for_helpers()
        if on:
            helper_brain = _xa.AGENTS[on]["label"]
    except Exception:
        pass
    # The agent programs Hearth knows how to run that aren't on this PC, so
    # the dialog can say the list grows with what gets installed.
    return {"roles": roles, "agents": agents, "workspace": WORKSPACE, "helper_brain": helper_brain,
            "more_agents": _xa.missing()}


def _saved_look() -> str:
    """The saved theme, accent and Simple/Advanced as a JS object literal, for
    the page to apply before its first paint."""
    s = _load_settings()
    from .look_icon import default_theme
    # A saved look wins; with none, what the window last showed; with neither, the
    # default for this install (Classic for one set up before the rename).
    look = {"theme": s.get("ui_theme"), "accent": s.get("ui_accent"), "ui": s.get("ui_mode"),
            "theme_default": default_theme(s)}
    ok = {"theme": {"classic", "dark", "light", "system"},
          "accent": {"ember", "sage", "ocean", "rose", "violet", "mono"},
          "ui": {"simple", "advanced"}}
    ok["theme_default"] = ok["theme"]
    return json.dumps({k: v for k, v in look.items() if v in ok[k]})


def _save_settings(d: Dict) -> Dict:
    with _SETTINGS_WRITE_LOCK:
        return _save_settings_locked(d)


def _save_settings_locked(d: Dict) -> Dict:
    cur = _load_settings()
    # Detect a cloud→local or local→cloud brain switch BEFORE writing — the
    # disk-model cache populated under cloud is shorter (cached scan) and
    # needs a fresh rescan when the user goes back to local. Don't block
    # the settings save; kick the rescan async + report it back via status.
    _prev_provider = (cur.get("llm_provider") or "").strip().lower()
    _new_provider = (d.get("llm_provider") or _prev_provider or "").strip().lower()
    _CLOUD = {"grok", "xai", "gemini", "google", "openai", "anthropic",
              "openrouter", "groq", "custom"}
    _was_cloud = _prev_provider in _CLOUD
    _is_cloud_now = _new_provider in _CLOUD
    _prev_tts_device = cur.get("tts_device", "cpu")
    _changed = sorted(k for k, v in d.items() if v is not None and cur.get(k) != v)
    cur.update({k: v for k, v in d.items() if v is not None})
    if _changed:
        live_bus.publish({"kind": "settings", "keys": [k for k in _changed if "key" not in k]})
    # A model file on this PC is never a cloud provider's model. Saved as one,
    # every message would ask the provider for a name it has never heard of.
    from .headless import PROVIDERS as _known
    if _new_provider in _known and str(cur.get("llm_model") or "").lower().endswith(".gguf"):
        cur["llm_model"] = ""
    os.makedirs(os.path.dirname(SETTINGS_PATH) or WORKSPACE, exist_ok=True)
    # Atomic: a truncated settings.json (power cut, AV lock mid-write) would
    # lose the brain, workspace pointer and voice prefs in one go.
    _atomic_write_json(SETTINGS_PATH, cur, indent=2)
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
    _env.set("STT_DEVICE", cur.get("stt_device", "cpu"))
    _env.set("STT_MODEL", cur.get("stt_model",  "base.en"))
    _env.set("STT_LANGUAGE", cur.get("stt_language", ""))
    _env.set("TTS_DEVICE", cur.get("tts_device", "cpu"))
    _env.set("VOICE_SPEED", str(cur.get("voice_speed", 1.0)))
    _env.set("VOICE", cur.get("voice_name", "am_michael"))
    _apply_server_port(cur.get("server_port"))
    # Mic pick. Every voice session builds its own recorder, so the next one
    # opens the chosen device; a running session keeps its mic until it ends.
    _env.set("VOICE_INPUT_DEVICE", str(cur.get("voice_input_device", -1)))
    _env.set("VOICE_INPUT_NAME", str(cur.get("voice_input_name", "") or ""))
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
    new_agent_name = (cur.get("agent_name") or __import__("hearth").default_agent_name()).strip() or __import__("hearth").default_agent_name()
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
            _voice.set_speed(float(cur.get("voice_speed", 1.0)))
        except Exception:
            pass
        try:
            _voice.set_default_voice(str(cur.get("voice_name", "am_michael")))
        except Exception:
            pass
        # Dropping the engine makes the next reply reload the whole speech
        # model first; only a change of the device it runs on needs that.
        if cur.get("tts_device", "cpu") != _prev_tts_device:
            try: _voice.reload()
            except Exception: pass
    return cur


def _list_files(rel_path: str = "", place: str = "inglen") -> Dict:
    """List a folder inside one of the Files tab's places (the workspace by
    default). Never leaves that place's folder."""
    from . import places as _places
    root = _places.root_for(place or "inglen")
    if not root:
        return {"error": "that place isn't on this PC"}
    rel = (rel_path or "").strip().strip("/").strip("\\")
    base = os.path.normpath(os.path.join(root, rel))
    from .tools import path_within
    if not path_within(os.path.realpath(base), os.path.realpath(root)):
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
    return {"path": rel, "abs": base, "root": root, "place": place or "inglen", "entries": out}


def _tail_log(n: int = 100) -> List[Dict]:
    """The last `n` tool events, oldest first. The lines saying the tool server
    started for an agent brain come along but don't count toward `n`: there is
    one per agent turn, and they pushed the tool calls out of the window."""
    log_path = os.path.join(LOGS_DIR, "activity.jsonl")
    if not os.path.isfile(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    n = max(1, min(n, 1000))
    out, real = [], 0
    for ln in reversed(lines):
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        out.append(ev)
        if ev.get("event") != "server_start":
            real += 1
            if real >= n:
                break
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

def one_shot(prompt: str, max_tokens: int = 512, timeout: float = 20) -> str:
    """A short answer from the brain in use, with no tools or chat around it.
    Empty when there is no brain or it doesn't answer."""
    settings = _load_settings()
    provider = (settings.get("llm_provider") or "").lower()
    from . import claude_brain
    if provider in claude_brain.BRAINS:
        # An agent program is the brain: there is no model endpoint to ask.
        return claude_brain.quick(prompt)
    is_cloud = provider not in ("", "local", "lmstudio", "builtin")
    model_id = (settings.get("llm_model") or "").strip()
    if not model_id:
        loaded = _detect_loaded()
        if loaded:
            model_id = loaded.get("id") or ""
    if not model_id and is_cloud:
        # Cloud provider with no model saved: the first chat model it lists.
        from .headless import settle_model
        model_id = settle_model(LOCAL_API_BASE, os.environ.get("LOCAL_API_KEY") or settings.get("llm_key") or "", "")
    if not model_id:
        return ""
    api_key = (os.environ.get("LOCAL_API_KEY") or settings.get("llm_key")
               or (_provider_key(provider) if is_cloud else "") or "hearth-builtin")
    from .headless import one_shot as _ask
    return _ask(prompt, model_id, base=LOCAL_API_BASE, key=api_key, max_tokens=max_tokens, timeout=timeout)


def title_ask(prompt: str) -> str:
    """A chat's title, from the model the memory pass would use: one loaded on
    this PC first (free), else the brain's own one-shot, on every brain kind
    (protocol agents included: one short request per chat)."""
    from .memory_pass import ask_once
    return ask_once(prompt) or one_shot(prompt, timeout=20)


_IDEAS_FILE = os.path.join(LOGS_DIR, "reminder_ideas.json")
_IDEAS_LOCK = threading.Lock()


def reminder_ideas(fresh: bool = False) -> List[Dict[str, str]]:
    """Reminders this person would likely want, made from what JARVIS remembers
    about them and what they talk about. Remade when either has moved on (at most
    hourly) or after half a day. None when there is nothing to go on."""
    with _IDEAS_LOCK:
        from . import memory, reminders as _r
        listed = _list_convos()[:20]          # kept per file by mtime, unlike a full read of every chat
        basis = [round(memory.changed_at()), max([int(c.get("updated") or 0) for c in listed] or [0])]
        try:
            with open(_IDEAS_FILE, encoding="utf-8") as f:
                kept = json.load(f)
            age = time.time() - float(kept.get("at") or 0)
            if not fresh and (age < 3600 or (kept.get("basis") == basis and age < 12 * 3600)):
                return kept.get("ideas") or []
        except (OSError, ValueError):
            pass
        try:
            mem = (memory.about_the_person() + "\n" + memory.index_for_prompt(25)).strip()
        except Exception:
            mem = ""
        chats = [c.get("title") for c in listed if c.get("title") and c.get("source") != "reminder"]
        if not mem.strip() and not chats:
            return []
        have = [r.get("what") for r in _r.list_reminders() if r.get("what")]
        prompt = (
            "Suggest 3 reminders or scheduled tasks this person would likely want, from what they "
            "keep in memory and talk about. Each is something an assistant on their PC could remind "
            "them of or do for them at a time or on repeat. Skip what they already have. Something "
            "coming up in what they remember (an exam, a deadline, a trip, a bill) comes first, as a "
            "reminder set ahead of it.\n\n"
            f"Today is {time.strftime('%A %d %B %Y')}.\n\n"
            f"Memory:\n{mem[:2500]}\n\nRecent chats:\n" + "\n".join(f"- {t}" for t in chats[:20])
            + ("\n\nAlready set:\n" + "\n".join(f"- {w}" for w in have[:15]) if have else "")
            + '\n\nAnswer with JSON only: [{"title": "2-4 words", '
              '"ask": "the request as they would type it, with when"}]')
        raw = one_shot(prompt, max_tokens=700, timeout=45)
        ideas: List[Dict[str, str]] = []
        try:
            got = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
            for x in got if isinstance(got, list) else []:
                if isinstance(x, dict) and x.get("title") and x.get("ask"):
                    ask = str(x["ask"])[:200]
                    ideas.append({"title": str(x["title"])[:60], "ask": ask,
                                  "repeats": bool(re.search(r"\b(every|daily|weekly|each)\b", ask, re.I))})
        except ValueError:
            pass
        if ideas:
            os.makedirs(LOGS_DIR, exist_ok=True)
            _atomic_write_json(_IDEAS_FILE, {"at": time.time(), "basis": basis, "ideas": ideas[:3]})
        return ideas[:3]


# ---------------------------------------------------------------- voice mode in the window
# Voice mode in the window is a call (call.Call, the one the phone app makes)
# over a WebSocket on this server: the page captures the mic with the browser's
# echo cancelling and plays the voice through it, so it never hears itself on
# speakers and talking over it works there too. One at a time.
_WIN_CALL = None
_WIN_ASK: Dict[str, Any] = {}       # the approval a voice turn in the window is waiting on
_WIN_LOCK = threading.Lock()
_WIN_HUD = {"listening": "listening", "thinking": "thinking", "step": "thinking",
            "speaking": "speaking", "asking": "speaking", "connecting": "idle"}


def window_call():
    """The window's voice call while one is on."""
    c = _WIN_CALL
    return c if c is not None and not c.ended else None


def _pcm_level(pcm: bytes) -> float:
    try:
        import numpy as np
        a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype("float32") / 32768.0
        return float(np.sqrt(np.mean(a * a))) if a.size else 0.0
    except Exception:
        return 0.0


def _window_hud(obj: Dict[str, Any]) -> None:
    """The desktop HUD follows the window's call: its state and its voice's loudness."""
    if not _voice:
        return
    kind = obj.get("type")
    try:
        if kind == "call.state":
            _voice.set_voice_state(_WIN_HUD.get(str(obj.get("state")), "idle"))
        elif kind == "call.down":
            _voice.note_level(min(1.0, _pcm_level(base64.b64decode(obj.get("data") or "")) * 2.6))
        elif kind == "call.ended":
            _voice.set_voice_state("idle")
    except Exception:
        pass


def _window_said(text: str) -> str:
    """A sentence heard in the window's call. When an approval is waiting and the
    sentence is a plain answer, it answers it (a spoken yes only for what Auto
    would let run anyway); anything else goes to the page to send as a message."""
    ask = dict(_WIN_ASK)
    if ask.get("id") and ask["id"] in _permission_queues:
        from .call import spoken_answer
        said = spoken_answer(text)
        if said and said != "deny" and ask.get("tap"):
            return "needs_tap"
        # A clear yes or no answers it; anything else is what they want to say, and
        # the step waits for another time (never taken as a no).
        _resolve_permission(ask["id"], said or "aside", "" if said else text)
        _WIN_ASK.clear()
        if said:
            return "answered"
        return "aside"
    call = window_call()
    if call is not None:
        call.send({"type": "call.say", "text": text})
    return "ran"


def _window_turn_event(turn, kind: str, fields: Dict[str, Any]) -> None:
    """A voice turn in the window, followed by its call: spoken as written, steps, approvals said."""
    try:
        turn.event(kind, fields)
        if kind != "permission_request" or turn.n != turn.call.turn:
            return
        if fields.get("aloud"):
            return                  # the brain asks in its own words; its reply is spoken
        # A card the turn waits on (an agent that only hears yes or no, a write outside
        # the folder): shown, never read out from a template. A clear spoken yes or no
        # answers it; anything else said is a message, not a no.
        name, args = str(fields.get("name") or ""), fields.get("args") or {}
        from .tools import needs_a_tap
        tap = name != "__extend_workspace__" and needs_a_tap(name, args)
        _WIN_ASK.clear()
        _WIN_ASK.update(id=str(fields.get("id") or ""), tap=bool(tap))
        turn.call.ask_quietly(tap=bool(tap), aid=_WIN_ASK["id"])
    except Exception as e:
        print(f"[hearth.voice] window call: {type(e).__name__}: {e}", flush=True)


_WIN_AWAY_S = 20                    # how long a call waits for its page to reconnect


def _window_call_over(call, link) -> None:
    """The window's call ends: its listener, the HUD, the mic for the wake word."""
    global _WIN_CALL
    call.end()
    with _WIN_LOCK:
        last = _WIN_CALL is call
        if last:
            _WIN_CALL = None
    if link.pop("claimed", False):
        try:
            from .listen import release_mic
            release_mic()
        except Exception:
            pass
    if last:
        _WIN_ASK.clear()
        try:
            from . import voice_overlay as _vo
            _vo.stop()
            if _voice:
                _voice.set_voice_state("idle")
        except Exception:
            pass


def _serve_window_call(ws) -> None:
    """One window's voice call, for as long as its page keeps it. A socket that
    drops (a page reloading its connection, something on the PC cutting local
    traffic) doesn't end the call: the page reconnects and takes the same call
    back, with its listener still warm and the reply still being spoken."""
    global _WIN_CALL
    from .call import Call
    link: Dict[str, Any] = {"ws": ws, "ready": False}
    mine: Dict[str, Any] = {"call": None, "link": link}

    def sender(lk):
        def send(obj: Dict[str, Any]) -> None:
            if obj.get("type") == "call.state" and obj.get("state") == "listening":
                lk["ready"] = True
            _window_hud(obj)
            lk["ws"].send(json.dumps(obj, ensure_ascii=False))
        return send

    send = sender(link)

    def save(changed: Dict[str, Any]) -> None:
        _save_settings(changed)

    def warming(call, lk) -> None:
        """Until the call first listens: how far a first-run speech model download is,
        and a word if starting takes long (a missing part, a stalled download)."""
        out = sender(lk)
        t0, last = time.time(), -1
        try:
            fetching = not _rt_voice.model_downloaded()
        except Exception:
            fetching = False
        if fetching:
            out({"type": "call.warming", "detail": "downloading the speech model, first time only (a minute or two)…"})
        told = False
        while not call.ended and not lk.get("ready") and time.time() - t0 < 1200:
            if fetching:
                pct = _rt_voice.download_progress()
                if pct is not None and pct != last:
                    last = pct
                    out({"type": "call.warming", "pct": pct,
                         "detail": f"downloading the speech model, first time only · {pct}%"})
            if not told and time.time() - t0 > 45:
                told = True
                out({"type": "call.slow", "message":
                     "Voice is still starting. First run downloads a small speech model, which can take a "
                     "minute. If it never starts, a voice component may be missing from this build - check "
                     "hearth_window.log."})
            time.sleep(0.5)

    # Which mic the page should open: the one picked in Settings, never system sound.
    # On macOS the window's web view would ask the person itself (with the local
    # address in the question), so there the page leaves the mic to the PC.
    try:
        from .listen import _SYSTEM_AUDIO_HINTS, selected_input_name
        hello = {"type": "call.mic", "name": selected_input_name(), "not_mics": list(_SYSTEM_AUDIO_HINTS)}
    except Exception:
        hello = {"type": "call.mic", "name": "", "not_mics": []}
    hello["page_mic"] = sys.platform != "darwin"
    hello["windows"] = sys.platform == "win32"
    ws.send(json.dumps(hello, ensure_ascii=False))
    left = False                        # the page ended the call, rather than its socket dropping
    try:
        while True:
            raw = ws.recv()
            if raw is None:
                break
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            kind, call = m.get("type"), mine["call"]
            if kind == "call.up":
                if call is not None:
                    data = str(m.get("data") or "")
                    call.up(data, int(m.get("rate") or 16000))
                    if _voice:
                        try:
                            _voice.set_mic_level(min(1.0, _pcm_level(base64.b64decode(data)) * 6.0))
                        except Exception:
                            pass
                continue
            if kind == "call.resume":
                # The same page, back on a new socket: the call it had carries on.
                ref = str(m.get("ref") or "")[:40]
                cur = window_call()
                if cur is not None and ref and cur.ref == ref:
                    lk = cur._link
                    lk["ws"] = ws
                    mine["call"], mine["link"] = cur, lk
                    link = lk
                    send({"type": "call.resumed", "ref": ref})
                    if lk.get("ready"):
                        send({"type": "call.state", "state": "speaking" if cur.speaker.busy() else "listening"})
                else:
                    ws.send(json.dumps({"type": "call.gone", "ref": ref}))
                continue
            if kind == "call.start":
                call = Call(send, _window_said, cid=re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80],
                            save=save, ref=str(m.get("ref") or "")[:40])
                call.quick = True               # speech starts at the first clause, as voice mode always has
                call._link = link
                with _WIN_LOCK:
                    old, _WIN_CALL = _WIN_CALL, call
                for c in (old, mine["call"]):
                    if c is not None and c is not call:
                        threading.Thread(target=_window_call_over, args=(c, getattr(c, "_link", {})),
                                         daemon=True).start()
                mine["call"] = call
                link["ready"] = False
                _WIN_ASK.clear()

                def begin(call=call, lk=link):
                    # The wake word's listener lets go of the PC's mic while voice mode is on,
                    # as it does for the PC's own listener.
                    try:
                        from .listen import claim_mic
                        claim_mic(0.0)
                        lk["claimed"] = True
                    except Exception:
                        pass
                    call.start()
                threading.Thread(target=begin, daemon=True, name="window-call").start()
                threading.Thread(target=warming, args=(call, link), daemon=True, name="window-call-warm").start()
                try:
                    from . import voice_overlay as _vo
                    _vo.start()                 # the HUD shows when the window isn't in front
                except Exception:
                    pass
                continue
            if call is None:
                continue
            if kind == "call.end":
                left = True
                break
            if kind == "call.hush":
                threading.Thread(target=call.hush, daemon=True).start()
            elif kind == "call.set":
                voice, lang = m.get("voice"), m.get("language")
                dev, mdl = m.get("stt_device"), m.get("stt_model")
                threading.Thread(target=call.set, args=(str(voice) if voice else None,
                                                        str(lang) if lang is not None else None,
                                                        str(dev) if dev else None, str(mdl) if mdl else None),
                                 daemon=True).start()
    finally:
        call, lk = mine["call"], mine["link"]
        if call is not None and lk.get("ws") is ws:
            if left:
                _window_call_over(call, lk)
            else:
                print("[hearth.voice] voice mode's socket dropped; waiting for the page to come back", flush=True)

                def away(call=call, lk=lk, gone=ws):
                    end = time.time() + _WIN_AWAY_S
                    while time.time() < end and lk.get("ws") is gone and not call.ended:
                        time.sleep(0.25)
                    if lk.get("ws") is gone and not call.ended:
                        print("[hearth.voice] voice mode's page didn't come back; the call ends", flush=True)
                        _window_call_over(call, lk)
                threading.Thread(target=away, daemon=True, name="window-call-away").start()
        ws.close()


class HearthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet — only print errors
        if args and isinstance(args[1], str) and args[1].startswith(("4", "5")):
            sys.stderr.write(f"[hearth.web] {self.address_string()} - {fmt % args}\n")

    # -------- helpers --------

    def _drain_body(self) -> None:
        """Read whatever of the request body has not been read yet.

        A reply sent without reading the body leaves those bytes unread, and on
        Windows closing a socket with unread data sends a reset rather than a
        normal close. The reset can reach the client before it has read the
        reply and wipe it, so an early answer (a refusal, an unknown path) was
        sometimes lost as "connection reset" instead of arriving.
        """
        if getattr(self, "_body_read", False):
            return
        self._body_read = True
        try:
            length = int(self.headers.get("content-length", "0") or "0")
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return
        if length > 8 * 1024 * 1024:
            self.close_connection = True       # too large to be worth reading
            return
        try:
            self.rfile.read(length)
        except OSError:
            pass

    def _send_json(self, code: int, body: Any) -> None:
        payload = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self._drain_body()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            # Browser tab closed / refresh / fast-clicked. Normal noise; the
            # default BaseHTTPRequestHandler trace was scaring users. Silenced.
            pass

    def _read_json(self) -> Dict:
        if getattr(self, "_body_read", False):
            return getattr(self, "_body_json", {})
        self._body_read = True
        length = int(self.headers.get("content-length", "0") or "0")
        if length <= 0:
            self._body_json = {}
            return {}
        try:
            self._body_json = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self._body_json = {}
        return self._body_json

    def _query(self) -> Dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        return {k: v[0] for k, v in q.items()}

    # -------- routing --------

    def _host_allowed(self) -> bool:
        """DNS-rebinding / CSRF guard for state-changing requests. Accept only a
        loopback Host header: a page that rebinds its domain to 127.0.0.1 still
        sends `Host: evil.com`, so this blocks it while the local GUI (Host:
        127.0.0.1:8765 / localhost:8765) passes. Users who deliberately expose the
        web UI on their LAN opt out with HEARTH_ALLOW_LAN=1."""
        if _env.get("ALLOW_LAN") in ("1", "true", "yes"):
            return True
        raw = (self.headers.get("Host") or "").strip().lower()
        if not raw:
            return True   # no Host (a non-browser local client) — not a rebind
        host = (raw.split("]")[0] + "]") if raw.startswith("[") else raw.split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _origin_allowed(self) -> bool:
        """Refuse a browser request made from any page other than Hearth's own.

        The host check stops DNS rebinding, where a page's own domain resolves
        to 127.0.0.1 and the Host header still names that domain. It does not
        stop the simpler case: a page on any site that fetches
        http://127.0.0.1:8765/ sends Host 127.0.0.1, because Host names the
        target, so it passed, and with a wildcard CORS header the page could
        read settings back and call tools.

        A browser always sends Origin on a cross-origin request, so an Origin
        naming a different host and port than the one being asked is someone
        else's page. No Origin at all is a non-browser client (the CLI, a
        bridge, a script) and is left alone. "null" is a sandboxed frame or a
        file page, and neither has any business with this API."""
        # A browser marks every request with where it came from. An <img>, a
        # <script> or a form GET from another site carries no Origin, so it got
        # past the check below and could set off a GET that does something (or
        # tell, by whether it loaded, that a file exists). Another site may open
        # the window's own page by a link, nothing else.
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site in ("cross-site", "same-site"):
            nav = (self.headers.get("Sec-Fetch-Mode") or "").strip().lower() == "navigate"
            return nav and self.command == "GET" and urllib.parse.urlparse(self.path).path == "/"
        origin = (self.headers.get("Origin") or "").strip().lower()
        if not origin:
            return True
        if origin == "null":
            return False
        want = (self.headers.get("Host") or "").strip().lower()
        return urllib.parse.urlparse(origin).netloc == want

    def _request_allowed(self) -> bool:
        return self._host_allowed() and self._origin_allowed()

    def _events(self) -> None:
        """What changes, as it changes (live_bus), to the window and to the phone bridge."""
        q = live_bus.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": open\n\n")
            self.wfile.flush()
            import queue as _queue
            while True:
                try:
                    ev = q.get(timeout=15)
                    data = "data: " + json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"
                except _queue.Empty:
                    data = ": still here\n\n"
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            live_bus.unsubscribe(q)

    def do_OPTIONS(self) -> None:
        # Hearth's own page is same-origin and never preflights. A preflight is
        # only ever another site asking permission, so it gets none.
        self.send_response(403)
        self.end_headers()

    def do_GET(self) -> None:
        if not self._request_allowed():
            return self._send_json(403, {"error": "forbidden origin"})
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/events":
            return self._events()
        if path in ("/", "/index.html", "/ui"):
            return self._serve_ui()
        if path == "/room.js":
            return self._serve_room_js()
        if path == "/call_audio.js":
            return self._serve_room_js(_CALL_AUDIO_JS)
        if path == "/api/voice/call":
            from . import wsock
            if not wsock.is_upgrade(self.headers):
                return self._send_json(400, {"error": "voice mode talks over a WebSocket"})
            return _serve_window_call(wsock.accept(self))
        if path == "/api/undo":
            # The newest file changes that can be put back (hearth/undo.py).
            from . import undo as _undo
            return self._send_json(200, {"changes": _undo.recent(30)})
        if path == "/api/room/now":
            # What JARVIS is doing in this window and in the terminal, for the
            # phone's room (the window's own room reads its page state).
            return self._send_json(200, room_now())
        if path.startswith("/assets/"):
            return self._serve_asset(path[len("/assets/"):])
        if path == "/api/setup/what-to-get":
            # First run: what this PC still needs for what was picked, with sizes.
            wq = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._send_json(200, what_to_get(local=wq.get("local", ["0"])[0] == "1",
                                                    voice=wq.get("voice", ["0"])[0] == "1"))
        if path == "/api/file/openers":
            # The apps Windows offers for this file, for the Files tab's Open menu.
            from . import openers as _op
            fq = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            real, why = _workspace_file(fq.get("path", [""])[0])
            if why:
                return self._send_json(400, {"apps": [], "error": why})
            return self._send_json(200, {"apps": [{"name": o["name"], "exe": o["exe"], "default": o["default"]}
                                                  for o in _op.openers(real)]})
        if path == "/api/file/opener-icon":
            from . import openers as _op
            exe = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("exe", [""])[0]
            # 48 px: shown at 16-22 px, sharp on a scaled display too.
            png = _op.icon_png(exe, 48) if exe.lower().endswith((".exe", ".desktop")) else None
            if not png:
                return self.send_error(404, "no icon")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(png)
            return
        if path == "/api/mention":
            # What "@words" in the message box can point at.
            from . import session_search as _ss
            mq = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._send_json(200, {"items": _ss.mention(mq.get("q", [""])[0])})
        if path == "/api/read-chat":
            # A chat attached to a message, read in order the way read_chat does.
            from . import session_search as _ss
            mq = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._send_json(200, {"text": _ss.read_chat(mq.get("id", [""])[0])})
        if path == "/api/look-icon":
            from . import look_icon
            lq = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            f = look_icon.file(lq.get("theme", ["classic"])[0], lq.get("accent", ["rose"])[0], "png")
            if not f:
                return self.send_error(404, "no icon")
            with open(f, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/update/repair-plan":
            # What a repair would actually touch. Repair used to be one button
            # with a progress bar and no statement of what it would do or what
            # it cannot fix, so a run that left voice broken still looked like
            # a success.
            from . import updater
            try:
                return self._send_json(200, updater.repair_plan())
            except Exception as e:
                return self._send_json(200, {"ok": False, "can_patch": False,
                                             "error": f"{type(e).__name__}: {e}",
                                             "items": []})
        if path == "/api/state":
            return self._send_state()
        if path == "/api/lockdown":
            from . import tools as _t
            return self._send_json(200, {"on": _t.read_locked()})
        if path == "/api/email":
            try:
                from . import email_tools
                return self._send_json(200, email_tools.status())
            except Exception as e:
                return self._send_json(200, {"configured": False, "error": str(e)})
        if path == "/api/window-signals":
            # Cheap, high-frequency poll for cross-process window actions the
            # backend can't do itself (the window is in the desktop_attach
            # subprocess). Kept OFF /api/state so it can poll every ~2s without
            # the heavier state build — surfacing on a second launch has to feel
            # instant, not wait out the 12s state poll.
            return self._send_json(200, {"focus": _take_pending_focus()})
        if path == "/api/agents/updates":
            from . import external_agents as _xa
            return self._send_json(200, {"updates": _xa.updates()})
        if path == "/api/agents/setup":
            # Which agents are here and signed in, for the setup dialog.
            from . import external_agents as _xa
            return self._send_json(200, {"agents": _xa.setup_state(fresh=self._query().get("fresh") == "1")})
        if path == "/api/brain/agent/models":
            from . import claude_brain
            aid = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("id", [""])[0]
            if aid not in claude_brain.BRAINS:
                return self._send_json(404, {"error": "unknown agent"})
            return self._send_json(200, claude_brain.agent_models(aid))
        if path == "/api/context-budget":
            # Live read of the current brain's context window so the GUI
            # ring + bottom bar update instantly on provider swap (don't
            # have to wait for the next chat turn to fire context_budget
            # SSE events). Same helper the chat path uses, so they agree.
            try:
                from . import claude_brain as _cb
                if _cb.active():
                    # An agent runs its own context; Hearth only knows the
                    # model's window (after its first reply, for Claude Code).
                    return self._send_json(200, {"agent": _cb.current(), "total": _cb.context_window() or 0,
                                                 "tools": 0, "effective": _cb.context_window() or 0,
                                                 "source": _cb.label(), "model": _cb.chosen_model(_cb.current()),
                                                 "base": 0})
            except Exception:
                pass
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
                # Persona / system-prompt overhead. The live context_state count
                # includes it, so the GUI's idle ring estimate (history chars / 4)
                # must add it too or the ring reads low at idle then jumps up the
                # instant a turn starts. Surface it so both agree.
                try:
                    from . import system_prompt as _sp
                    base_tokens = len(_sp()) // 4
                except Exception:
                    base_tokens = 0
                effective = max(2048, tokens - tool_tokens)
                return self._send_json(200, {
                    "total": tokens, "tools": tool_tokens,
                    "effective": effective, "source": source,
                    "model": model, "base": base_tokens,
                })
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/conversations":
            return self._send_json(200, {"conversations": _list_convos()})
        if path == "/api/conversations/search":
            # Full-text search across all past chats (FTS5). Returns the matching
            # conversation + a highlighted snippet so the sidebar can jump to it.
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0].strip()
            if not q:
                return self._send_json(200, {"results": []})
            try:
                from . import session_search
                matches = session_search.search(q, limit=12, match_all=True)
                return self._send_json(200, {"results": [
                    {"conversation_id": m.conversation_id, "title": m.title,
                     "role": m.role, "snippet": m.snippet} for m in matches]})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path.startswith("/api/conversations/"):
            cid = urllib.parse.unquote(path[len("/api/conversations/"):])
            data = _load_convo(cid)
            if data is None:
                return self._send_json(404, {"error": "not found"})
            return self._send_json(200, data)
        if path == "/api/models":
            try:
                from . import claude_brain as _cb
                _agents = _cb.choices()
                if _cb.active():
                    return self._send_json(200, {"models": [{"id": _cb.label(), "state": "loaded",
                                                             "type": "llm", "provider": _cb.current()}],
                                                 "notice": "", "agent_brains": _agents,
                                                 "providers": _saved_providers()})
            except Exception:
                _agents = []
            _ms = _list_models()
            return self._send_json(200, {"models": _ms, "notice": _models_notice, "agent_brains": _agents,
                                         "providers": _saved_providers()})
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
        if path == "/api/crash/pending":
            from . import crashlog as _cl
            return self._send_json(200, {"crashed": _cl.pending() is not None})
        if path == "/api/new-chat/offers":
            from . import offers as _offers
            return self._send_json(200, {"rows": _offers.rows()})
        if path == "/api/reminders/ideas":
            try:
                return self._send_json(200, {"ideas": reminder_ideas(self._query().get("fresh") == "1")})
            except Exception as e:
                return self._send_json(200, {"ideas": [], "error": f"{type(e).__name__}: {e}"})
        if path == "/api/reminders":
            try:
                from . import reminders as _r
                items = _r.list_reminders(include_fired=self._query().get("all") == "1")
                return self._send_json(200, {"items": items})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/skills":
            try:
                from . import skills_loader as _sl
                return self._send_json(200, {"items": _sl.list_skills(include_body=True, include_off=True)})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/bridges":
            # Which phone bridges are configured (for the Connect pane).
            import json as _j
            base = os.path.join(os.path.expanduser("~"), ".hearth")
            out = {}
            for ch, fn in {"telegram": "phone_bridge.json", "discord": "discord_bridge.json",
                           "whatsapp": "whatsapp_bridge.json"}.items():
                cfg = {}
                try:
                    with open(os.path.join(base, fn), encoding="utf-8") as f:
                        cfg = _j.load(f) or {}
                except Exception:
                    cfg = {}
                if ch == "whatsapp":
                    configured = bool(cfg.get("allowed_numbers") or cfg.get("allow_self_chat"))
                else:
                    configured = bool((cfg.get("bot_token") or "").strip())
                # Ids go to the page as text: a Discord id is longer than a
                # JavaScript number holds, so it came back rounded and a Save
                # wrote the wrong person in.
                for key in ("allowed_chat_ids", "allowed_user_ids", "allowed_numbers"):
                    if isinstance(cfg.get(key), list):
                        cfg[key] = [str(x) for x in cfg[key]]
                out[ch] = {"configured": configured, "config": cfg, "running": _bridge_running(ch)}
            try:
                from . import custom_connector as _cust
                for name in _cust.available():
                    out["custom:" + name] = {"configured": True, "custom": True, "config": {},
                                             "running": _bridge_running("custom:" + name)}
            except Exception:
                pass
            return self._send_json(200, {"bridges": out})
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
        if path == "/api/memory-share":
            # Which coding agents read and write this memory too.
            from . import shared_memory as _sm
            return self._send_json(200, {"agents": _sm.status()})
        if path == "/api/images":
            # The image and video engine, its model folders and which models are ready.
            from . import sdserver
            return self._send_json(200, sdserver.overview(start_engine=self._query().get("start", ["1"])[0] != "0"))
        if path == "/api/connectors/access":
            # Who asked to talk on a chat app, and who is let in.
            from . import channel_core as _cc
            return self._send_json(200, {"pending": _cc.pending(), "approved": _cc.approved()})
        if path == "/api/plus":
            # Plus: the licence, the relay, paired phones and a pairing in progress.
            from . import plus as _plus, relay_bridge as _rb
            return self._send_json(200, {**_plus.status(), "relay": bool(_rb.relay_url()), "phones": _rb.phones(),
                                         "pairing": _rb.pairing_status(), "bridge": _bridge_running("relay")})
        if path == "/api/install-count":
            # The opt-in install count: whether it is on, and exactly what it sends.
            from . import install_count
            return self._send_json(200, {"on": install_count.on(), "payload": install_count.payload()})
        if path == "/api/notify-targets":
            # Where a reminder for the phone would go now: the Inglen app (Plus) and the chat apps.
            from . import reminders as _rem
            return self._send_json(200, _rem.phone_targets())
        if path == "/api/cards":
            # Where each chat stands, for the Memory tab's "Going on".
            from . import memory_cards
            return self._send_json(200, {"cards": memory_cards.everything()})
        if path.startswith("/api/memory/"):
            name = urllib.parse.unquote(path[len("/api/memory/"):])
            return self._send_memory_one(name)
        if path == "/api/files":
            q = self._query()
            return self._send_json(200, _list_files(q.get("path", ""), q.get("place", "inglen")))
        if path == "/api/files/places":
            from . import places as _places
            return self._send_json(200, {"places": _places.listing()})
        if path == "/api/files/recent":
            from . import places as _places
            return self._send_json(200, {"entries": _places.touched()})
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
        if path == "/api/phone-logs":
            # Tail the phone-bridge logs so the user can SEE what's coming in
            # from Discord/Telegram/WhatsApp — phone reach runs headless and
            # the user (rightly) wants visibility into who messaged + what ran.
            q = self._query()
            try:
                n = max(1, min(500, int(q.get("lines", "120"))))
            except ValueError:
                n = 120
            logs_dir = os.path.join(WORKSPACE, "logs")
            out = []
            channels = []
            try:
                for fn in sorted(os.listdir(logs_dir)):
                    if fn.endswith("_bridge.log"):
                        channel = fn[:-len("_bridge.log")]  # telegram/discord/whatsapp
                        channels.append(channel)
                        fp = os.path.join(logs_dir, fn)
                        try:
                            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                                for ln in f.read().splitlines():
                                    if ln.strip():
                                        # Tag each line with its channel so the
                                        # one view keeps them distinguishable.
                                        out.append(f"[{channel}] {ln}")
                        except OSError:
                            pass
            except OSError:
                pass
            return self._send_json(200, {"lines": out[-n:], "channels": channels})
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
        if path == "/api/voice/mic-meter":
            return self._mic_meter_stream()
        if path == "/api/voice/devices":
            # List audio INPUT devices for the mic picker (Settings → Voice).
            # ?rescan=1 re-enumerates out-of-process to catch a just-plugged mic.
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            _rescan = _q.get("rescan", ["0"])[0] in ("1", "true", "yes")
            devs = []
            try:
                if _listen:
                    devs = (_listen.rescan_devices() if _rescan
                            else _listen.list_input_devices())
            except Exception:
                devs = []
            cur = -1
            try:
                cur = int(_env.get("VOICE_INPUT_DEVICE", "-1"))
            except ValueError:
                cur = -1
            sel_name = _env.get("VOICE_INPUT_NAME", "")
            if not sel_name and cur >= 0 and _listen:
                # An older pick saved as a number: name it now if it still means
                # a real mic, and keep it that way.
                try:
                    sel_name, _why = _listen.name_for_numbered_pick(cur)
                    if sel_name:
                        _save_settings({"voice_input_name": sel_name})
                except Exception:
                    sel_name = ""
            return self._send_json(200, {
                "devices": devs, "selected": cur, "selected_name": sel_name})
        if path == "/api/voice/install-status":
            return self._send_json(200, _VOICE_INSTALL or {"running": False})
        if path == "/api/voice/status":
            tts_status = {"available": False, "reason": "voice module missing"}
            stt_status = {"ready": False, "reason": "listen module missing"}
            rt_status = {"available": False}
            if _voice:
                try:
                    tts_status = dict(_voice.status())
                    # The fallbacks here and the setup screen say "available";
                    # the module itself says "ready". One answer for both.
                    tts_status["available"] = bool(tts_status.get("ready"))
                except Exception as e:
                    tts_status = {"available": False, "reason": f"{type(e).__name__}: {e}"}
            if _listen:
                try:
                    stt_status = _listen.status()
                    # Surface whether the CUDA whisper runtime is actually present
                    # so the GUI's STT dropdown shows "(not installed)" accurately.
                    try:
                        stt_status = {**stt_status, "cuda_available": bool(_listen.cuda_available())}
                    except Exception:
                        pass
                except Exception as e:
                    stt_status = {"ready": False, "reason": f"{type(e).__name__}: {e}"}
            if _rt_voice:
                try:
                    rt_status = _rt_voice.status()
                except Exception as e:
                    rt_status = {"available": False, "reason": f"{type(e).__name__}: {e}"}
            # What the wake listener is actually listening for. Until now this
            # lived only in an env var, so nobody could see the phrases.
            wake_status = {"on": False, "phrases": []}
            try:
                from .wake import _wake_phrases
                wake_status = {
                    "on": bool((_load_settings() or {}).get("tray_wake_on")),
                    "phrases": _wake_phrases(),
                }
            except Exception:
                pass
            return self._send_json(200, {"tts": tts_status, "stt": stt_status,
                                         "realtime": rt_status, "wake": wake_status})
        if path == "/api/voice/realtime/stream":
            return self._realtime_stream()
        if path == "/api/permissions":
            return self._send_json(200, {
                "always_allow": sorted(_always_allow),
                "always_deny":  sorted(_always_deny),
                "pending":      list(_permission_queues.keys()),
            })
        if path == "/api/settings":
            # With the name the agent has until the person picks one, for the window's fallbacks.
            return self._send_json(200, dict(_load_settings(), agent_name_default=__import__("hearth").default_agent_name()))
        if path == "/api/auto_review":
            from . import auto_review
            return self._send_json(200, auto_review.status())
        if path == "/api/gpu":
            return self._send_json(200, _gpu_snapshot())
        if path == "/api/tools":
            try:
                from .tools import _NEVER_PERM as _NP, is_risky as _isr
                out = []
                for _td in TOOL_DEFINITIONS:
                    _r = "never" if _td["name"] in _NP else ("risky" if _isr(_td["name"]) else "safe")
                    out.append({**_td, "risk": _r, "source": "builtin"})
                # Bridged MCP tools are callable, so they belong in the panel
                # too. Without them there was no way to deny one.
                try:
                    from . import mcp_client
                    for _mt in mcp_client.list_remote_tools():
                        out.append({
                            "name": _mt.get("name", ""),
                            "description": _mt.get("description", ""),
                            "parameters": _mt.get("parameters") or {},
                            "risk": "risky",
                            "source": "mcp",
                            "server": _mt.get("_mcp_server", ""),
                        })
                except Exception:
                    pass
                return self._send_json(200, {"tools": out})
            except Exception:
                return self._send_json(200, {"tools": TOOL_DEFINITIONS})
        if path == "/api/persona":
            return self._send_json(200, {"system_prompt": system_prompt()})
        if path == "/api/llmserver/status":
            from . import llmserver
            return self._send_json(200, llmserver.status(LOCAL_API_BASE))
        if path == "/api/runtime/builds":   # GET — the Engine pane reads this
            from . import llmserver
            return self._send_json(200, llmserver.list_llama_builds())
        if path == "/api/model/facts":
            # The model menu's details card: what a model takes in, whether it
            # reasons, its context. v is a menu row's value.
            from . import model_facts as _mf
            v = str(self._query().get("v") or "")
            kind, _, rest = v.partition("::")
            who, _, model = rest.partition("::")
            try:
                if kind == "disk":
                    facts = _mf.for_file(rest)
                elif kind == "agent":
                    facts = _mf.for_agent(who, model)
                elif kind == "prov" and model:
                    facts = _mf.for_provider(who, model)
                else:
                    facts = {}
            except Exception as e:
                facts = {"error": f"{type(e).__name__}: {e}"}
            return self._send_json(200, facts)
        if path == "/api/runtime/info":
            from . import llmserver
            _info = llmserver.llama_runtime_info()
            _info["advice"] = llmserver.runtime_advice()
            _info["update"] = llmserver.check_llama_update()  # cached 6h
            _info["preference"] = llmserver.get_engine_preference()
            return self._send_json(200, _info)
        if path == "/api/mcp/inbound":
            # Inbound MCP clients (Claude Desktop / LM Studio / Cursor …) that
            # spawned a hearth.mcp_server. Each writes ~/.hearth/mcp_clients/<pid>
            # .json on connect; we prune the ones whose process is gone.
            import glob as _glob
            clients = []
            d = os.path.join(os.path.expanduser("~/.hearth"), "mcp_clients")
            for fp in _glob.glob(os.path.join(d, "*.json")):
                try:
                    with open(fp, encoding="utf-8") as f:
                        c = json.load(f)
                    # A bare pid_exists is not enough: Windows recycles pids,
                    # so a session that ended long ago keeps showing as
                    # connected once an unrelated process inherits its number.
                    # Match the recorded start time too.
                    alive = True
                    try:
                        import psutil
                        _pid = int(c.get("pid", -1))
                        if not psutil.pid_exists(_pid):
                            alive = False
                        else:
                            _pr = psutil.Process(_pid)
                            _started = c.get("started_at")
                            if _started is not None:
                                alive = abs(_pr.create_time() - float(_started)) < 2.0
                            else:
                                # Older records have no start time; fall back to
                                # the process name, which still rules out a pid
                                # reused by something unrelated.
                                _want = (c.get("client") or "").lower()
                                alive = (not _want) or _pr.name().lower() == _want
                    except psutil.NoSuchProcess:
                        alive = False
                    except Exception:
                        alive = True
                    if alive:
                        clients.append(c)
                    else:
                        os.remove(fp)
                except Exception:
                    pass
            clients.sort(key=lambda c: c.get("connected_at", ""))
            return self._send_json(200, {"clients": clients})
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
        if path in ("/api/favicon", "/api/brand-icon"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if path == "/api/brand-icon":
                from .brand_icons import brand_icon, brand_icon_cached
                fetch, arg = brand_icon, q.get("k", [""])[0]
                icon = _icon_now(brand_icon_cached, fetch, arg)
            else:
                fetch, arg = _favicon, q.get("d", [""])[0]
                icon = _icon_now(_favicon_cached, fetch, arg)
            if not icon:
                # Not known yet (being fetched now) or none: the page shows its own stand-in, and
                # the browser doesn't keep this answer, so the next draw asks again. X-Icon says
                # which, so the page waits for one on its way and stops asking for one that isn't.
                self.send_response(404)
                self.send_header("X-Icon", "later" if _icon_fetching(fetch, arg) else "none")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data, mime = icon
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/agents/jobs":
            from . import external_agents as _xa
            return self._send_json(200, {"jobs": _xa.jobs()})
        if path == "/api/team/choices":
            return self._send_json(200, _team_choices())
        if path == "/api/voice/choices":
            # Voice mode's settings sheet: the voices, the languages it listens in, and
            # what is picked, the same list the phone's call sheet shows.
            from .call import languages, voices
            from . import voice as _vc
            st = _load_settings()
            sess = getattr(_rt_voice, "_session", None) if _rt_voice else None
            return self._send_json(200, {"voice": st.get("voice_name") or _vc.current_voice(), "voices": voices(),
                                         "language": (st.get("stt_language") or "").strip().lower(),
                                         "languages": languages(), "tones": st.get("voice_tones", True) is not False,
                                         "stt_device": (st.get("stt_device") or "cpu").lower(),
                                         "stt_model": st.get("stt_model") or "",
                                         "listening": getattr(sess, "stt", "") if sess is not None else ""})
        if path == "/api/voice/sample":
            # The line a voice reads when it is tried, in its own language.
            from . import voice as _vc
            return self._send_json(200, _vc.sample_line(self._query().get("voice", "")))
        if path == "/api/status":
            return self._send_json(200, status_snapshot())
        if path == "/api/usage":
            # One brain's usage, for a panel that shows a single agent.
            from . import usage as _usage
            want = self._query().get("id", "")
            return self._send_json(200, {"usage": [u for u in _usage.everything(_provider_key)
                                                   if not want or u["id"] == want]})
        if path == "/api/team":
            # Live state of helper agents for the Team view.
            try:
                from . import subagents as _sa
                return self._send_json(200, {"agents": _sa.team_state()})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        if path == "/api/team/changes":
            from . import helper_changes as _hc
            return self._send_json(200, _hc.act(self._query().get("id", "").strip(), "show"))
        if path == "/api/team/story":
            # One helper's work so far, for the panel beside the room.
            from . import subagents as _sa
            story = _sa.agent_story(self._query().get("id", "").strip())
            return self._send_json(200 if story else 404, story or {"error": "no such helper"})
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
        if path == "/api/llmserver/prompt-progress":
            # Cold-prompt processing progress for the built-in server, polled by
            # the chat UI during the wait before the first token so it can show
            # "Processing prompt 88%" instead of a frozen-looking spinner.
            from . import llmserver
            return self._send_json(200, {"progress": llmserver.prompt_progress()})
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
            if not q.strip():
                return self._send_json(200, {"results": llmserver.popular_chat_models()})
            return self._send_json(200, {"results": llmserver.search_huggingface(q)})
        if path == "/api/llmserver/vision":
            from . import llmserver
            mp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("path", [""])[0]
            if not mp or not os.path.isfile(mp):
                return self._send_json(400, {"state": "unknown", "error": "no such model file"})
            v = llmserver.vision_for_file(mp)
            if v.get("state") == "needs_file":
                v["job"] = llmserver.model_download_state(llmserver.vision_addon_name(v["repo"], v["file"]))
            return self._send_json(200, v)
        if path == "/api/llmserver/hf-files":
            # ?repo=user/model lists the .gguf files in that repo
            from . import llmserver
            repo = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("repo", [""])[0]
            return self._send_json(200, {"files": llmserver.list_hf_files(repo)})
        return self._not_found()

    def do_DELETE(self) -> None:
        if not self._request_allowed():
            return self._send_json(403, {"error": "forbidden origin"})
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/memory/"):
            name = urllib.parse.unquote(path[len("/api/memory/"):])
            return self._delete_memory(name)
        if path.startswith("/api/cards/"):
            from . import memory_cards
            memory_cards.forget(urllib.parse.unquote(path[len("/api/cards/"):]))
            return self._send_json(200, {"ok": True})
        if path.startswith("/api/conversations/"):
            cid = urllib.parse.unquote(path[len("/api/conversations/"):])
            ok = _delete_convo(cid)
            return self._send_json(200 if ok else 404, {"ok": ok})
        return self._not_found()

    def _stream_model_download(self, repo: str, filename: str, *,
                               found: Optional[Dict[str, Any]] = None,
                               error: str = "", dest_dir: str = "", save_as: str = "",
                               vision_for: str = "") -> None:
        """Report a model download as NDJSON: {type:"progress", done, total}
        while it runs, then {type:"done", ok, path|error}. The download itself
        runs on a backend thread (llmserver), so closing the window drops this
        connection and the transfer carries on; pausing is its own call."""
        from . import llmserver
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def send(obj: Dict[str, Any]) -> None:
            self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            if error or not repo or not filename:
                return send({"type": "done", "ok": False,
                             "error": error or "repo and filename required"})
            if found:
                return send({"type": "done", **found})
            llmserver.start_model_download(repo, filename, dest_dir=dest_dir, save_as=save_as, vision_for=vision_for)
            last = None
            while True:
                st = llmserver.model_download_state(save_as or filename) or {}
                if st.get("status") != "downloading":
                    res = st.get("result") or {"ok": False, "error": "download stopped"}
                    return send({"type": "done", **res})
                cur = (st.get("done"), st.get("total"))
                if cur != last and cur[1]:
                    last = cur
                    send({"type": "progress", "done": cur[0], "total": cur[1]})
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def do_POST(self) -> None:
        if not self._request_allowed():
            return self._send_json(403, {"error": "forbidden origin"})
        # Hoisted: the brain-switch branch reassigns LOCAL_API_BASE, and other
        # branches (e.g. /api/memory/import) read it — so the global must be
        # declared before any use in this function, not mid-body.
        global LOCAL_API_BASE
        path = urllib.parse.urlparse(self.path).path
        if path == "/chat":
            return self._stream_chat()
        if path == "/api/events":
            # Another process (a bridge, the terminal) saying what changed; passed to every listener.
            ev = self._read_json()
            if isinstance(ev, dict) and ev.get("kind") in ("chat", "settings", "turn", "phone_notice"):
                live_bus.publish(ev)
            return self._send_json(200, {"ok": True})
        if path == "/api/cancel":
            _CANCEL.set()  # run_once checks this between turns and bails out
            return self._send_json(200, {"cancelled": True})
        if path == "/api/steer":
            # Queue a message typed mid-generation. run_once drains it between
            # tool iterations and injects it as a user turn (steering, not a new
            # turn). Non-blocking: returns immediately, the running turn picks it
            # up on its next iteration.
            try:
                body = self._read_json()
            except Exception:
                body = {}
            text = (body.get("text") or "").strip()
            if not text:
                return self._send_json(400, {"error": "empty steer"})
            with _STEER_LOCK:
                _STEER_Q.append(text)
                depth = len(_STEER_Q)
            return self._send_json(200, {"queued": True, "pending": depth})
        if path == "/api/file/reveal":
            # Open the OS file explorer with the given file selected. Safety:
            # same workspace-sandbox check as /file so a chat-injected path
            # can't pop arbitrary Explorer windows. Windows uses
            # `explorer /select,<path>`, mac uses `open -R`, linux uses
            # whatever xdg-open does for the parent dir (no select equivalent).
            return self._reveal_in_folder()
        if path == "/api/model/reveal":
            # Reveal a MODEL file in Explorer. Models live outside the workspace
            # (LM Studio / HF-cache / ~/.hearth), so this is NOT sandboxed — it
            # only accepts an existing .gguf (or a directory) so it can't become a
            # general "open any path" primitive from a chat-injected path.
            return self._reveal_model_path()
        if path == "/api/runtime/reveal":
            # Show an installed engine build's folder in Explorer — mirrors the
            # model reveal. Only ever a real Hearth-managed build dir (resolved
            # from the tag), never an arbitrary path.
            from . import llmserver
            _tag = (self._read_json().get("tag") or "").strip()
            _d = llmserver.engine_build_dir(_tag)
            if not _d:
                return self._send_json(404, {"error": "no such engine build"})
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", os.path.realpath(_d)],
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", _d])
                else:
                    subprocess.Popen(["xdg-open", _d])
                return self._send_json(200, {"ok": True})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/runtime/remove":
            # Delete an installed engine build by tag — mirrors deleting a model.
            from . import llmserver
            _tag = (self._read_json().get("tag") or "").strip()
            return self._send_json(200, llmserver.remove_llama_build(_tag))
        if path == "/api/agent/rename":
            return self._rename_agent()
        if path == "/api/workspace/relocate":
            return self._relocate_workspace()
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
        if path == "/api/auto_review/check":
            from . import auto_review
            started = auto_review.check_in_background(again=True)
            return self._send_json(200, dict(auto_review.status(), started=started))
        if path == "/api/voice/mic-test":
            try:
                from . import listen as _mic   # not `_listen`: that name in here shadows the module-wide one
                return self._send_json(200, _mic.mic_test())
            except Exception as e:
                return self._send_json(200, {"ok": False, "error": str(e)})
        if path == "/api/models/set-dir":
            from . import llmserver
            b = self._read_json()
            r = llmserver.set_models_dir(b.get("path", ""))
            if r.get("ok"):
                _models_cache["ts"] = 0  # force a rescan against the new folder
            return self._send_json(200, r)
        if path == "/api/lockdown":
            from . import tools as _t
            b = self._read_json()
            return self._send_json(200, {"on": _t.set_lockdown(bool(b.get("on")))})
        if path == "/api/email/save":
            b = self._read_json()
            try:
                from . import email_tools
                return self._send_json(200, email_tools.save_config(
                    b.get("address", ""), b.get("password", ""),
                    b.get("imap_host", ""), b.get("smtp_host", ""),
                    b.get("imap_port"), b.get("smtp_port")))
            except Exception as e:
                return self._send_json(200, {"ok": False, "error": str(e)})
        if path == "/api/email/test":
            try:
                from . import email_tools
                return self._send_json(200, email_tools.test_connection())
            except Exception as e:
                return self._send_json(200, {"ok": False, "error": str(e)})
        if path == "/api/email/clear":
            try:
                from . import email_tools
                return self._send_json(200, email_tools.clear_config())
            except Exception as e:
                return self._send_json(200, {"ok": False, "error": str(e)})
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
        if path == "/api/memory/item":
            # "wrong / forget" or "done with this" on what a reply had in mind.
            from . import memory_cards
            ob = self._read_json() or {}
            r = memory_cards.act(str(ob.get("kind") or ""), str(ob.get("id") or ""), str(ob.get("action") or ""))
            return self._send_json(200 if r.get("ok") else 400, r)
        if path == "/api/memory-share":
            from . import shared_memory as _sm
            ob = self._read_json() or {}
            r = _sm.set_shared(str(ob.get("agent") or ""), bool(ob.get("on")))
            return self._send_json(200 if r.get("ok") else 400, r)
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
                _llm = _mx.make_openai_llm_call(_sync, _model)
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
        if path == "/api/update/check":
            try:
                from . import updater
                # Keep in sync with hearth_cli.HEARTH_VERSION + Hearth.iss.
                return self._send_json(200, updater.check_for_update())
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/update/apply":
            try:
                from . import updater
                return self._send_json(200, {"ok": True, "result": updater.apply_update()})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/update/cancel":
            from . import updater
            return self._send_json(200, updater.cancel_installer_download())
        if path == "/api/update/capability":
            from . import updater
            return self._send_json(200, {"can_patch": updater.can_patch()})
        if path == "/api/update/restart":
            from . import updater
            return self._send_json(200, updater.restart_app())
        if path == "/api/update/repair":
            # Restore deleted/corrupted loose files (code + voice) by re-fetching
            # and re-applying this version's patch. Same NDJSON progress shape.
            from . import updater
            import time as _t
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            _last = [0.0]

            def emit(obj: Dict[str, Any]) -> None:
                try:
                    self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def on_progress(done: int, total: int) -> None:
                now = _t.time()
                if now - _last[0] < 0.2 and done != total:
                    return
                _last[0] = now
                emit({"type": "progress", "done": done, "total": total})
            try:
                r = updater.repair(on_progress=on_progress)
                emit({"type": "done", **r, "repaired": bool(r.get("ok"))})
            except Exception as e:
                emit({"type": "done", "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/update/patch":
            # Small code-only update: download the patch, apply it over the
            # on-disk .py layer, and report. Same NDJSON shape as the installer
            # download so the GUI can drive both with one progress path.
            from . import updater
            import time as _t
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            _last = [0.0]

            def emit(obj: Dict[str, Any]) -> None:
                try:
                    self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def on_progress(done: int, total: int) -> None:
                now = _t.time()
                if now - _last[0] < 0.2 and done != total:
                    return
                _last[0] = now
                emit({"type": "progress", "done": done, "total": total})

            try:
                got = updater.download_patch(on_progress=on_progress)
                if not got.get("ok"):
                    emit({"type": "done", **got})
                    return
                applied = updater.apply_patch(got["path"])
                emit({"type": "done", **applied, "patched": bool(applied.get("ok")),
                      "tag": got.get("tag")})
            except Exception as e:
                emit({"type": "done", "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/update/download":
            # Download the installer matching THIS edition, streaming NDJSON
            # progress so the GUI shows the same bar/cancel as a model or engine
            # download instead of dumping the user on a browser download page.
            from . import updater
            import time as _t
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            _last = [0.0]

            def emit(obj: Dict[str, Any]) -> None:
                try:
                    self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def on_progress(done: int, total: int) -> None:
                now = _t.time()
                if now - _last[0] < 0.2 and done != total:
                    return
                _last[0] = now
                emit({"type": "progress", "done": done, "total": total})

            try:
                result = updater.download_installer(on_progress=on_progress)
                emit({"type": "done", **result})
            except Exception as e:
                emit({"type": "done", "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/update/launch":
            from . import updater
            body = self._read_json()
            return self._send_json(200, updater.launch_installer((body.get("path") or "").strip()))
        if path == "/api/announcements/check":
            # Poll the broadcast feed for announcements this build hasn't shown.
            try:
                from . import announcements as _ann
                return self._send_json(200, {"items": _ann.fetch_new()})
            except Exception as e:
                return self._send_json(200, {"items": [], "error": f"{type(e).__name__}: {e}"})
        if path == "/api/announcements/publish":
            # Author-side: append to the local announcements.json (then push it).
            body = self._read_json()
            try:
                from . import announcements as _ann
                return self._send_json(200, _ann.publish(
                    body.get("title") or "", body.get("body") or "",
                    body.get("kind") or "info"))
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/skills/inspect":
            # Phase 1: fetch + parse a skill, return its manifest for the
            # confirm dialog. Does NOT install.
            body = self._read_json()
            try:
                from . import skills_install as _si
                m = _si.inspect_source((body.get("source") or "").strip())
                _staged = m.pop("_staged", None)
                if _staged:
                    try:
                        import shutil as _sh, pathlib as _pl
                        _sh.rmtree(_staged, ignore_errors=True)
                    except Exception:
                        pass
                return self._send_json(200, m)
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/skills/install":
            # Phase 2: install after the user confirmed the manifest in the UI.
            body = self._read_json()
            try:
                from . import skills_install as _si
                res = _si.install_skill((body.get("source") or "").strip(),
                                        consent=lambda _m: True,
                                        force=bool(body.get("force")))
                return self._send_json(200, res)
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/mcp/reload":
            # Apply an edited mcp.json without restarting Hearth. The client has
            # always supported shutdown then bootstrap; it was simply never
            # reachable, so saving the file told people to restart the app.
            from . import mcp_client as _mc
            try:
                _mc.shutdown()
            except Exception:
                pass
            try:
                res = _mc.bootstrap()
            except Exception as e:
                return self._send_json(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            try:    # a reconnect changes which mcp_ tools exist
                from .tools import reset_unlocked_tools
                reset_unlocked_tools()
            except Exception:
                pass
            return self._send_json(200, res if isinstance(res, dict) else {"ok": True})
        if path == "/api/voice/install-models":
            # Fetch the Kokoro weights (~100MB). Speaking used to pull these
            # inline, which blocked the request for minutes with nothing on
            # screen, so the Test button read as a silent failure and the Lite
            # build had no other way to get voice at all. Runs in the background
            # and reports through the same channel the GPU install uses, so the
            # UI polls /api/voice/install-status either way.
            if not _voice:
                return self._send_json(503, {"ok": False, "error": "voice module missing"})
            try:
                if _voice.models_present():
                    return self._send_json(200, {"ok": True, "already": True})
            except Exception:
                pass
            if _VOICE_INSTALL.get("running"):
                return self._send_json(200, {"ok": True, "already": True})
            threading.Thread(target=_install_voice_models_worker, daemon=True).start()
            return self._send_json(200, {"ok": True, "started": True})

        if path == "/api/voice/install-cancel":
            # Stops a voice download from the downloads overlay, matching the
            # Cancel every other download kind already has.
            _VOICE_INSTALL_CANCEL.set()
            return self._send_json(200, {"ok": True})
        if path == "/api/voice/install-gpu":
            # Install the GPU runtime for STT so whisper runs on the user's card
            # instead of slow CPU. CUDA whisper = ctranslate2 needs the cuBLAS +
            # cuDNN wheels; DirectML = onnxruntime-directml. Runs in the
            # background; the UI polls /api/voice/install-status.
            body = self._read_json()
            dev = (body.get("device") or "cuda").strip().lower()
            # The GUI can pass the exact pip package(s) for the option it's
            # installing (TTS GPU = onnxruntime-gpu, etc.); otherwise fall back
            # to the device map (STT CUDA = cuBLAS+cuDNN, DirectML = ort-dml).
            explicit = body.get("packages")
            if isinstance(explicit, str):
                explicit = explicit.split()
            pkgs = explicit or {"cuda": ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"],
                                "dml":  ["onnxruntime-directml"]}.get(dev)
            if not pkgs:
                return self._send_json(400, {"ok": False, "error": f"unknown device '{dev}'"})
            if _VOICE_INSTALL.get("running"):
                return self._send_json(200, {"ok": True, "already": True})

            def _go(_pkgs=pkgs, _dev=dev):
                global _VOICE_INSTALL
                _VOICE_INSTALL = {"running": True, "device": _dev, "pct": None,
                                  "speed": "", "downloaded": "", "step": "resolving…"}
                try:
                    import subprocess as _sp, re as _re, os as _os
                    import tempfile as _tf, shutil as _sh
                    env = dict(_os.environ, PYTHONUNBUFFERED="1")
                    prog = _re.compile(r"([\d.]+)/([\d.]+)\s*(kB|MB|GB)")
                    spd = _re.compile(r"([\d.]+\s*[kKMG]?B/s)")
                    dl = _re.compile(r"Downloading\s+([\w.\-]+)")
                    tail = []

                    def _stream_pip(args):
                        """Run pip, stream its progress into _VOICE_INSTALL, return rc."""
                        _pip_cmd = [sys.executable, "-m", "pip", *args]
                        if getattr(sys, "frozen", False):
                            _pip_cmd = [sys.executable, "--hearth-run-python",
                                        "-m", "pip", *args]
                        proc = _sp.Popen(
                            _pip_cmd,
                            stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True, bufsize=1,
                            env=env, creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                        buf = ""
                        while True:
                            ch = proc.stdout.read(1)
                            if not ch:
                                break
                            if ch in ("\r", "\n"):
                                line = buf.strip(); buf = ""
                                if not line:
                                    continue
                                tail.append(line); tail[:] = tail[-12:]
                                d = dl.search(line)
                                if d:
                                    _VOICE_INSTALL["step"] = "downloading " + d.group(1).split("-")[0]
                                    _VOICE_INSTALL["pct"] = None
                                m = prog.search(line)
                                if m:
                                    cur, tot = float(m.group(1)), float(m.group(2))
                                    if tot > 0:
                                        _VOICE_INSTALL["pct"] = round(cur / tot * 100)
                                        _VOICE_INSTALL["downloaded"] = f"{cur:.0f}/{tot:.0f} {m.group(3)}"
                                sm = spd.search(line)
                                if sm:
                                    _VOICE_INSTALL["speed"] = sm.group(1).replace(" ", "")
                            else:
                                buf += ch
                        return proc.wait()

                    # onnxruntime, -gpu and -directml ship the SAME `onnxruntime`
                    # module and collide, so switching devices means removing the
                    # old one. The naive "uninstall then install" left the user
                    # with NO runtime if they closed the app mid-download. So:
                    # DOWNLOAD first (the long part, non-destructive — the working
                    # runtime stays put), and only swap once the wheels are fully
                    # local, shrinking the broken window from minutes to seconds.
                    if any("onnxruntime" in p for p in _pkgs):
                        _tmp = _tf.mkdtemp(prefix="hearth_ort_")
                        try:
                            rc = _stream_pip(["download", "--progress-bar", "on",
                                              "--dest", _tmp, *_pkgs])
                            if rc != 0:
                                raise RuntimeError("download failed (runtime untouched)")
                            _VOICE_INSTALL.update(step="swapping runtime…", pct=None, speed="")
                            _uninstall_cmd = [sys.executable, "-m", "pip", "uninstall", "-y",
                                     "onnxruntime", "onnxruntime-gpu", "onnxruntime-directml"]
                            if getattr(sys, "frozen", False):
                                _uninstall_cmd = [sys.executable, "--hearth-run-python",
                                     "-m", "pip", "uninstall", "-y",
                                     "onnxruntime", "onnxruntime-gpu", "onnxruntime-directml"]
                            _sp.run(_uninstall_cmd,
                                    capture_output=True, text=True,
                                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                            rc = _stream_pip(["install", "--no-index",
                                              "--find-links", _tmp, *_pkgs])
                            ok = rc == 0
                        finally:
                            _sh.rmtree(_tmp, ignore_errors=True)
                    else:
                        ok = _stream_pip(["install", "--progress-bar", "on", *_pkgs]) == 0

                    _VOICE_INSTALL = {
                        "running": False, "done": True, "ok": ok, "device": _dev,
                        "msg": ("Installed — toggle voice off/on to use the GPU."
                                if ok else ("\n".join(tail))[-400:] or "pip failed"),
                    }
                except Exception as e:
                    _VOICE_INSTALL = {"running": False, "done": True, "ok": False,
                                      "msg": f"{type(e).__name__}: {e}"[-400:]}

            threading.Thread(target=_go, daemon=True).start()
            return self._send_json(200, {"ok": True, "started": True})
        if path == "/api/skills/toggle":
            try:
                body = self._read_json()
                name = (body.get("name") or "").strip()
                if not name:
                    return self._send_json(400, {"ok": False, "error": "missing name"})
                cur = _load_settings()
                off = [n for n in (cur.get("skills_off") or []) if n != name]
                if body.get("on") is False:
                    off.append(name)
                _save_settings(dict(cur, skills_off=off))
                return self._send_json(200, {"ok": True, "name": name, "on": name not in off})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/skills/remove":
            body = self._read_json()
            try:
                from . import skills_install as _si
                return self._send_json(200, _si.uninstall_skill((body.get("name") or "").strip()))
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path.startswith("/api/plus/"):
            from . import plus as _plus, relay_bridge as _rb
            body = self._read_json()
            act = path.rsplit("/", 1)[1]
            if act == "activate":
                r = _plus.activate(str(body.get("key") or ""))
                if r.get("ok") and _bridge_configured("relay"):
                    _spawn_bridge("relay")
                return self._send_json(200, r)
            if act == "deactivate":
                _stop_bridge("relay")
                return self._send_json(200, _plus.deactivate())
            if act == "pair":
                if not _plus.active():
                    return self._send_json(200, {"ok": False, "error": "pairing a phone needs Inglen Plus on this PC"})
                r = _rb.start_pairing(str(body.get("name") or ""))
                if r.get("ok"):
                    import segno
                    buf = io.BytesIO()
                    # A viewBox and no fixed size, so the code scales into its box whole, with
                    # the quiet zone of four modules that scanners need.
                    segno.make(r["link"], error="m").save(buf, kind="svg", scale=5, border=4, dark="#111",
                                                          light="#fff", omitsize=True, xmldecl=False)
                    r["qr"] = buf.getvalue().decode("utf-8")
                    running, err = _spawn_bridge("relay")
                    if not running:
                        r = {"ok": False, "error": err or "the relay bridge didn't start"}
                return self._send_json(200, r)
            if act == "confirm":
                return self._send_json(200, _rb.confirm_pairing(str(body.get("name") or "")))
            if act == "cancel":
                return self._send_json(200, _rb.cancel_pairing())
            if act == "forget":
                return self._send_json(200, _rb.forget_phone(str(body.get("id") or "")))
            return self._send_json(404, {"ok": False, "error": "unknown Plus action"})
        if path == "/api/connectors/access":
            # Approve, dismiss or remove someone on a chat app.
            from . import channel_core as _cc
            body = self._read_json()
            ch, act = str(body.get("channel") or ""), str(body.get("action") or "")
            if act == "approve":
                return self._send_json(200, _cc.approve(ch, str(body.get("code") or "")))
            if act == "dismiss":
                return self._send_json(200, _cc.dismiss(ch, str(body.get("code") or "")))
            if act == "revoke":
                return self._send_json(200, _cc.revoke(ch, str(body.get("sender") or "")))
            return self._send_json(400, {"ok": False, "error": "action is approve, dismiss or revoke"})
        if path == "/api/bridges/start":
            # Launch a configured bridge as its own process so the bot comes
            # ONLINE without the user running a terminal command.
            body = self._read_json()
            ch = (body.get("channel") or "").strip().lower()
            if ch not in _BRIDGE_MODULES and not ch.startswith("custom:"):
                return self._send_json(400, {"ok": False, "error": "unknown channel"})
            if _bridge_running(ch):
                return self._send_json(200, {"ok": True, "running": True, "note": "already running"})
            try:
                running, err = _spawn_bridge(ch)
                return self._send_json(200, {"ok": running, "running": running, "error": err})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/bridges/stop":
            body = self._read_json()
            _stop_bridge((body.get("channel") or "").strip().lower())
            return self._send_json(200, {"ok": True, "running": False})
        if path == "/api/bridges/config":
            # Save a phone-bridge config (~/.hearth/<file>.json) from the GUI so
            # users don't hand-edit JSON. Channel -> file map below.
            body = self._read_json()
            ch = (body.get("channel") or "").strip().lower()
            fn = {"telegram": "phone_bridge.json", "discord": "discord_bridge.json",
                  "whatsapp": "whatsapp_bridge.json"}.get(ch)
            if not fn:
                return self._send_json(400, {"ok": False, "error": "unknown channel"})
            import json as _j
            base = os.path.join(os.path.expanduser("~"), ".hearth")
            try:
                os.makedirs(base, exist_ok=True)
                with open(os.path.join(base, fn), "w", encoding="utf-8") as f:
                    _j.dump(dict(body.get("config") or {}), f, indent=2)
                return self._send_json(200, {"ok": True})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/autostart":
            # Toggle launch-at-login for the tray. POST {enabled: bool} to set;
            # POST {} (no key) just reports the current state. Mirrors the entry
            # in Task Manager > Startup.
            body = self._read_json()
            try:
                from . import install_shortcuts as _sh
                if "enabled" in body:
                    state = _sh.set_autostart(bool(body.get("enabled")))
                else:
                    state = _sh.is_autostart_enabled()
                return self._send_json(200, {"ok": True, "enabled": state})
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
                # NOTE: do NOT re-import `from . import listen as _listen` here —
                # it made `_listen` a LOCAL for the whole do_POST, so every other
                # `if _listen:` in this method (e.g. STT model change) crashed with
                # UnboundLocalError. Use the module-global imported at the top.
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
        if path == "/api/jobs/background-now":
            from . import jobs as _jobs
            return self._send_json(200, {"ok": True, "jobs": _jobs.background_now()})
        if path == "/api/reminders/update":
            try:
                from . import reminders as _r
                body = self._read_json()
                rid = (body.get("id") or "").strip()
                if not rid:
                    return self._send_json(400, {"ok": False, "error": "missing id"})
                return self._send_json(200, _r.update_reminder(rid, body.get("what"), body.get("when"),
                                                               body.get("recurring"), body.get("until")))
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/reminders/pause":
            try:
                from . import reminders as _r
                body = self._read_json()
                rid = (body.get("id") or "").strip()
                if not rid:
                    return self._send_json(400, {"ok": False, "error": "missing id"})
                return self._send_json(200, _r.pause_reminder(rid, body.get("paused", True) is not False))
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/reminders/done":
            from . import reminders as _r
            rid = (self._read_json().get("id") or "").strip()
            if not rid:
                return self._send_json(400, {"ok": False, "error": "missing id"})
            return self._send_json(200, _r.done_reminder(rid))
        if path == "/api/crash/dismiss":
            from . import crashlog as _cl
            _cl.dismiss()
            return self._send_json(200, {"ok": True})
        if path == "/api/crash/report":
            from . import crashlog as _cl
            p = _cl.pending()
            _cl.dismiss()
            return self._send_json(200, {"ok": bool(p), "url": _cl.report_url(p) if p else ""})
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
                    data = json.loads(txt)
                except Exception as e:
                    return self._send_json(400, {"ok": False, "error": f"JSON: {e}"})
                # Schema validation — catch typos like {"foo":123} before restart
                if not isinstance(data, dict):
                    return self._send_json(400, {"ok": False, "error": "config must be a JSON object with mcpServers"})
                servers = data.get("mcpServers")
                if servers is not None:
                    if not isinstance(servers, dict):
                        return self._send_json(400, {"ok": False, "error": "mcpServers must be an object"})
                    for name, spec in servers.items():
                        if not isinstance(spec, dict):
                            return self._send_json(400, {"ok": False, "error": f"server '{name}' must be an object"})
                        if "command" not in spec or not isinstance(spec["command"], str) or not spec["command"].strip():
                            return self._send_json(400, {"ok": False, "error": f"server '{name}' missing 'command' string"})
                        if "args" in spec and not isinstance(spec["args"], list):
                            return self._send_json(400, {"ok": False, "error": f"server '{name}' args must be an array"})
                        if "env" in spec and not isinstance(spec["env"], dict):
                            return self._send_json(400, {"ok": False, "error": f"server '{name}' env must be an object"})
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
            if "pinned" not in data:
                # A window that loaded the chat before it was pinned elsewhere
                # must not unpin it by saving.
                prev = _load_convo(data["id"]) or {}
                if prev.get("pinned"):
                    data["pinned"] = True
            # Turns the terminal added that this window hadn't read yet stay.
            from . import chat_sync as _cs
            data = _cs.keep_terminal_turns(_load_convo(data["id"]), data)
            ok = _save_convo(data)
            return self._send_json(200 if ok else 500, {"ok": ok})
        if path == "/api/undo":
            from . import undo as _undo
            return self._send_json(200, _undo.undo(str(self._read_json().get("id") or "")))
        if path == "/api/conversations/seen":
            data = _load_convo((self._read_json().get("id") or "").strip())
            if not data:
                return self._send_json(404, {"ok": False, "error": "conversation not found"})
            if data.pop("unread", None) is not None:
                _save_convo(data)
            return self._send_json(200, {"ok": True})
        if path == "/api/conversations/pin":
            body = self._read_json()
            cid = (body.get("id") or "").strip()
            data = _load_convo(cid)
            if not data:
                return self._send_json(404, {"ok": False, "error": "conversation not found"})
            if body.get("pinned"):
                data["pinned"] = True
            else:
                data.pop("pinned", None)
            ok = _save_convo(data)
            return self._send_json(200 if ok else 500, {"ok": ok, "pinned": bool(data.get("pinned"))})
        if path == "/api/conversations/compact":
            # Real, invisible compaction (the "subagent way"): summarize the older
            # turns server-side and REPLACE them with one compact marker — no fake
            # "Compact this conversation:" user message in the transcript, and the
            # history actually shrinks. Keeps the recent tail intact.
            body = self._read_json()
            cid = (body.get("id") or "").strip()
            keep = max(2, int(body.get("keep") or 6))
            data = _load_convo(cid)
            if not data:
                return self._send_json(404, {"ok": False, "error": "conversation not found"})
            msgs = data.get("messages") or []
            if len(msgs) <= keep + 2:
                return self._send_json(200, {"ok": False, "error": "not enough messages to compact"})
            older, tail = msgs[:-keep], msgs[-keep:]
            try:
                # The chat's memory pass reads it whole, before it is summarized.
                from . import memory_pass
                threading.Thread(target=memory_pass.now, args=(cid, list(msgs)), daemon=True).start()
            except Exception:
                pass

            def _txt(m):
                c = m.get("content")
                return c if isinstance(c, str) else json.dumps(c, default=str)
            transcript = "\n".join(f"{m.get('role','?')}: {_txt(m)[:2000]}"
                                   for m in older if _txt(m).strip())
            if not transcript.strip():
                return self._send_json(200, {"ok": False, "error": "nothing to summarize"})
            _s = _load_settings()
            _model = (_s.get("llm_model") or "").strip()
            _key = os.environ.get("LOCAL_API_KEY") or _s.get("llm_key") or "hearth-builtin"
            if not _model:
                try:
                    import urllib.request as _ur
                    _req = _ur.Request(f"{LM_STUDIO_V0}/models",
                                       headers={"Authorization": f"Bearer {_key}"})
                    with _ur.urlopen(_req, timeout=3) as r:
                        for m in json.loads(r.read().decode()).get("data", []):
                            if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm"):
                                _model = m.get("id"); break
                except Exception:
                    pass
            if not _model:
                return self._send_json(200, {"ok": False, "error": "no model loaded to summarize with"})
            try:
                import openai as _oai
                from . import memory_extract as _mx
                _sync = _oai.OpenAI(api_key=_key, base_url=LOCAL_API_BASE)
                _llm = _mx.make_openai_llm_call(_sync, _model)
                summary = (_llm(
                    "You compress a conversation. Output ONLY a tight summary, no preamble.",
                    "Summarize the older turns below in 4-6 short bullet lines. Keep concrete "
                    "facts, decisions, names, file paths, numbers and open tasks; drop greetings "
                    "and filler.\n\n" + transcript + "\n\nSummary:") or "").strip()
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": f"summarize failed: {type(e).__name__}: {e}"})
            if not summary:
                return self._send_json(200, {"ok": False, "error": "empty summary"})
            marker = {"role": "assistant",
                      "content": "**Earlier context (compacted):**\n" + summary,
                      "compacted": True}
            data["messages"] = [marker] + tail
            data["updated"] = time.time()
            _save_convo(data)
            return self._send_json(200, {"ok": True, "compacted": len(older),
                                         "messages": data["messages"]})
        if path == "/api/logs/clear":
            # Clears AGENT log (activity.jsonl). The Logs view's clear button
            # passes which=server when the dropdown is showing server logs,
            # so the button no longer cross-wires onto the wrong file.
            q = self._query()
            which = (q.get("which") or "agent").lower()
            # Truncate in place, no backup. A rename/delete fails on Windows
            # while the llama.cpp child still holds llamaserver.log open
            # (WinError 32), and users don't want hoarded *.log.bak files full
            # of /v1/models poll spam. The server log goes through llmserver's
            # own held handle so a truncate lands even while it's locked.
            try:
                if which == "server":
                    from . import llmserver
                    if not llmserver.clear_server_log():
                        return self._send_json(500, {"error": "could not clear server log"})
                else:
                    log_path = os.path.join(LOGS_DIR, "activity.jsonl")
                    if os.path.isfile(log_path):
                        with open(log_path, "w", encoding="utf-8") as f:
                            f.write("")
                return self._send_json(200, {"ok": True, "cleared": which})
            except OSError as e:
                return self._send_json(500, {"error": str(e)})
        if path == "/api/upload":
            return self._upload()
        if path == "/api/brain/permission":
            # Claude Code or Codex, run as the brain, asking through Hearth's MCP
            # server whether it may do something. Answered by the running turn's own
            # permission check, so the approval strip and voice question appear.
            body = self._read_json() or {}
            from . import claude_brain
            return self._send_json(200, claude_brain.decide(
                str(body.get("token") or ""), str(body.get("tool") or ""), body.get("input") or {}))
        if path == "/api/brain/desktop":
            # The brain's tool server acting on the desktop: the glow and pill live here.
            body = self._read_json() or {}
            from . import claude_brain
            return self._send_json(200, claude_brain.desktop(
                str(body.get("token") or ""), str(body.get("tool") or ""), body.get("input") or {}))
        if path == "/api/brain/tool":
            # A helper tool the brain called: run here, where helpers live.
            body = self._read_json() or {}
            from . import claude_brain
            return self._send_json(200, claude_brain.run_tool(
                str(body.get("token") or ""), str(body.get("tool") or ""), body.get("input") or {}))
        if path == "/api/providers/models":
            # What one saved provider offers, asked with its saved key, so the
            # menu can list it without switching to it.
            body = self._read_json() or {}
            pid = str(body.get("id") or "").strip().lower()
            from .headless import PROVIDERS, provider_models
            url = PROVIDERS[pid][1] if pid in PROVIDERS else next(
                (p["url"] for p in _saved_providers() if p["id"] == pid), "")
            key = _provider_key(pid)
            if not url or not key:
                return self._send_json(404, {"error": "no key is saved for that provider"})
            models = provider_models(url, key, timeout=10)
            if models is None:
                return self._send_json(502, {"error": "the provider didn't answer with a model list"})
            return self._send_json(200, {"models": models})
        if path in ("/api/agents/install", "/api/agents/signin"):
            # The setup dialog's two buttons: get the agent, then sign in to it.
            body = self._read_json() or {}
            from . import external_agents as _xa
            r = (_xa.install if path.endswith("install") else _xa.sign_in)(str(body.get("id") or ""))
            return self._send_json(200 if r.get("ok") else 400, r)
        if path == "/api/agents/cancel":
            # The Cancel button on an install or update in the Downloads list.
            body = self._read_json() or {}
            from . import external_agents as _xa
            ok = _xa.cancel_job(str(body.get("id") or ""))
            return self._send_json(200 if ok else 400, {"ok": ok} if ok else {"ok": False, "error": "nothing to stop"})
        if path == "/api/agents/update":
            # Asked for from the "a newer one is out" prompt; never on its own.
            body = self._read_json() or {}
            from . import external_agents as _xa
            r = _xa.update(str(body.get("id") or ""))
            return self._send_json(200 if r.get("ok") else 400, r)
        if path == "/api/file/open":
            # A file from the Files tab, in the app Windows opens it with.
            ob = self._read_json() or {}
            real, why = _workspace_file(str(ob.get("path") or ""))
            if why:
                return self._send_json(400, {"ok": False, "error": why})
            try:
                if ob.get("exe"):
                    from . import openers as _op
                    _op.open_with(real, str(ob["exe"]))
                elif sys.platform == "win32":
                    os.startfile(real)  # type: ignore[attr-defined]
                else:
                    import subprocess as _sp
                    _sp.Popen(["open" if sys.platform == "darwin" else "xdg-open", real])
                return self._send_json(200, {"ok": True})
            except (OSError, ValueError) as e:
                return self._send_json(500, {"ok": False, "error": str(e)})
        if path == "/api/voice/mic-privacy":
            # Windows' own switch for the mic, when it keeps voice mode from listening.
            if sys.platform != "win32":
                return self._send_json(400, {"ok": False})
            try:
                os.startfile("ms-settings:privacy-microphone")
                return self._send_json(200, {"ok": True})
            except OSError as e:
                return self._send_json(500, {"ok": False, "error": str(e)})
        if path == "/api/voice/realtime/mute":
            # The mic button in voice mode. Kept here, not in the page: the
            # recorder is what has to stop keeping what it hears.
            body = self._read_json() or {}
            from . import realtime_voice as _rtv
            _rtv.set_muted(bool(body.get("muted")))
            return self._send_json(200, {"ok": True, "muted": _rtv.is_muted()})
        if path == "/api/brain/agent":
            # Pick an agent program as the brain from the model sticker, or
            # with no provider go back to the model endpoint in Settings.
            body = self._read_json() or {}
            from . import claude_brain
            err = claude_brain.set_brain(str(body.get("provider") or "").strip().lower(),
                                         str(body["model"]).strip() if "model" in body else None)
            if err:
                return self._send_json(400, {"ok": False, "error": err})
            return self._send_json(200, {"ok": True, "provider": claude_brain.current() or ""})
        if path == "/api/team/tell":
            # A message typed to a working helper from its panel in the Team view.
            body = self._read_json() or {}
            from . import subagents as _sa
            r = _sa.tell_subagent(str(body.get("id") or ""), str(body.get("text") or ""))
            return self._send_json(200 if r.get("ok") else 400, r)
        if path == "/api/team/changes":
            # Bring in, discard or undo a helper's changes from its panel. The
            # person clicked it, so there is no permission prompt.
            body = self._read_json() or {}
            from . import helper_changes as _hc
            r = _hc.act(str(body.get("id") or ""), str(body.get("action") or ""))
            return self._send_json(200 if r.get("ok") else 400, r)
        if path == "/api/team/stop":
            body = self._read_json() or {}
            from . import subagents as _sa
            r = _sa.stop_subagent(str(body.get("id") or ""))
            return self._send_json(200 if r.get("ok") else 400, r)
        if path == "/api/team/start":
            # Starting a helper by hand from the Team view: one of Hearth's
            # roles, or a task handed to an installed agent CLI. The person
            # chose this themselves, so there is no permission prompt.
            body = self._read_json() or {}
            task = str(body.get("task") or "").strip()
            agent = str(body.get("agent") or "").strip().lower()
            role = str(body.get("persona") or "").strip().lower()
            folder = str(body.get("folder") or "").strip()
            if not task:
                return self._send_json(400, {"error": "say what the helper should do"})
            if not agent and not role:
                return self._send_json(400, {"error": "pick a helper"})
            if folder:
                folder = os.path.abspath(os.path.expanduser(folder))
                if not os.path.isdir(folder):
                    return self._send_json(400, {"error": f"folder not found: {folder}"})
            from . import subagents as _sa, external_agents as _xa
            if agent:
                r = _xa.spawn(agent, task, cwd=folder, by="you")
            else:
                # Where one of Hearth's own helpers thinks: the main brain (none
                # given), this PC, or a saved provider with one of its models.
                route, err = _sa.route_for(str(body.get("on") or ""), str(body.get("model") or ""))
                if err:
                    return self._send_json(400, {"error": err})
                prompt = task + (f"\n\nWork in this folder: {folder}" if folder else "")
                r = _sa.spawn_subagent(role, prompt, mode="background", by="you", route=route)
            if not r.get("ok"):
                return self._send_json(400, {"error": r.get("error") or "it didn't start"})
            return self._send_json(200, {"ok": True, "agent_id": r.get("agent_id")})
        if path == "/api/onboarding":
            # The same apply the terminal's first-run setup uses.
            from . import onboarding
            try:
                saved = onboarding.apply(self._read_json() or {})
            except Exception as e:
                return self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
            return self._send_json(200, {"ok": True, "settings": saved})
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
            _env.set("TTS_DEVICE", tts_dev)
            _env.set("STT_DEVICE", stt_dev)
            _env.set("STT_MODEL", stt_mdl)
            _env.set("STT_LANGUAGE", settings.get("stt_language", ""))
            _env.set("VOICE", settings.get("voice_name", "am_michael"))
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
                    # Report the ACTUAL device, not the requested one — CUDA
                    # may have silently fallen back to CPU on AMD/Radeon.
                    actual = getattr(_listen, "DEVICE", stt_dev)
                    load_err = getattr(_listen, "_last_load_error", None)
                    details["stt"] = f"whisper {stt_mdl} on {actual}"
                    if load_err:
                        details["stt_warning"] = load_err
                except Exception as e:
                    details["stt"] = f"{type(e).__name__}: {e}"
            # A running voice session ends; the next one loads the new model.
            if _rt_voice:
                try:
                    _rt_voice.stop_continuous()
                    details["realtime"] = "will rebuild on next start"
                except Exception as e:
                    details["realtime"] = f"{type(e).__name__}: {e}"
            return self._send_json(200, {"ok": True, "details": details})
        if path == "/api/voice/realtime/stop":
            # The stream that owns the session notices within a second and
            # closes with "stopped". Voice mode's call is ended by its own page.
            _wc = window_call()
            if _rt_voice and not (_wc is not None and _rt_voice.owns(_wc.lease)):
                try:
                    _rt_voice.stop_continuous()
                except Exception:
                    pass
            return self._send_json(200, {"ok": True})
        if path == "/api/focus":
            # Single-instance hook — another launch attempt asked us to surface
            # the existing window instead of spawning a duplicate. Two cases:
            #  - Window OPEN (in the desktop_attach subprocess): flag it, its
            #    /api/state poll raises its own native window (the reliable path;
            #    the backend can't cross the process boundary itself).
            #  - Window CLOSED to the tray (subprocess dead → nobody polls the
            #    flag): ask the tray to SPAWN a fresh window. Without this the
            #    focus request silently dropped — the reported "clicking the exe
            #    does nothing while it's in the tray" bug.
            global _pending_focus
            _pending_focus = True
            _surface_window()
            return self._send_json(200, _focus_window())
        if path == "/api/window/chat":
            # The tray menu picked a chat (or New chat) while the window is open:
            # its /api/state poll opens it and raises the window.
            global _pending_open_chat
            _pending_open_chat = str((self._read_json() or {}).get("id") or "new")[:200]
            _focus_window()
            return self._send_json(200, {"ok": True})
        if path == "/api/voice/open":
            # Wake word / tray Voice-mode fired while a window was already open.
            # The tray can't spawn a second ?voice=1 window, so it asks us to flip
            # the open GUI into voice mode: set the one-shot flag (the next
            # /api/state poll picks it up + enters voice mode) and best-effort
            # surface the window.
            global _pending_voice_open
            _pending_voice_open = True
            _focus_window()
            return self._send_json(200, {"ok": True})
        if path == "/api/open-rules":
            rules_path = os.path.join(WORKSPACE, "rules.md")
            try:
                if not os.path.exists(rules_path):
                    with open(rules_path, "w", encoding="utf-8") as f:
                        f.write(
                            "# Your house rules for Jarvis\n\n"
                            "This file is re-read every turn. Add anything the assistant should always do\n"
                            "or never do.\n"
                        )
                from .tools import open_outside
                open_outside(rules_path)
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
            reason = (body.get("reason") or "").strip()[:500]
            if dec not in ("allow", "deny", "always", "never", "aside"):
                return self._send_json(400, {"error": "decision must be allow/deny/always/never"})
            if _answer_voice_ask(rid, dec):
                return self._send_json(200, {"ok": True, "voice": True})
            ok = _resolve_permission(rid, dec, reason)
            if not ok:
                # The live request is gone (it timed out, or a barge-in / new
                # message ended the turn before you clicked). "always"/"never"
                # are durable preferences, so still honour them so the click
                # isn't wasted, and report 'stale' instead of a scary 404. The
                # tool name is encoded in the id: perm_<ts>_<name>.
                nm = rid.split("_", 2)[2] if rid.count("_") >= 2 else ""
                if nm and dec == "always":
                    _always_allow.add(nm); _always_deny.discard(nm); _save_perms_to_disk()
                elif nm and dec == "never":
                    _always_deny.add(nm); _always_allow.discard(nm); _save_perms_to_disk()
                return self._send_json(200, {"ok": True, "stale": True})
            return self._send_json(200, {"ok": True})
        if path == "/api/permission/clear":
            # Reset always_allow / always_deny — also wipe disk so they don't come
            # back on restart. With {"name": "<tool>"} remove just that one rule
            # (so a user can un-set a single "always", not only nuke them all).
            # With {"name": "<tool>", "decision": "always"|"never"} set one
            # directly from the permissions panel.
            body = self._read_json() or {}
            nm = (body.get("name") or "").strip()
            dec = (body.get("decision") or "").strip()
            if nm:
                _always_allow.discard(nm)
                _always_deny.discard(nm)
                if dec == "always": _always_allow.add(nm)
                elif dec == "never": _always_deny.add(nm)
            else:
                _always_allow.clear()
                _always_deny.clear()
            _save_perms_to_disk()
            return self._send_json(200, {"ok": True,
                "always_allow": sorted(_always_allow), "always_deny": sorted(_always_deny)})
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
        if path == "/api/llm-endpoint/test":
            # Check a key BEFORE it is saved. Without this a typo is accepted
            # silently and every later message fails with an auth error that
            # points at nothing, which is a miserable first run.
            #
            # Listing models is not enough on its own: some providers serve their
            # catalogue publicly, so a wrong key still returns a full list. The
            # list only proves the address is right. Auth is proven by asking for
            # a single token, which costs effectively nothing.
            b = self._read_json()
            _u = (b.get("url") or "").strip().rstrip("/")
            _k = (b.get("key") or "").strip()
            _m = (b.get("model") or "").strip()
            if not _u:
                return self._send_json(400, {"ok": False, "error": "url required"})
            import urllib.request as _ur
            _hdr = {"Authorization": f"Bearer {_k}"} if _k else {}
            _ids = []
            try:
                from .headless import model_list_req
                _rq = model_list_req(_u, _k)
                with _ur.urlopen(_rq, timeout=12) as _r:
                    _d = json.loads(_r.read().decode("utf-8", "replace"))
                _ids = [m.get("id") for m in (_d.get("data") or []) if m.get("id")]
            except urllib.error.HTTPError as _he:
                if _he.code in (401, 403):
                    return self._send_json(200, {"ok": False, "status": _he.code,
                                                 "error": "the provider rejected this key"})
                if _he.code == 404:
                    return self._send_json(200, {"ok": False, "status": 404,
                                                 "error": "reached the server but found no "
                                                          "model list there, check the address"})
            except Exception as _e:
                return self._send_json(200, {
                    "ok": False,
                    "error": f"could not reach {_u} ({type(_e).__name__}), check the "
                             f"address and your connection"})
            # Address is good. Now prove the key by asking for one token.
            _probe_model = _m or (_ids[0] if _ids else "")
            if not _probe_model:
                return self._send_json(200, {"ok": True, "count": len(_ids), "models": _ids[:200],
                                             "note": "address reachable; no model to test auth with"})
            try:
                _body = json.dumps({"model": _probe_model, "max_tokens": 1,
                                    "messages": [{"role": "user", "content": "hi"}]}).encode()
                _rq2 = _ur.Request(_u + "/chat/completions", data=_body, method="POST",
                                   headers={**_hdr, "Content-Type": "application/json"})
                with _ur.urlopen(_rq2, timeout=25):
                    pass
                return self._send_json(200, {"ok": True, "count": len(_ids),
                                             "models": _ids[:200], "verified": True})
            except urllib.error.HTTPError as _he2:
                if _he2.code in (401, 403):
                    return self._send_json(200, {"ok": False, "status": _he2.code,
                                                 "error": "the provider rejected this key"})
                if _he2.code == 402:
                    return self._send_json(200, {"ok": False, "status": 402,
                                                 "error": "the key is valid but the account has "
                                                          "no credit"})
                if _he2.code == 404:
                    return self._send_json(200, {"ok": False, "status": 404,
                                                 "error": f"no model named {_probe_model} on this "
                                                          f"provider"})
                if _he2.code == 429:
                    return self._send_json(200, {"ok": True, "count": len(_ids), "models": _ids[:200],
                                                 "note": "rate limited while testing, the key "
                                                         "itself was accepted"})
                return self._send_json(200, {"ok": False, "status": _he2.code,
                                             "error": f"the provider answered {_he2.code}"})
            except Exception as _e2:
                return self._send_json(200, {"ok": False,
                                             "error": f"could not complete the check "
                                                      f"({type(_e2).__name__})"})
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
            from . import claude_brain
            if provider.lower() in claude_brain.BRAINS:
                # An agent program as the brain is not an endpoint. The local
                # address stays as it is, because helpers and background memory
                # keep using the local model, and so does whatever is loaded there.
                err = claude_brain.set_brain(provider.lower(), model)
                if err:
                    return self._send_json(400, {"ok": False, "error": err})
                return self._send_json(200, {"ok": True, "provider": provider.lower()})
            if not url:
                return self._send_json(400, {"ok": False, "error": "url required"})
            # Reconcile provider with the URL. A cloud URL must carry its cloud
            # provider - never "local". Otherwise the saved state drifts into
            # provider=local + url=api.x.ai, which makes Hearth treat a cloud
            # endpoint as a local server (chats silently hit the cloud, the
            # eject button shows for a "loaded" cloud model, etc.).
            _detected = _hl.provider_for(url)   # "" for an unknown host: the caller's label stands
            if _detected == "local" and provider and provider != "local":
                # a local URL with a cloud provider label is also inconsistent
                provider = "local"
            elif _detected and _detected != "local" and (not provider or provider == "local"):
                provider = _detected
            LOCAL_API_BASE = url
            _hl.LOCAL_API_BASE = url
            if key:
                _hl.LOCAL_API_KEY = key
            else:
                # No key supplied: keep harmless default so local servers still work
                _hl.LOCAL_API_KEY = "hearth-builtin"
            os.environ["LOCAL_API_BASE"] = url
            if key:
                os.environ["LOCAL_API_KEY"] = key
            else:
                # When switching to cloud / non-builtin, drop the old builtin
                # key from env so polls don't keep authenticating against an
                # unrelated server.
                os.environ.pop("LOCAL_API_KEY", None)
            # A cloud model name is checked against what that provider lists:
            # a default that has since been retired would fail on every message.
            # Only a name Hearth suggested (or none at all): one the person
            # typed is theirs, listed or not.
            if (body.get("settle") or not model) and provider not in ("", "local", "lmstudio", "builtin"):
                model = _hl.settle_model(url, key or _provider_key(provider), model) or model
            if model:
                os.environ["LOCAL_MODEL"] = model
            else:
                os.environ.pop("LOCAL_MODEL", None)
            # Persist the user's choice so Settings + the Models tab pill
            # both show the right thing on restart. Without this the pill
            # said "Built-in server" even after the user switched to Grok.
            try:
                _saved = _load_settings()
                # Per-provider key vault so switching brains never destroys a
                # key. Before this, moving to a keyless local model overwrote
                # llm_key with "" and the user's Grok/OpenAI key was gone — they
                # had to re-paste it every time they came back. Now each
                # provider's key is remembered under provider_keys[<provider>].
                _vault = dict(_saved.get("provider_keys") or {})
                _old_provider = (_saved.get("llm_provider") or "").strip().lower()
                _old_key = (_saved.get("llm_key") or "").strip()
                # Only a provider's own key is filed under its name: a local
                # server or an agent program has none, and what sat in llm_key
                # while one was in use belongs to the provider before it.
                _keyed = set(_hl.PROVIDERS) | {"custom"}
                if _old_provider in _keyed and _old_key:
                    _vault[_old_provider] = _old_key  # bank the outgoing key
                _new_provider = (provider or _saved.get("llm_provider", "")).strip().lower()
                # Use the supplied key, else recover this provider's banked key.
                _eff_key = key or _vault.get(_new_provider, "") or _provider_key(_new_provider)
                if _new_provider in _keyed and _eff_key:
                    _vault[_new_provider] = _eff_key
                _saved["provider_keys"] = _vault
                _saved["llm_provider"] = provider or _saved.get("llm_provider", "")
                _saved["llm_url"] = url
                _saved["llm_key"] = _eff_key
                # A .gguf is a LOCAL model FILE, never a valid cloud model id — if it
                # carried over from the previous local brain, saving it makes the
                # sticker show a stale "gemma.gguf ● active" on a cloud endpoint (and
                # a dead eject). Drop it so the sticker asks the user to pick a model.
                _model_save = model
                if provider and provider != "local" and _model_save.lower().endswith(".gguf"):
                    _model_save = ""
                _saved["llm_model"] = _model_save
                # If we recovered a banked key (none was supplied this switch),
                # push it to the live runtime so the very next chat authenticates
                # instead of using the "hearth-builtin" placeholder set above.
                if _eff_key and not key:
                    _hl.LOCAL_API_KEY = _eff_key
                    os.environ["LOCAL_API_KEY"] = _eff_key
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
                        # The model takes seconds to let go of the card; the
                        # new brain doesn't need to wait for that.
                        threading.Thread(target=llmserver.stop_builtin, daemon=True,
                                         name="unload-for-switch").start()
                        stopped_builtin = True
                except Exception:
                    pass
            _models_cache["ts"] = 0  # force the next /api/models to repopulate
            # Bust the llmserver status cache too — a brain switch changes the
            # authoritative active endpoint/provider, and we want the very next
            # /api/llmserver/status + /api/state poll to reflect it instead of
            # serving a snapshot from before the switch (up to 8s stale).
            try:
                from . import llmserver as _ls_inval
                _ls_inval.invalidate_status_cache()
            except Exception:
                pass
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
                    import urllib.parse as _up
                    _host = (_up.urlparse(url).hostname or "").lower()
                    _is_localhost = (
                        _host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
                        or _host.startswith("192.168.")
                        or _host.startswith("10.")
                        or _host.endswith(".local")
                    )
                    if not _is_localhost:
                        # provider=local but the URL points at a public host
                        # (e.g. a cloud URL left over from a previous provider).
                        # It may well answer /v1/models, but it is NOT a local
                        # server - never call it LM Studio.
                        local_probe = {"reachable": True, "kind": "remote"}
                    elif _ls.ours_at(url):
                        # Hearth's own server, from this window or the terminal.
                        local_probe = {"reachable": True, "kind": "builtin"}
                    elif _ls.external_server_running(url, timeout=1.0, api_key=key or None):
                        local_probe = {"reachable": True, "kind": "other"}
                    else:
                        local_probe = {"reachable": False, "kind": "none"}
                except Exception:
                    pass
            # Running bridges read the saved brain before each message
            # (channel_core.follow_saved_brain), so they switch with it from the
            # next one; restarting them dropped any reply in progress, and a
            # switch made from the phone took down the bridge answering it.
            restarted_bridges = [_ch for _ch in list(_BRIDGE_PROCS.keys()) if _bridge_running(_ch)]
            return self._send_json(200, {
                "ok": True, "url": url, "provider": provider,
                "stopped_builtin": stopped_builtin,
                "local_probe": local_probe,
                "restarted_bridges": restarted_bridges,
                "model": model,              # what is in use, which may not be the name sent
            })
        if path == "/api/llmserver/start":
            # Boot the optional built-in llama-cpp-python server with the user's
            # picked model file + load config. The GUI surfaces every llama.cpp
            # knob (n_gpu_layers, ctx, KV cache quant, threads, flash attn)
            # — see the Models tab inline expansion.
            return self._send_json(200, load_local_brain("", self._read_json()))
        if path == "/api/llmserver/helper":
            # A second, smaller model for helpers and sub-agents. An empty
            # model_path stops it and forgets the choice.
            from . import llmserver
            body = self._read_json()
            mp = (body.get("model_path") or "").strip()
            if not mp:
                _save_settings({"helper_model": ""})
                return self._send_json(200, llmserver.stop_helper())
            ctx = int(body.get("ctx") or 16384)
            r = llmserver.start_helper(mp, ctx=ctx)
            if r.get("ok"):
                _save_settings({"helper_model": mp, "helper_ctx": ctx})
                llmserver.invalidate_status_cache()
            return self._send_json(200, r)
        if path == "/api/llmserver/stop":
            from . import llmserver
            stop_result = llmserver.stop_builtin()
            # Revert endpoint to whatever was configured before (LM Studio
            # default) so subsequent chats don't try to hit a dead URL.
            from . import headless as _hl
            settings = _load_settings()
            saved_url = (settings.get("llm_url") or "").strip()
            new_base = saved_url or _local_base()
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
            category = str(body.get("category") or "").strip()
            if category:
                # An image or video model file goes in the image engine's folder for its kind.
                from . import sdserver
                if category == "auto":
                    category = sdserver.folder_for(os.path.basename(filename))
                if category not in sdserver.CATEGORIES or category == "recipes":
                    return self._stream_model_download("", "", error=f"no such model folder '{category}'")
                return self._stream_model_download(repo, filename, dest_dir=str(sdserver.models_dir() / category),
                                                   save_as=os.path.basename(filename))
            return self._stream_model_download(repo, filename)
        if path == "/api/llmserver/vision-download":
            # A model's vision file, streamed like any model download, saved beside it.
            from . import llmserver
            mp = (self._read_json().get("path") or "").strip()
            v = llmserver.vision_for_file(mp) if mp and os.path.isfile(mp) else {}
            if v.get("state") != "needs_file":
                return self._stream_model_download("", "", error="this model has no vision file to fetch")
            return self._stream_model_download(v["repo"], v["file"], dest_dir=v["folder"], save_as=v["save_as"],
                                               vision_for=mp)
        if path == "/api/llmserver/download-pause":
            from . import llmserver
            body = self._read_json()
            return self._send_json(200, {"ok": llmserver.pause_model_download(
                (body.get("filename") or "").strip())})
        if path == "/api/images/install":
            # The image engine, fetched for this system and card, as NDJSON progress.
            from . import sdserver
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            _seen = [0.0]

            def _emit(obj):
                try:
                    self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _prog(done, total):
                if time.time() - _seen[0] >= 0.2 or done == total:
                    _seen[0] = time.time()
                    _emit({"type": "progress", "done": done, "total": total})
            try:
                _emit({"type": "done", **sdserver.install(on_progress=_prog)})
            except Exception as e:
                _emit({"type": "done", "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/images/open-folder":
            from . import sdserver
            from .tools import open_outside
            open_outside(str(sdserver.models_dir()))
            return self._send_json(200, {"ok": True})
        if path == "/api/images/cancel":
            from . import sdserver
            return self._send_json(200, sdserver.cancel_install())
        if path == "/api/images/folder":
            from . import sdserver
            body = self._read_json()
            return self._send_json(200, sdserver.set_folder(str(body.get("path") or ""), add=not body.get("remove")))
        if path == "/api/images/load":
            from . import sdserver
            body = self._read_json()
            rid = str(body.get("recipe") or "")
            if not rid:
                return self._send_json(200, {"ok": True, **sdserver.api("POST", "/sdcpp/v1/models/unload")})
            return self._send_json(200, sdserver.ensure_loaded(bool(body.get("video")), rid))
        if path == "/api/images/delete":
            from . import sdserver
            body = self._read_json()
            return self._send_json(200, sdserver.delete_model(str(body.get("recipe") or "")))
        if path == "/api/images/identify":
            from . import sdserver
            body = self._read_json()
            return self._send_json(200, sdserver.identify(str(body.get("file") or "")))
        if path == "/api/runtime/install":
            # Download Hearth's own llama.cpp runtime (the native llama-server.exe
            # + CUDA DLLs). Streams NDJSON progress like the model download. The
            # GUI shows the AV-false-positive heads-up BEFORE calling this.
            from . import llmserver
            # Optional {tag} pins a specific build so a regressed one can be
            # rolled back; absent means latest.
            _body = self._read_json()
            _want_tag = (_body.get("tag") or "").strip()
            _repo = str(_body.get("repo") or "").strip()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            _last = [0.0]

            def emit(obj: Dict[str, Any]) -> None:
                try:
                    self.wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def on_progress(done: int, total: int) -> None:
                now = time.time()
                if now - _last[0] < 0.2 and done != total:
                    return
                _last[0] = now
                emit({"type": "progress", "done": done, "total": total})

            try:
                result = llmserver.download_llama_runtime(
                    tag=_want_tag or None, on_progress=on_progress, repo=_repo)
                emit({"type": "done", **result})
            except Exception as e:
                emit({"type": "done", "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/llmserver/discard-partial":
            from . import llmserver
            body = self._read_json()
            return self._send_json(200, llmserver.discard_partial(
                (body.get("filename") or "").strip()))
        if path == "/api/llmserver/delete-model":
            from . import llmserver
            body = self._read_json()
            return self._send_json(200, llmserver.delete_model(
                (body.get("path") or "").strip()))
        if path == "/api/runtime/cancel":
            from . import llmserver
            return self._send_json(200, llmserver.cancel_llama_download())
        if path == "/api/runtime/discard":
            # Cancel the engine download AND drop its partial (does not resume).
            from . import llmserver
            return self._send_json(200, llmserver.discard_llama_download())
        if path == "/api/runtime/add-folder":
            from . import llmserver
            return self._send_json(200, llmserver.add_engine_folder(str(self._read_json().get("path") or "")))
        if path == "/api/runtime/remove-added":
            from . import llmserver
            return self._send_json(200, llmserver.remove_added_engine(str(self._read_json().get("id") or "")))
        if path == "/api/runtime/preference":
            from . import llmserver
            body = self._read_json()
            return self._send_json(200, llmserver.set_engine_preference(
                (body.get("preference") or "").strip()))
        return self._not_found()

    def _not_found(self):
        """Answer an unknown path. Every caller of these is the window, which
        reads JSON, so an HTML error page reached it as a parse failure with no
        status in it and the real problem was invisible."""
        return self._send_json(404, {"error": f"no endpoint at {self.path}"})

    # -------- handlers --------

    def _send_state(self) -> None:
        loaded = _detect_loaded()
        # ONE coherent truth about the active brain. endpoint/provider/context
        # are all derived from the SAME live state the chat path uses, so the
        # frontend never sees /api/state disagree with /api/models or
        # /api/llmserver/status.
        endpoint = _active_base()
        settings = _load_settings()
        provider = (settings.get("llm_provider") or "").strip().lower() or "local"
        # Active context window for the ring. Priority:
        #   1) builtin's REAL launched n_ctx (authoritative — it's what the
        #      server was actually started with, set in _proc_info by
        #      start_builtin), so the ring matches the running server even if
        #      the saved server_default_ctx differs.
        #   2) resolve_context_tokens() — LM Studio loaded_context_length or
        #      the cloud model's window (same helper /api/context-budget uses,
        #      so the two endpoints agree).
        context_window = None
        context_source = None
        try:
            from . import llmserver as _ls
            if (_ls._proc is not None and _ls._proc.poll() is None
                    and (_ls._proc_info or {}).get("url") == endpoint):
                context_window = (_ls._proc_info or {}).get("ctx")
                context_source = "builtin-n_ctx"
        except Exception:
            pass
        if not context_window:
            try:
                from . import headless as _hl
                model_id = (loaded or {}).get("id") or (settings.get("llm_model") or "")
                context_window, context_source = _hl.resolve_context_tokens(model_id)
            except Exception:
                context_window, context_source = None, None
        try:
            from . import llmserver as _lsm
            _models_dir = _lsm.get_models_dir()
        except Exception:
            _models_dir = ""
        self._send_json(200, {
            "model": loaded,
            "endpoint": endpoint,
            "provider": provider,
            # The model name saved for the brain, so a window holding an older
            # copy of the settings shows the one that is really used.
            "picked_model": (settings.get("llm_model") or ""),
            # Active context window so the GUI ring is accurate the instant a
            # brain/model swap lands — no waiting for the next chat turn's SSE.
            "context": context_window,
            "context_source": context_source,
            "tools": len(TOOL_DEFINITIONS),
            # One-shot: wake fired while a window was already open. The GUI enters
            # voice mode in place instead of the tray spawning a ?voice=1 window.
            "enter_voice": _take_pending_voice_open(),
            "open_chat": _take_pending_open_chat(),
            # One-shot: a second launch asked us to surface. The open window raises
            # itself through its own process (voice's reliable foreground path).
            "surface": _take_pending_focus(),
            "memories": len(_memory_index()),
            # Facts the background pass saved lately, which the window shows.
            "facts_saved": _facts_noticed(),
            # The model on this PC that stays loaded beside a cloud brain and
            # runs the helpers, for the sticker.
            "local_kept": _local_kept(),
            # A turn running in the terminal, so the room shows JARVIS at it.
            "elsewhere": __import__("hearth.activity", fromlist=["current"]).current(not_from="window"),
            "workspace": WORKSPACE,
            "models_dir": _models_dir,
            "lms_cli": bool(_find_lms_cli()),
            # For the "Hearth as MCP server" snippet — the real interpreter +
            # full script path so the config doesn't rely on bare `python` being
            # on PATH or on `-m` resolving the package from some unknown cwd.
            "python_exe": sys.executable,
            "mcp_server_path": os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py"),
            "frozen": bool(getattr(sys, "frozen", False)),
            # Ready-to-paste launcher for "Hearth as MCP server". Computed here
            # because only the backend knows how it's running:
            #  - packaged (frozen): sys.executable is Hearth.exe, so route the
            #    module through the --hearth-run-python sentinel the exe handles.
            #  - from source / editable: sys.executable is the venv python, so
            #    hand it the ABSOLUTE mcp_server.py path (mcp_server.py adds its
            #    own parent to sys.path, so it imports `hearth` from any cwd).
            #    `python -m hearth.mcp_server` was wrong: it needs the repo as
            #    cwd, which the launching client does not provide.
            "mcp_launch": (
                {"command": sys.executable,
                 "args": ["--hearth-run-python", "-m", "hearth.mcp_server"]}
                if getattr(sys, "frozen", False) else
                {"command": sys.executable,
                 "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "mcp_server.py")]}
            ),
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
        # memory_forget takes `title`; this sent `name`, the argument check refused it,
        # and the window said "Deleted" for a memory that stayed. Its answer is read now.
        try:
            out = str(execute_tool("memory_forget", {"title": name}) or "")
        except Exception as e:
            return self._send_json(500, {"ok": False, "error": str(e)})
        if out.lstrip().lower().startswith(("error", "no memory found")):
            return self._send_json(200, {"ok": False, "error": out.split(":", 1)[-1].strip() if ":" in out else out})
        self._send_json(200, {"ok": True, "name": name})

    def _reveal_in_folder(self) -> None:
        """Open the OS file explorer with the given path selected. Reveal follows
        the READ policy, not the write policy: the Files view browses the whole
        disk, so revealing what you're already looking at must work anywhere. Only
        HEARTH_LOCKDOWN (which also confines reads) restricts it to the workspace.
        Cross-site POSTs are already blocked by the Host guard, and reveal just
        opens Explorer at an existing path (no data access). Non-blocking spawn."""
        from .tools import list_extra_workspaces, read_locked, path_within
        body = self._read_json()
        path = (body.get("path") or "").strip()
        if not path:
            return self._send_json(400, {"error": "missing path"})
        try:
            p = os.path.expanduser(path)
            # The Files tab names things from the workspace down.
            real = os.path.realpath(p if os.path.isabs(p) else os.path.join(WORKSPACE, p))
        except Exception:
            return self._send_json(400, {"error": "bad path"})
        if not os.path.exists(real):
            return self._send_json(404, {"error": "not found"})
        if read_locked():
            if not any(path_within(real, root)
                       for root in [WORKSPACE] + list(list_extra_workspaces())):
                return self._send_json(403, {"error": "path outside allowed roots (lockdown)"})
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

    def _reveal_model_path(self) -> None:
        """Open the OS file explorer with a MODEL file selected. NOT
        workspace-sandboxed (models live in LM Studio / HF-cache / ~/.hearth
        dirs), so it only accepts an existing .gguf file or a directory — a
        reveal just opens Explorer, and restricting it to real model paths keeps
        it from being a general 'open any path' primitive."""
        body = self._read_json()
        path = (body.get("path") or "").strip()
        if not path:
            return self._send_json(400, {"error": "missing path"})
        try:
            real = os.path.realpath(path)
        except Exception:
            return self._send_json(400, {"error": "bad path"})
        if not os.path.exists(real):
            return self._send_json(404, {"error": "not found"})
        # Only ever a .gguf model FILE — never an arbitrary directory. A model
        # reveal always targets a specific file, and allowing any dir would let a
        # localhost POST pop Explorer anywhere on disk.
        if not (os.path.isfile(real) and real.lower().endswith(".gguf")):
            return self._send_json(403, {"error": "not a model file"})
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", real],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", real])
            else:
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
        current_name = (_load_settings().get("agent_name") or __import__("hearth").default_agent_name()).strip() or __import__("hearth").default_agent_name()
        if new_name == current_name:
            return self._send_json(200, {"ok": True, "noop": True, "name": new_name})
        # Compute target workspace. Use the PARENT of current WORKSPACE so a
        # user who already set $HEARTH_WORKSPACE=D:\Stuff\Jarvis gets the
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
            # relaunches the tray with HEARTH_WORKSPACE pointing at the
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
                "env['HEARTH_WORKSPACE'] = new\n"
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

    def _relocate_workspace(self) -> None:
        """Move the whole Hearth workspace to a user-chosen path (e.g. another
        drive). Same move-and-restart dance as _rename_agent, but to an
        arbitrary location AND it writes the ~/.hearth pointer so the new
        location persists across cold launches (not just this respawn)."""
        body = self._read_json()
        raw = (body.get("path") or "").strip().strip('"').strip("'")
        if not raw:
            return self._send_json(400, {"error": "missing path"})
        cur_ws = os.path.realpath(WORKSPACE)
        new_ws = os.path.abspath(os.path.expanduser(raw))
        if os.path.realpath(new_ws) == cur_ws:
            return self._send_json(400, {"error": "that's already where Inglen lives"})
        # Moving INTO the target: it must not be an existing non-empty folder.
        if os.path.isdir(new_ws) and os.listdir(new_ws):
            return self._send_json(409, {"error": f"target exists and isn't empty: {new_ws}"})
        parent = os.path.dirname(new_ws.rstrip("\\/")) or new_ws
        if not os.path.isdir(parent):
            return self._send_json(400, {"error": f"parent folder doesn't exist: {parent}"})
        # Free the builtin server's file handles in WORKSPACE so the move works.
        try:
            from . import llmserver as _ls
            if _ls._proc is not None and _ls._proc.poll() is None:
                _ls.stop_builtin()
        except Exception:
            pass
        pointer = os.path.join(os.path.expanduser("~"), ".hearth", "workspace.txt")

        def _do_relocate():
            import time as _t
            _t.sleep(1.5)  # let the HTTP response reach the browser
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
                f"pointer={pointer!r}\n"
                f"pid={our_pid}\n"
                "for _ in range(200):\n"
                "    try:\n"
                "        if sys.platform == 'win32':\n"
                "            import ctypes\n"
                "            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)\n"
                "            if not h: break\n"
                "            ctypes.windll.kernel32.CloseHandle(h)\n"
                "        else:\n"
                "            os.kill(pid, 0)\n"
                "    except Exception:\n"
                "        break\n"
                "    time.sleep(0.1)\n"
                "time.sleep(0.5)\n"
                "try:\n"
                "    os.makedirs(os.path.dirname(new) or new, exist_ok=True)\n"
                "    try:\n"
                "        os.rename(old, new)\n"
                "    except OSError:\n"
                "        shutil.copytree(old, new); shutil.rmtree(old, ignore_errors=True)\n"
                "    os.makedirs(os.path.dirname(pointer), exist_ok=True)\n"
                "    open(pointer, 'w', encoding='utf-8').write(new)\n"
                "except Exception as e:\n"
                "    print('relocate failed:', e); sys.exit(1)\n"
                "env = os.environ.copy()\n"
                "env['HEARTH_WORKSPACE'] = new\n"
                "flags = 0\n"
                "if sys.platform == 'win32':\n"
                "    flags = 0x08000000 | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008\n"
                f"subprocess.Popen([{py!r}, '-m', 'hearth.tray', '--open'],\n"
                "                 env=env, creationflags=flags, close_fds=True)\n"
            )
            try:
                flags = 0
                if sys.platform == "win32":
                    flags = (0x08000000
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                             | 0x00000008)
                subprocess.Popen([py, "-c", helper],
                                 creationflags=flags, close_fds=True)
            except Exception:
                pass
            _t.sleep(0.3)
            os._exit(0)

        threading.Thread(target=_do_relocate, daemon=True).start()
        return self._send_json(200, {
            "ok": True,
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
        q = self._query()
        path = q.get("path", "")
        if not path:
            return self._send_json(400, {"error": "missing path"})
        real, why = _workspace_file(path)
        if why:
            return self._send_json(403 if why == "outside" else 404 if why == "missing" else 400, {"error": why})
        ctype, _ = mimetypes.guess_type(real)
        ctype = ctype or "application/octet-stream"
        try:
            size = os.path.getsize(real)
            with open(real, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "public, max-age=3600")
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
        up_dir = os.path.join(WORKSPACE, "uploads")
        os.makedirs(up_dir, exist_ok=True)
        # Collision-safe: two files named the same (e.g. screenshot.png) must not
        # silently overwrite each other — the first's bytes were being lost. Keep
        # the clean name when free; else suffix -2, -3, … so both survive.
        stem, ext = os.path.splitext(name)
        dest = os.path.join(up_dir, name)
        _n = 2
        while os.path.exists(dest):
            dest = os.path.join(up_dir, f"{stem}-{_n}{ext}")
            _n += 1
        with open(dest, "wb") as f:
            f.write(blob)
        # Pre-extract text so the CONTENT can ride the prompt directly instead of
        # only "here's a path, please call read_file" — a weak local model often
        # ignores that hint and the attach looks like it did nothing. Images/video
        # skip this (they need a vision model via view_image, not text). Capped +
        # size-guarded so a huge upload doesn't block the response.
        _ext = os.path.splitext(dest)[1].lower()
        _IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".ico"}
        _MEDIA = _IMG | {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav",
                         ".flac", ".ogg", ".m4a", ".zip", ".7z", ".rar", ".exe", ".dll"}
        preview = ""
        if _ext not in _MEDIA and len(blob) <= 10 * 1024 * 1024:
            try:
                _txt = execute_tool("read_file", {"path": dest}) or ""
                if isinstance(_txt, str) and _txt.strip() and not _txt.lstrip().startswith("Error"):
                    preview = _txt[:6000]
            except Exception:
                preview = ""
        self._send_json(200, {"ok": True, "path": dest, "size": len(blob),
                              "is_image": _ext in _IMG, "preview": preview})

    def _stream_chat(self) -> None:
        body = self._read_json()
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return self._send_json(400, {"error": "empty prompt"})
        # A new user turn = TTS for this reply is allowed again. A barge-in on
        # the PREVIOUS turn set voice._abort (to cut that reply's audio), and it
        # is only cleared here — otherwise every reply after a barge-in was
        # silently dropped by speak() and voice went permanently mute.
        try:
            from . import voice as _v
            _v.reset_abort()
        except Exception:
            pass
        think = bool(body.get("think"))
        # Reasoning effort level when think is on (low/medium/high). From the
        # request, else the saved setting, else the model's own default.
        think_level = (body.get("think_level")
                       or (_load_settings().get("think_level") or "")).strip().lower()
        model = body.get("model") or None
        history = body.get("history") or []
        voice = bool(body.get("voice"))  # voice-mode turn → spoken-register prompt
        chat_id = str(body.get("chat") or "")[:80] or None   # which chat this turn is in
        # First message of a chat: drop deferred tools the PREVIOUS conversation
        # unlocked. They're process-global, so without this a fresh chat starts
        # carrying whatever the last one happened to open.
        if not history:
            try:
                from .tools import reset_unlocked_tools
                reset_unlocked_tools()
            except Exception:
                pass
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
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        _live_turn = secrets.token_hex(5)
        live_bus.publish(live_bus.turn_event(chat_id, "window", _live_turn, "start", {"text": prompt}) or {})
        _spoken: Dict[str, Any] = {"turn": None}     # a voice-mode turn: its reply is said in the window's call

        from .waited import Waited
        _waited = Waited()          # time spent on the person's OK or answer: not part of the turn's duration

        def emit(event_type: str, **fields: Any) -> None:
            fields = _waited.fix(event_type, fields)
            _turn_saw(event_type, fields)
            if _spoken["turn"] is not None:
                _window_turn_event(_spoken["turn"], event_type, fields)
            try:                                 # the phone and other windows see it as it's written
                ev = live_bus.turn_event(chat_id, "window", _live_turn, event_type, fields)
                if ev:
                    live_bus.publish(ev)
            except Exception:
                pass
            line = json.dumps({"type": event_type, **fields}, ensure_ascii=False, default=str) + "\n"
            try:
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        _TURN_NOW.update(state="thinking", waiting="", at=time.time())
        _CANCEL.clear()  # fresh generation — clear any prior stop request
        with _STEER_LOCK:
            _STEER_Q.clear()  # drop any steer left over from a prior turn
        # Wire the GUI as the ask_user surface for the lifetime of THIS chat
        # turn — the tool dispatcher will route ask_user calls through here,
        # blocking until the user clicks an option in the modal.
        try:
            from .tools import set_ask_user_callback
            set_ask_user_callback(_waited.timing(_make_ask_user_bridge(emit)))
        except Exception:
            pass
        # Local endpoint with NOTHING loaded: don't let run_once hit a dead
        # endpoint and fail silently — that's the "say the wake word, overlay
        # opens, then dead air" trap. Boot the user's default model on-demand
        # (reusing the launch-time autoboot so the builtin-vs-LM-Studio matching
        # lives in one place) and stream a "warming" cue so the HUD shows the
        # model coming up instead of going quiet.
        try:
            from . import claude_brain as _cb
            _claude = _cb.active()
        except Exception:
            _claude = False
        if _is_local_endpoint and not _claude and not _detect_loaded():
            _bs = _load_settings()
            _pref = (_bs.get("preferred_model") or "").strip()
            if _pref and _bs.get("server_autoboot", True):
                emit("model_loading", model=os.path.basename(_pref))
                try:
                    _auto_boot_preferred_model_async()
                except Exception:
                    pass
                for _ in range(80):           # up to ~40s for the server to come up
                    if _CANCEL.is_set() or _detect_loaded():
                        break
                    time.sleep(0.5)
            if not _detect_loaded():
                emit("error", message=(
                    "The model is still warming up, or none is loaded. Give it a "
                    "few seconds and say it again — or load one in the Models tab."))
                _TURN_NOW.update(state="idle", waiting="")
                return
        if voice and window_call() is not None:
            from .call import Turn
            _spoken["turn"] = Turn(window_call())
        _check = _make_permission_check(emit)
        if voice:
            _check = _voice_permission_check(_check, emit, prompt, history)
        try:
            asyncio.run(run_once(
                prompt, emit=emit, think=think, think_level=think_level, model=model, history=history,
                permission_check=_waited.timing(_check),
                should_cancel=_CANCEL.is_set, drain_steer=_drain_steer,
                voice=voice, chat_id=chat_id, prior_failure=str(body.get("prior_failure") or "")[:600],
            ))
        except Exception as e:
            emit("error", message=f"{type(e).__name__}: {e}")
        finally:
            _TURN_NOW.update(state="idle", waiting="", at=time.time())
            if _spoken["turn"] is not None:
                _spoken["turn"].end()
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
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(obj: dict) -> bool:
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        # This stream's own queue. A shared one let a stream that was still
        # closing take the next session's events, "ready" among them.
        events: "_queue_mod.Queue[dict]" = _queue_mod.Queue()
        put = events.put

        def _on_barge() -> None:
            # Cut TTS here, server-side, the instant the VAD fires; the event
            # is only for the HUD.
            try:
                if _voice:
                    _voice.stop()
            except Exception:
                pass
            put({"type": "barge"})

        _ready_fired = threading.Event()

        def _on_ready() -> None:
            _ready_fired.set()
            # A mic fallback (the picked device would not open) is known by
            # now; ship it with 'ready' so the UI can say which mic it is using.
            ev = {"type": "ready"}
            try:
                w = _rt_voice.mic_warning()
                if w:
                    ev["warning"] = w
            except Exception:
                pass
            put(ev)

        lease = None
        try:
            if _voice:
                _voice.set_level_sink(lambda v: put({"type": "level", "v": v}))
                _voice.set_mic_sink(lambda v: put({"type": "mic", "v": v}))
            # "warming" until the STT model finishes loading; the ready callback
            # flips it to listening once the recorder can actually hear.
            _detail = ""
            try:
                if not _rt_voice.model_downloaded():
                    _detail = "downloading the speech model, first time only (a minute or two)…"
            except Exception:
                pass
            emit({"type": "warming", "detail": _detail} if _detail else {"type": "warming"})
            if _detail:
                # A first run downloads the speech model; say how far along it is.
                def _dl_progress():
                    last, t_end = -1, time.time() + 1200
                    while not _ready_fired.is_set() and time.time() < t_end:
                        pct = _rt_voice.download_progress()
                        if pct is not None and pct != last:
                            last = pct
                            put({"type": "warming", "pct": pct,
                                 "detail": f"downloading the speech model, first time only · {pct}%"})
                        if pct is not None and pct >= 99:
                            put({"type": "warming", "detail": "loading the speech model…"})
                            return
                        time.sleep(0.5)
                threading.Thread(target=_dl_progress, daemon=True).start()
            lease = _rt_voice.start_continuous(
                lambda t: put({"type": "final", "text": t}),
                on_caption=lambda t: put({"type": "partial", "text": t}),
                on_barge=_on_barge, on_ready=_on_ready,
                on_error=lambda m: put({"type": "error", "message": m}),
                on_notice=lambda m: put({"type": "notice", "message": m}))
            emit({"type": "started"})
            # If the recorder never reaches ready (a component missing from a
            # packaged build, a first-run model download that stalls), say so
            # rather than sit on "warming".
            def _warm_watchdog():
                if not _ready_fired.is_set():
                    put({"type": "warn_slow", "message":
                        "Voice is still starting. First run downloads a small "
                        "speech model, which can take a minute. If it never "
                        "starts, a voice component may be missing from this "
                        "build - check hearth_window.log."})
            _wt = threading.Timer(45.0, _warm_watchdog)
            _wt.daemon = True
            _wt.start()
            # The desktop HUD hides itself while the Hearth window has focus.
            try:
                from . import voice_overlay as _vo
                _vo.start()
            except Exception:
                pass
        except Exception as e:
            emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            return

        # Drain until the client goes, or the session this stream owns ends
        # (stopped from elsewhere, or handed to a newer stream).
        last_beat = time.time()
        try:
            while True:
                try:
                    ev = events.get(timeout=1.0)
                except Exception:
                    if not _rt_voice.owns(lease):
                        emit({"type": "stopped"})
                        break
                    if time.time() - last_beat >= 15.0:
                        last_beat = time.time()
                        if not emit({"type": "heartbeat"}):
                            break
                    continue
                if not emit(ev):
                    break
                if ev.get("type") in ("stopped", "error"):
                    if ev.get("type") == "stopped" or not _rt_voice.is_listening():
                        break
        finally:
            # Only the owner tears down. A stream that closes after a newer one
            # took over must leave that session, its level meter and HUD alone.
            if _rt_voice.stop_continuous(lease):
                try:
                    if _voice:
                        _voice.set_level_sink(None)
                        _voice.set_mic_sink(None)
                    from . import voice_overlay as _vo
                    _vo.stop()
                except Exception:
                    pass

    def _mic_meter_stream(self) -> None:
        """Stream the live input level from the SELECTED mic as NDJSON so the GUI
        can bounce a meter in real time (a real 'speak and watch it move' test).
        Runs ~6s, then ends. Best-effort; a mic in use elsewhere just errors."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(obj) -> bool:
            try:
                self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        try:
            from . import listen as _listen
            # Capture runs in an isolated subprocess with its own fresh PortAudio,
            # so a mic plugged in after launch is actually seen (the main process's
            # device table is a stale startup snapshot). emit() relays each line.
            _listen.stream_mic_levels(emit, seconds=6.0)
        except Exception as e:
            emit({"error": f"{type(e).__name__}: {e}"})

    def _tts(self) -> None:
        body = self._read_json()
        text = (body.get("text") or "").strip()
        if not text:
            return self._send_json(400, {"error": "empty text"})
        if not _voice:
            return self._send_json(503, {"error": "TTS not available", "detail": {"reason": "voice module missing"}})
        # The manual Speak button sends force=true: clear any stale abort flag so
        # it ALWAYS plays. Without this, a prior stop()/barge-in left _abort set
        # and every Speak click was silently dropped (the silent-Speak bug).
        if body.get("force"):
            try:
                _voice.reset_abort()
            except Exception:
                pass
        # Speak through system speakers — non-blocking so the request returns fast.
        # _voice.speak(allow_download=True) will fetch Kokoro models on first use
        # (~100MB), so the onboarding Test now triggers download instead of 503.
        try:
            r = _voice.speak(text, blocking=False)
            if r == "no_voice":
                reason = _voice.status() if _voice else {"reason": "voice module missing"}
                return self._send_json(503, {"error": "TTS not available", "detail": reason})
            if r == "empty":
                return self._send_json(400, {"error": "empty text after filtering"})
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

        from . import chat_sync
        return self._send_json(200, {"title": chat_sync.title_for(first, title_ask)})

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
        # Stamp the edition (Full/Lite) + version into the page so the user can
        # see which build they're running (Settings panel footer).
        from .updater import HEARTH_VERSION as _ver  
        try:
            from hearth import edition as _ed
            tag = f"Inglen {_ed.label()} · v{_ver}"
        except Exception:
            tag = f"Inglen · v{_ver}"
        payload = payload.replace(b"__HEARTH_EDITION__", tag.encode("utf-8"))
        payload = payload.replace(b"/*__HEARTH_LOOK__*/{}", _saved_look().encode("utf-8"), 1)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_room_js(self, path: str = "") -> None:
        path = path or _ROOM_JS
        try:
            with open(path, "rb") as f:
                payload = f.read()
        except OSError:
            return self.send_error(404, os.path.basename(path) + " missing")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
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


def what_to_get(local: bool, voice: bool) -> Dict[str, Any]:
    """What first-run setup offers to download for the choices made: a model
    when this PC is the brain and none is on disk, the engine where the build
    has none, and the voice and speech files when talking was picked."""
    from . import llmserver
    items: List[Dict[str, Any]] = []
    if local:
        try:
            have = bool(llmserver.scan_disk_for_models() or llmserver.list_local_models())
        except Exception:
            have = True
        if not have:
            pick = llmserver.suggest_model()
            if pick:
                vram = llmserver.detect_gpu_vram_gb()
                items.append({"id": "model", "title": pick["repo"].split("/")[-1].replace("-GGUF", "").replace("-gguf", ""),
                              "sub": f"{pick['params_b']}B, the most downloaded fit for "
                                     + (f"your {vram:.0f} GB card" if vram else "this PC") + " this month",
                              "size_gb": pick.get("size_gb"), "repo": pick["repo"], "filename": pick["filename"]})
        try:
            needs_engine = llmserver._is_lite_edition() and not llmserver.find_native_llama_server()
        except Exception:
            needs_engine = False
        if needs_engine:
            items.append({"id": "engine", "title": "The engine that runs it",
                          "sub": "llama.cpp, built for the graphics card in this PC", "size_gb": None})
    if voice:
        try:
            missing_voice = bool(_voice) and not _voice.models_present()
            missing_ear = bool(_listen) and not _listen.model_present()
        except Exception:
            missing_voice = missing_ear = False
        if missing_voice or missing_ear:
            gb = 0.0
            if missing_voice:
                gb += 0.34                       # the voice model and its voices
            if missing_ear:
                try:
                    repo = "Systran/faster-whisper-" + _listen.model_size()
                    gb += sum(int(x.get("size") or 0) for x in llmserver._repo_siblings(repo)) / (1024 ** 3)
                except Exception:
                    pass
            items.append({"id": "voice", "title": f"{(_load_settings().get('agent_name') or __import__('hearth').default_agent_name())}'s voice and hearing",
                          "sub": "Speaking, and the speech model that turns what you say into text",
                          "size_gb": round(gb, 2) if gb else None})
    return {"items": items}


def _workspace_file(path: str):
    """(real path, "") for a file inside the workspace or an allowed folder,
    else ("", why). The Files tab names things from the workspace down."""
    from .tools import list_extra_workspaces
    try:
        real = os.path.realpath(path if os.path.isabs(path) else os.path.join(WORKSPACE, path))
    except Exception:
        return "", "bad path"
    from . import places as _places
    roots = [os.path.realpath(WORKSPACE)] + [os.path.realpath(p) for p in list_extra_workspaces()] \
        + [os.path.realpath(p) for p in _places.roots().values()] \
        + [os.path.realpath(t["path"]) for t in _places.touched(200)]
    if not any(real.lower().startswith(r.lower() + os.sep) or real.lower() == r.lower() for r in roots):
        return "", "outside"
    if not os.path.isfile(real):
        return "", "missing"
    return real, ""


def _start_reminder_watcher() -> None:
    """Background reminder watcher. Each fire goes through the same notifier
    as the terminal and the notify tool, so it carries Hearth's name and icon."""
    try:
        from . import reminders as _rem
    except Exception:
        return

    _rem.start_watcher(lambda title, body: _rem.desktop_notify(title, body))


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
        _hl.LOCAL_API_KEY = key or "hearth-builtin"
        os.environ["LOCAL_API_BASE"] = url
        if key:
            os.environ["LOCAL_API_KEY"] = key
        if model:
            os.environ["LOCAL_MODEL"] = model
    except Exception:
        pass


_apply_saved_llm_endpoint()


def _resume_downloads_async() -> None:
    """Pick up model downloads that were cut off by a restart or crash."""
    def _go():
        time.sleep(5)                       # let the network and the server settle
        try:
            from . import llmserver
            started = llmserver.resume_interrupted_downloads()
            if started:
                print(f"[hearth.web] resuming downloads: {', '.join(started)}", file=sys.stderr)
        except Exception as e:
            print(f"[hearth.web] download resume failed: {e}", file=sys.stderr)
    threading.Thread(target=_go, daemon=True, name="resume-downloads").start()


def _migrate_numbered_mic() -> None:
    """Older versions saved the picked mic as a device number, which points at a
    different device once anything is plugged in or out. Convert it to the mic's
    name once, while the number still means a real microphone."""
    try:
        cur = _load_settings()
        if (cur.get("voice_input_name") or "").strip():
            return
        from . import listen as _ll
        name, _ = _ll.name_for_numbered_pick(cur.get("voice_input_device", -1))
        if name:
            _save_settings({"voice_input_name": name})
            _env.set("VOICE_INPUT_NAME", name)
    except Exception:
        pass


def _auto_boot_helper_async() -> None:
    """Start the helper model the user picked, after the main model has had
    its turn at the GPU, so the main one gets first claim on VRAM."""
    def _go():
        try:
            from . import llmserver
            s = _load_settings()
            mp = (s.get("helper_model") or "").strip()
            if not mp or not os.path.isfile(mp):
                return
            deadline = time.time() + 240
            time.sleep(8)
            while time.time() < deadline and llmserver._starting_paths:
                time.sleep(2)
            r = llmserver.start_helper(mp, ctx=int(s.get("helper_ctx") or 16384))
            print(f"  [hearth.web] helper model: {'ready' if r.get('ok') else r.get('error')}", flush=True)
        except Exception as e:
            print(f"  [hearth.web] helper model skipped: {e}", flush=True)
    threading.Thread(target=_go, daemon=True, name="helper-boot").start()


def _auto_boot_preferred_model_async() -> None:
    """If the user picked a default model in Settings (`preferred_model`) and
    nothing's already serving on the configured endpoint, boot Hearth's
    built-in llama-cpp server with that model in the background.

    Without this, "default model" was a stored string nothing read on boot
    — the user had to click Use this in the Models tab every launch. Now
    Hearth honors the setting automatically. Skip with HEARTH_NO_AUTOBOOT=1.

    Match logic: the saved `preferred_model` may be the LM Studio short id
    ('harmonic-hermes-9b'), a filename ('Qwen3.5-9B-Harmonic.Q4_K_M.gguf'),
    or a full path. We try in order: exact path, exact filename, then
    case-insensitive slug-match against each disk model's filename.
    """
    if _env.get("NO_AUTOBOOT") in ("1", "true", "yes"):
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
                    # A user-pointed external server usually needs no key;
                    # clear ours so we don't 401 against it.
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
                cfg["ctx"] = int(settings.get("server_default_ctx", 24576))
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
    HEARTH_NO_ONBOARDING=1."""
    if _env.get("NO_ONBOARDING") in ("1", "true", "yes"):
        return
    def _go():
        try:
            from hearth import environment as _envmod
            # Machine memories from older versions go; the facts live in this_pc.json now.
            _envmod.drop_old_memories()
            if _envmod.read_this_pc():
                # Already onboarded: only relearn if the machine actually changed
                # (drive plugged in, GPU swap) so context stays fresh without churn.
                msg = _envmod.refresh_environment_if_changed(endpoint=LOCAL_API_BASE)
                if msg:
                    print(f"  [hearth.web] machine changed — {msg}", flush=True)
            else:
                print(f"  [hearth.web] {_envmod.learn_environment(endpoint=LOCAL_API_BASE)}", flush=True)
        except Exception as e:
            print(f"  [hearth.web] machine scan skipped: {e}", flush=True)

    threading.Thread(target=_go, daemon=True).start()


class _QuietHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server that quietly swallows connection-aborted errors.

    Browsers close + reopen sockets aggressively; socketserver's default
    handle_error dumps a full traceback per drop, which looks like Hearth is
    crashing when it's not. We silence ONLY the connection-class errors and
    keep tracebacks for everything else."""
    # A page asks for many things at once (room art, state, previews). The
    # default backlog of 5 makes Windows refuse or drop the rest, and the
    # browser waits on them for many seconds.
    request_queue_size = 128

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError,
                            ConnectionResetError)):
            return  # normal — client disconnected mid-write
        # Real bug — let it bubble up so we see it in the terminal
        super().handle_error(request, client_address)


_MEM_MAINT_STARTED = False


def _start_memory_maintenance_loop(interval_s: int = 1800) -> None:
    """Autonomous memory upkeep on a timer, so consolidation + expiry no longer
    depend on the model happening to save a fact. memory.run_maintenance() is
    self-throttling (24h/new-fact gates, once-per-day expiry, size-cap), so a
    30-min tick is near-free when nothing's due. Local-only; never blocks the
    server. Idempotent — a second call is a no-op."""
    global _MEM_MAINT_STARTED
    if _MEM_MAINT_STARTED:
        return
    _MEM_MAINT_STARTED = True

    def _loop() -> None:
        # First pass a bit after boot so it doesn't compete with model load.
        time.sleep(120)
        while True:
            try:
                from . import memory as _mem
                _mem.run_maintenance()
            except Exception:
                pass
            time.sleep(interval_s)

    threading.Thread(target=_loop, daemon=True).start()


def _warm_imports() -> None:
    """Load the cloud clients in the background, so the first turn doesn't wait
    seconds for the import."""
    def go():
        for mod in ("openai", "anthropic", "hearth.anthropic_chat"):
            try:
                __import__(mod)
            except Exception:
                pass
    threading.Thread(target=go, daemon=True, name="warm-imports").start()


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Start the server (non-blocking) and return the server instance.
    Used by hearth.desktop to embed the same backend in a PyWebView window."""
    server = _QuietHTTPServer((host, port), HearthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _warm_imports()
    try:
        from . import claude_brain
        claude_brain.set_bridge_url(f"http://127.0.0.1:{server.server_address[1]}")
    except Exception:
        pass
    live_bus.announce_window(f"http://127.0.0.1:{server.server_address[1]}")
    # Clear write temporaries a killed save left behind, before anything lists
    # the folder and reports one as a conversation.
    for _d in (CONVOS_DIR, os.path.dirname(SETTINGS_PATH) or "."):
        _sweep_stale_temp_files(_d)
    _preload_whisper_async()
    _start_reminder_watcher()
    _maybe_learn_environment_async()
    _auto_boot_preferred_model_async()
    _auto_boot_helper_async()
    _start_memory_maintenance_loop()
    _resume_downloads_async()
    threading.Thread(target=_migrate_numbered_mic, daemon=True).start()
    # Bring configured phone bridges (Discord/Telegram/WhatsApp) online so the
    # user doesn't have to flip a Start toggle every session. Backgrounded
    # because each spawn waits ~1.6s to fail-fast on a bad token.
    threading.Thread(target=_autostart_bridges, daemon=True).start()
    try:                                  # the opt-in install count (off unless they said yes)
        from . import install_count
        install_count.start()
    except Exception:
        pass
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
        description="Inglen desktop UI — runs in your browser.",
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
    print(f"\n  Inglen UI on {url}")
    print(f"  Model endpoint: {LOCAL_API_BASE}  (default; point it at a real brain in Settings)")
    print(f"  Workspace: {WORKSPACE}")
    print(f"  Ctrl-C to stop.\n")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    _preload_whisper_async()
    _start_reminder_watcher()
    _maybe_learn_environment_async()
    _auto_boot_preferred_model_async()
    _start_memory_maintenance_loop()
    _resume_downloads_async()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[hearth.web] stopping.")
        try:
            from . import mcp_client as _mc
            _mc.shutdown()
        except Exception:
            pass
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
