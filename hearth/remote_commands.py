"""Slash commands sent from the phone or a chat app (Telegram, Discord, WhatsApp).

The window and the terminal have their own; these are the ones that make sense away
from the PC, answered here for every app, before anything reaches the model. A /status
sent from the phone used to go to the model as a message (C21).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# name -> (what it does, the words it takes). Shown in the phone's "/" menu and by /help.
COMMANDS: Dict[str, Tuple[str, str]] = {
    "/status": ("What's thinking, usage and the PC", ""),
    "/brain": ("What thinks, or switch it", "name"),
    "/mode": ("Before acting: ask, edits or auto", "ask|edits|auto"),
    "/think": ("Thinking: on, off, low, medium, high", "on|off|low|medium|high"),
    "/new": ("Start a new chat", ""),
    "/help": ("These commands", ""),
}
ALIASES = {"/usage": "/status", "/model": "/brain", "/clear": "/new", "/commands": "/help"}
# The phone has its own New chat button and opens a chat by itself.
NOT_ON = {"relay": {"/new"}}

_CMD = re.compile(r"^(/[a-z][a-z-]*)(?:@\w+)?(?:\s+(.*))?$", re.S)
MODES = {"ask": "Ask", "edits": "Auto-edit", "auto": "Auto"}
LEVELS = ("low", "medium", "high")


def listing(app: str = "") -> List[Dict[str, str]]:
    """The commands this app offers, for its "/" menu."""
    skip = NOT_ON.get(app, set())
    return [{"name": n, "about": a, "args": w} for n, (a, w) in COMMANDS.items() if n not in skip]


def parse(text: str) -> Optional[Tuple[str, str]]:
    """("/status", "") for a command, None for an ordinary message. A path such as
    /home/me/notes.txt is a message: a command is a word, then a space or the end."""
    m = _CMD.match((text or "").strip())
    if not m:
        return None
    name = m.group(1).lower()
    return ALIASES.get(name, name), (m.group(2) or "").strip()


# ---------------------------------------------------------------- settings, as the window keeps them

def _window(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from .relay_bridge import _window as w
    return w(method, path, body)


def _settings() -> Dict[str, Any]:
    got = _window("GET", "/api/settings")
    if got.get("ok") is False:
        from .web import _load_settings
        return _load_settings()
    return got


def _save(changed: Dict[str, Any]) -> bool:
    """Through the window when it is open (it applies the change and tells the phone and
    other windows); straight to the settings file when it isn't."""
    got = _window("POST", "/api/settings", changed)
    if got.get("ok") is False and "isn't running" in str(got.get("error") or ""):
        from .web import _save_settings
        _save_settings(changed)
        return True
    return got.get("ok") is not False


# ---------------------------------------------------------------- chats a chat app starts fresh

def _chats_file() -> str:
    from .tools import WORKSPACE
    return os.path.join(WORKSPACE, "channel_chats.json")


def current_chat(app: str, chat: str) -> str:
    """The Inglen chat a person's messages go to: the one kept for them, or the one
    they started with /new."""
    from .channel_core import chat_id_for
    base = chat_id_for(app, chat)
    try:
        with open(_chats_file(), encoding="utf-8") as f:
            return str(json.load(f).get(base) or base)
    except (OSError, ValueError):
        return base


def _new_chat(app: str, chat: str) -> str:
    from .channel_core import chat_id_for
    base = chat_id_for(app, chat)
    try:
        with open(_chats_file(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d[base] = f"{base}_{time.strftime('%Y%m%d%H%M%S')}{secrets.token_hex(2)}"[:80]
    os.makedirs(os.path.dirname(_chats_file()), exist_ok=True)
    with open(_chats_file(), "w", encoding="utf-8") as f:
        json.dump(d, f)
    return d[base]


# ---------------------------------------------------------------- the answers

def _status() -> str:
    from .web import status_snapshot
    st = status_snapshot()
    b = st["brain"]
    lines = [f"Thinking with {b['label']}" + (f" · {b['model']}" if b.get("model") else ""),
             f"Before acting: {MODES.get(st.get('mode') or 'ask', st.get('mode'))}"]
    now = time.time()
    for lim in st.get("limits") or []:
        for w in lim.get("windows") or []:
            if w.get("used") is None:
                continue
            left = ""
            if w.get("resets_at"):
                mins = max(0, int((w["resets_at"] - now) / 60))
                left = f", resets in {mins // 60}h {mins % 60}m" if mins >= 60 else f", resets in {mins}m"
            lines.append(f"{lim['label']} {w['name']}: {w['used']}% used{left}")
    loc = st.get("local") or {}
    if loc.get("running"):
        lines.append(f"Engine on this PC: {loc.get('model') or 'running'}")
    h = st.get("helpers") or {}
    if h.get("working"):
        lines.append(f"Helpers working: {h['working']}")
    return "\n".join(lines)


def _brains() -> List[Tuple[str, str, Dict[str, Any]]]:
    """(label, id, how to switch to it) for what can think: agents installed, providers set up."""
    models = _window("GET", "/api/models")
    out = []
    for a in models.get("agent_brains") or []:
        if a.get("installed"):
            out.append((a.get("label") or a["id"], a["id"], {"kind": "agent", "id": a["id"]}))
    for p in models.get("providers") or []:
        out.append((p.get("label") or p["id"], p["id"], {"kind": "provider", "id": p["id"]}))
    return out


def _brain(arg: str) -> str:
    from .web import status_snapshot
    if not arg:
        b = status_snapshot()["brain"]
        names = [label for label, _, _ in _brains()]
        return f"Thinking with {b['label']}" + (f" · {b['model']}" if b.get("model") else "") \
            + (f"\nOthers: {', '.join(names)}" if names else "")
    want = arg.lower()
    pick = next((x for x in _brains() if want in (x[0].lower(), x[1].lower())), None) \
        or next((x for x in _brains() if x[0].lower().startswith(want) or x[1].lower().startswith(want)), None)
    if not pick:
        return f"No brain called {arg}"
    from .relay_bridge import answer
    got = answer(dict(pick[2], what="use"))
    if got.get("ok") is False or got.get("error"):
        return f"Couldn't switch: {got.get('error') or 'the PC said no'}"
    return f"Thinking with {pick[0]}"


def _mode(arg: str) -> str:
    if not arg:
        return f"Before acting: {MODES.get(_settings().get('approval_mode') or 'ask')}"
    v = {"auto-edit": "edits", "autoedit": "edits"}.get(arg.lower(), arg.lower())
    if v not in MODES:
        return "Use ask, edits or auto"
    return f"Before acting: {MODES[v]}" if _save({"approval_mode": v}) else "Couldn't change it"


def _think(arg: str) -> str:
    s = _settings()
    if not arg:
        on = bool(s.get("think_default"))
        return "Thinking: " + ((s.get("think_level") or "on") if on else "off")
    v = arg.lower()
    if v == "off":
        changed: Dict[str, Any] = {"think_default": False}
    elif v == "on":
        changed = {"think_default": True, "think_level": ""}
    elif v in LEVELS:
        changed = {"think_default": True, "think_level": v}
    else:
        return "Use on, off, low, medium or high"
    return f"Thinking: {v}" if _save(changed) else "Couldn't change it"


def _help(app: str) -> str:
    return "\n".join(f"{c['name']}{' ' + c['args'] if c['args'] else ''}  {c['about']}" for c in listing(app))


def answer(app: str, chat: str, text: str) -> Optional[str]:
    """The reply to a command, or None when the text isn't one. Unknown commands say so."""
    got = parse(text)
    if not got:
        return None
    name, arg = got
    if name not in COMMANDS or name in NOT_ON.get(app, set()):
        return f"No {name} here. /help lists them."
    try:
        if name == "/status":
            return _status()
        if name == "/brain":
            return _brain(arg)
        if name == "/mode":
            return _mode(arg)
        if name == "/think":
            return _think(arg)
        if name == "/new":
            _new_chat(app, chat)
            return "New chat"
        return _help(app)
    except Exception as e:
        return f"Couldn't do {name}: {type(e).__name__}"
