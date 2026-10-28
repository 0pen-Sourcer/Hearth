"""Hearth Plus Relay: JARVIS on the phone, through Hearth's relay server.

The PC connects outward to the relay (nothing to open on the router), one room
per paired phone, and everything between them is end-to-end encrypted
(hearth/relay_crypto.py). Messages from the phone run through the same core as
the other chat apps (hearth/channel_core.py): the conversation is a Hearth
chat, risky steps ask first (as Allow / Deny on the phone), files go both ways.

State in ~/.hearth/relay.json: the PC's key, the paired phones, and a pairing
in progress. Pairing: "Pair a phone" makes a QR code; the phone scans it and
sends its key with proof it saw the code; both show six digits; confirming on
the PC remembers the phone.

    python -m hearth.relay_bridge
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import urllib.parse
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from . import channel_core as cc
from . import env as _env
from . import relay_crypto as R

_env.setdefault("NO_GUI", "1")

STATE_FILE = os.path.join(os.path.expanduser("~"), ".hearth", "relay.json")
DEFAULT_RELAY = os.environ.get("HEARTH_RELAY_URL") or ""
PAIR_LIFE_S = 600
_LOCK = threading.Lock()


# ---------------------------------------------------------------- state

def _read() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, STATE_FILE)


def relay_url() -> str:
    try:
        from .web import _load_settings
        u = (_load_settings().get("relay_url") or "").strip()
    except Exception:
        u = ""
    return (u or DEFAULT_RELAY).rstrip("/")


def _pc_key():
    with _LOCK:
        d = _read()
        if not d.get("pc_key"):
            d["pc_key"] = R.b64(R.raw_private(R.new_private()))
            _write(d)
    return R.private_from(R.unb64(d["pc_key"]))


def phone_id(phone_public: bytes) -> str:
    return hashlib.sha256(phone_public).hexdigest()[:12]


def start_pairing(name: str = "") -> Dict[str, Any]:
    """A new pairing: what the QR code holds, as a link that opens the phone app."""
    url = relay_url()
    if not url:
        return {"ok": False, "error": "no relay address yet (Settings > Plus)"}
    pc = _pc_key()
    ps = secrets.token_bytes(16)
    payload = R.pairing_payload(url.replace("https://", "wss://").replace("http://", "ws://"), R.raw_public(pc), ps)
    with _LOCK:
        d = _read()
        d["pending"] = {"ps": R.b64(ps), "room": payload["room"], "at": time.time(), "name": name or "",
                        "phone": "", "digits": ""}
        _write(d)
    # Only the PC's key and the secret go in the link: the phone app is served by
    # the relay, so it knows the relay's address, and works the room out from the
    # two. A short link is a QR code with big squares that old cameras read.
    # It goes after #: a browser never sends that part to the server.
    frag = R.b64(R.raw_public(pc) + ps)
    return {"ok": True, "link": f"{url}/#p={frag}", "room": payload["room"]}


def pairing_status() -> Dict[str, Any]:
    p = _read().get("pending") or {}
    if not p or time.time() - float(p.get("at") or 0) > PAIR_LIFE_S:
        return {"state": "none"}
    return {"state": "confirm" if p.get("digits") else "waiting", "digits": p.get("digits") or ""}


def confirm_pairing(name: str = "") -> Dict[str, Any]:
    """The person saw the same six digits on both screens: remember the phone."""
    with _LOCK:
        d = _read()
        p = d.get("pending") or {}
        if not p.get("phone") or time.time() - float(p.get("at") or 0) > PAIR_LIFE_S:
            return {"ok": False, "error": "no phone is waiting to be paired; scan the code again"}
        phones = [x for x in d.get("phones", []) if x.get("pub") != p["phone"]]
        phones.append({"pub": p["phone"], "ps": p["ps"], "room": p["room"], "name": name or p.get("name") or "Phone",
                       "at": time.time()})
        d["phones"], d["pending"] = phones, {}
        d["confirmed"] = p["room"]
        _write(d)
    return {"ok": True}


def cancel_pairing() -> Dict[str, Any]:
    with _LOCK:
        d = _read()
        d["pending"] = {}
        _write(d)
    return {"ok": True}


def phones() -> List[Dict[str, Any]]:
    return [{"id": phone_id(R.unb64(p["pub"])), "name": p.get("name") or "Phone", "at": p.get("at")}
            for p in _read().get("phones", [])]


def forget_phone(pid: str) -> Dict[str, Any]:
    with _LOCK:
        d = _read()
        keep = [p for p in d.get("phones", []) if phone_id(R.unb64(p["pub"])) != pid]
        gone = len(keep) != len(d.get("phones", []))
        d["phones"] = keep
        _write(d)
    return {"ok": gone}


# ---------------------------------------------------------------- big messages

# The relay passes frames of up to 256 KB. A message bigger than PART (a photo,
# a file, a voice note) goes as parts {type: part, pid, i, n, s} of its JSON,
# joined again on the other side before it is read.
PART = 150_000


def split_message(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = json.dumps(obj, separators=(",", ":"))
    if len(text) <= PART:
        return [obj]
    pid = secrets.token_hex(6)
    chunks = [text[i:i + PART] for i in range(0, len(text), PART)]
    return [{"type": "part", "pid": pid, "i": i, "n": len(chunks), "s": c} for i, c in enumerate(chunks)]


class Joiner:
    """Puts parts back together; whole messages pass straight through."""

    def __init__(self, max_bytes: int = 40_000_000):
        self.parts: Dict[str, Dict[int, str]] = {}
        self.max_bytes = max_bytes

    def add(self, m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if m.get("type") != "part":
            return m
        pid, n = str(m.get("pid") or ""), int(m.get("n") or 0)
        got = self.parts.setdefault(pid, {})
        got[int(m.get("i") or 0)] = str(m.get("s") or "")
        if n * PART > self.max_bytes or len(self.parts) > 8:
            self.parts.pop(pid, None)
            return None
        if len(got) < n:
            return None
        del self.parts[pid]
        try:
            whole = json.loads("".join(got[i] for i in range(n)))
        except (KeyError, ValueError):
            return None
        return whole if isinstance(whole, dict) and whole.get("type") != "part" else None


# ---------------------------------------------------------------- what the phone asks for

def answer(m: Dict[str, Any], own_chat: str = "") -> Dict[str, Any]:
    """The phone's question about this PC, answered: the chat list ("chats"), one
    chat's messages ("open"), the helpers ("team"), or the look ("look")."""
    from . import chat_sync
    what = str(m.get("what") or "")
    if what == "chats":
        rows = chat_sync.listing(int(m.get("limit") or 80))
        return {"chats": [{"id": r["id"], "title": r["title"], "at": r["updated"], "source": r["source"],
                           "count": r["count"]} for r in rows], "own": own_chat}
    if what == "open":
        from .term_render import tool_words
        cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80]
        d = chat_sync.load(cid) or {}
        msgs = []
        for x in d.get("messages") or []:
            role = x.get("role")
            if role not in ("user", "assistant"):
                continue
            events = [e for e in x.get("events") or [] if isinstance(e, dict)]
            steps = []
            for e in events:
                if e.get("kind") != "tool":
                    continue
                args = e.get("args") if isinstance(e.get("args"), dict) else {}
                if e.get("name") == "web_search" and not args.get("query") and e.get("result"):
                    # Codex steps saved before their query was read kept it as the result.
                    args = {"query": str(e.get("result"))[:90]}
                step = {"w": tool_words(e.get("name") or "", args), "tool": e.get("name") or "",
                        "site": cc.site_of(args)}
                if e.get("name") == "web_search":
                    step["sites"] = cc.sites_in(str(e.get("result") or ""))
                steps.append(step)
            # The window keeps a turn's stats on its done event and its reasoning as think chunks.
            done = next((e for e in reversed(events) if e.get("kind") == "done"), {})
            stats = x.get("stats") or done.get("stats") or None
            if stats is not None and done.get("duration_ms") is not None and "duration_ms" not in stats:
                stats = dict(stats, duration_ms=done["duration_ms"])
            thought = "".join(str(e.get("content") or "") for e in events if e.get("kind") == "think_chunk").strip()
            text = chat_sync._text(x.get("content"))
            # A file sent from a phone is kept as a note for the model; the person sent the file.
            text = re.sub(r"\[They sent (.+?), saved at .+?before answering about it\.\]",
                          lambda g: "Sent " + g.group(1), text, flags=re.S)
            if text or steps:
                msgs.append({"role": role, "text": text, "at": x.get("at") or 0, "steps": steps,
                             "via": x.get("via") or "", "stats": stats, "thought": thought[-6000:],
                             "context": [{"topic": t.get("topic") or "", "ago": t.get("ago") or ""}
                                         for t in (x.get("context") or []) if isinstance(t, dict)]})
        keep = int(m.get("last") or 60)
        return {"chat": cid, "title": d.get("title") or "", "source": d.get("source") or "",
                "messages": msgs[-keep:], "more": max(0, len(msgs) - keep)}
    if what == "team":
        from . import subagents
        return {"team": [{"id": a.get("id"), "name": a.get("name") or a.get("persona") or "Helper",
                          "role": a.get("persona") or "", "status": a.get("status"), "what": a.get("what") or "",
                          "task": str(a.get("prompt") or "")[:300], "on": a.get("on") or a.get("runtime") or "",
                          "steps": a.get("tools_used") or 0, "blocked": a.get("blocked") or ""}
                         for a in subagents.team_state()]}
    if what == "ideas":
        return ideas()
    if what == "icons":
        # Brands' own marks by key and sites' icons by domain, as the window shows them.
        from .web import _favicon
        from .brand_icons import brand_icon
        out = {}
        for key in [str(x) for x in (m.get("keys") or [])][:40]:
            try:
                icon = brand_icon(key)
            except Exception:
                icon = None
            if icon:
                out["brand:" + key] = f"data:{icon[1]};base64,{base64.b64encode(icon[0]).decode()}"
        for site in [str(x) for x in (m.get("sites") or [])][:24]:
            try:
                icon = _favicon(site)
            except Exception:
                icon = None
            if icon:
                out[site] = f"data:{icon[1]};base64,{base64.b64encode(icon[0]).decode()}"
        return {"icons": out}
    if what == "provider_models":
        # One saved provider's models, asked with its saved key, as the window's menu lists them.
        out = _window("POST", "/api/providers/models", {"id": str(m.get("id") or "")}, timeout=20)
        return out if out.get("models") is not None else {"error": out.get("error") or "no model list"}
    if what == "facts":
        # What a model takes in, whether it reasons, its context and price: the window's details card.
        v = str(m.get("v") or "")
        return _window("GET", "/api/model/facts?v=" + urllib.parse.quote(v, safe=""))
    if what in ("brain", "agent_models"):
        return cached_brain(what, m)
    if what in ("use", "set"):
        out = brain_request(what, m)
        if out.get("ok") is not False:
            _settle_brain(what, m)
        warm_brain()
        return out
    if what == "look":
        try:
            from .web import _load_settings
            s = _load_settings() or {}
        except Exception:
            s = {}
        return {"theme": s.get("ui_theme") or "", "accent": s.get("ui_accent") or ""}
    return {"error": f"unknown request {what!r}"}


def ideas() -> Dict[str, Any]:
    """What a new chat on the phone offers: the threads going on lately (each
    opens its chat) and the reminder ideas Hearth already wrote, from its cache."""
    out: Dict[str, Any] = {"threads": [], "ideas": []}
    try:
        from . import threads
        out["threads"] = [{"topic": t.get("topic") or "", "chat": t.get("chat") or "", "ago": t.get("ago") or "",
                           "open": (t.get("open") or [])[:2]} for t in threads.recent(limit=3) if t.get("chat")]
    except Exception:
        pass
    try:
        from .tools import WORKSPACE
        with open(os.path.join(WORKSPACE, "logs", "reminder_ideas.json"), encoding="utf-8") as f:
            cached = json.load(f)
        out["ideas"] = [{"title": i.get("title") or "", "ask": i.get("ask") or ""}
                        for i in (cached.get("ideas") or [])[:3] if i.get("ask")]
    except Exception:
        pass
    return out


# The brain and the settings the phone can change go through the window's own
# routes (its address is handed to this bridge when the window starts it), so a
# switch from the phone is the same switch as one made in the window.
WINDOW_URL = os.environ.get("HEARTH_WINDOW_URL", "").rstrip("/")
_SETTABLE = {"approval_mode": ("ask", "edits", "auto"), "think_default": (True, False), "think_level": None}


def _window(method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 30) -> Dict[str, Any]:
    import urllib.error
    import urllib.request
    if not WINDOW_URL:
        return {"ok": False, "error": "the Hearth window isn't running on the PC"}
    req = urllib.request.Request(WINDOW_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return {"ok": False, **json.loads(e.read() or b"{}")}
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# What the phone's picker shows is read ahead: asking an agent for its models
# can mean starting that agent (Claude Code took 20-30 seconds the first time),
# far too long for a phone waiting on a tap. When a phone connects, the brain and
# every agent's models are read in the background; the phone gets what is kept,
# and anything older than a minute is read again behind it.
_BRAIN_CACHE: Dict[str, Any] = {}       # "brain" -> (at, answer); ("agent", id) -> (at, answer)
_WARMING = threading.Lock()
_CHANGED = [0]                          # bumped by each switch or setting from the phone
_AGAIN = threading.Event()


def warm_brain() -> None:
    """Re-read the brain list in the background. A read that started before a
    switch is thrown away, and one more read follows it."""
    def run():
        if not _WARMING.acquire(blocking=False):
            _AGAIN.set()
            return
        try:
            while True:
                _AGAIN.clear()
                seen = _CHANGED[0]
                b = brain_request("brain", {})
                if not b.get("error") and seen == _CHANGED[0]:
                    _BRAIN_CACHE["brain"] = (time.time(), b)
                for a in b.get("agents") or []:
                    info = brain_request("agent_models", {"id": a["id"]})
                    if info.get("models") and seen == _CHANGED[0]:
                        _BRAIN_CACHE[("agent", a["id"])] = (time.time(), info)
                if not _AGAIN.is_set() and seen == _CHANGED[0]:
                    break
        finally:
            _WARMING.release()
    threading.Thread(target=run, daemon=True, name="relay-brain-warm").start()


def _settle_brain(what: str, m: Dict[str, Any]) -> None:
    """Write what the phone just changed into the saved list, so the next look
    at it answers at once instead of waiting for the whole re-read."""
    _CHANGED[0] += 1
    hit = _BRAIN_CACHE.get("brain")
    if not hit:
        return
    b = dict(hit[1])
    if what == "set":
        key = str(m.get("key") or "")
        if key in ("approval_mode", "think_level", "think_default"):
            b[key] = m.get("value")
    elif m.get("kind") == "agent":
        b["provider"] = str(m.get("id") or "")
        if m.get("model") is not None:
            info = _BRAIN_CACHE.get(("agent", b["provider"]))
            if info:
                _BRAIN_CACHE[("agent", b["provider"])] = (time.time(), dict(info[1], model=str(m.get("model") or "")))
    elif m.get("kind") == "provider":
        b["provider"] = str(m.get("id") or "")
        p = next((x for x in b.get("providers") or [] if x.get("id") == b["provider"]), None)
        b["model"] = str(m.get("model") or (p or {}).get("model") or "")
    else:
        # A model loading on the PC: what's loaded changes as it goes, so read it fresh.
        _BRAIN_CACHE.pop("brain", None)
        return
    _BRAIN_CACHE["brain"] = (time.time(), b)


def cached_brain(what: str, m: Dict[str, Any]) -> Dict[str, Any]:
    key = "brain" if what == "brain" else ("agent", str(m.get("id") or ""))
    hit = _BRAIN_CACHE.get(key)
    if hit:
        if time.time() - hit[0] > 60:
            warm_brain()
        return hit[1]
    out = brain_request(what, m)
    if not out.get("error") and (what == "brain" or out.get("models")):
        _BRAIN_CACHE[key] = (time.time(), out)
    return out


def brain_request(what: str, m: Dict[str, Any]) -> Dict[str, Any]:
    if what == "brain":
        models = _window("GET", "/api/models")
        state = _window("GET", "/api/state")
        settings = _window("GET", "/api/settings")
        disk = (_window("GET", "/api/llmserver/status").get("disk_models") or [])
        if models.get("ok") is False and not models.get("agent_brains"):
            return {"error": models.get("error") or "the window didn't answer"}
        from .brand_icons import key_for
        agents = [{"id": a["id"], "label": a.get("label") or a["id"], "plan": a.get("plan") or "",
                   "site": a.get("icon_site") or urllib.parse.urlparse(a.get("site") or "").netloc,
                   "brand": key_for(a["id"])}
                  for a in models.get("agent_brains") or [] if a.get("installed")]
        provider = str(state.get("provider") or settings.get("llm_provider") or "")
        return {"provider": provider, "model": state.get("picked_model") or settings.get("llm_model") or "",
                "agents": agents,
                "providers": [{"id": p["id"], "label": p.get("label") or p["id"], "model": p.get("model") or "",
                               "active": bool(p.get("active")), "brand": key_for(p["id"]),
                               "site": re.sub(r"^api\.|^generativelanguage\.", "",
                                              urllib.parse.urlparse(p.get("url") or "").netloc)}
                              for p in models.get("providers") or []],
                "local": [{"path": d.get("path"), "name": str(d.get("filename") or "").replace(".gguf", ""),
                           "size_gb": d.get("size_gb")} for d in disk if not (d.get("not_chat") or d.get("incomplete"))],
                "loaded": [x.get("id") for x in models.get("models") or [] if x.get("state") == "loaded"],
                "approval_mode": settings.get("approval_mode") or "ask",
                "think_default": bool(settings.get("think_default")), "think_level": settings.get("think_level") or ""}
    if what == "agent_models":
        return _window("GET", "/api/brain/agent/models?id=" + re.sub(r"[^a-z0-9_-]", "", str(m.get("id") or "")))
    if what == "use":
        kind = m.get("kind")
        if kind == "agent":
            body: Dict[str, Any] = {"provider": str(m.get("id") or "")}
            if m.get("model") is not None:
                body["model"] = str(m.get("model") or "")
            return _window("POST", "/api/brain/agent", body)
        if kind == "provider":
            models = _window("GET", "/api/models")
            p = next((x for x in models.get("providers") or [] if x.get("id") == m.get("id")), None)
            if not p:
                return {"ok": False, "error": "that provider isn't set up on the PC"}
            return _window("POST", "/api/llm-endpoint", {"provider": p["id"], "url": p.get("url") or "", "key": "",
                                                          "model": str(m.get("model") or p.get("model") or "")})
        if kind == "local":
            return _window("POST", "/api/llmserver/start", {"model_path": str(m.get("path") or "")}, timeout=600)
        return {"ok": False, "error": "unknown kind"}
    if what == "set":
        key, value = str(m.get("key") or ""), m.get("value")
        allowed = _SETTABLE.get(key, ())
        if key not in _SETTABLE or (allowed is not None and value not in allowed):
            return {"ok": False, "error": f"{key} can't be changed from a phone"}
        return _window("POST", "/api/settings", {key: value})
    return {"error": "unknown"}


# ---------------------------------------------------------------- the adapter

class Relay(cc.Adapter):
    """Sends go out as encrypted frames on the phone's live connection, or to
    its mailbox on the server when it's away."""
    name, label, max_len = "relay", "Phone", 20000

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.rooms: Dict[str, "Room"] = {}     # chat (phone id) -> room
        self._live_at: Dict[str, float] = {}   # turn -> when its live text last went out
        self._think_at: Dict[str, float] = {}  # turn -> when its reasoning last went out

    def _push(self, chat: str, obj: Dict[str, Any]) -> Optional[str]:
        room = self.rooms.get(str(chat))
        if room is None:
            return None
        mid = secrets.token_hex(6)
        # The Hearth chat and the turn it belongs to, so the app files it there.
        obj = dict(obj, id=mid, **cc.turn_context())
        fut = asyncio.run_coroutine_threadsafe(room.deliver(obj), self.loop)
        try:
            fut.result(15)
        except Exception:
            return None
        return mid

    def send(self, chat, text, reply_to=None):
        return self._push(chat, {"type": "text", "text": text})

    def edit(self, chat, msg_id, text):
        return self._push(chat, {"type": "edit", "of": msg_id, "text": text}) is not None

    def ask(self, chat, text):
        return self._push(chat, {"type": "ask", "text": text})

    def send_file(self, chat, path):
        try:
            if os.path.getsize(path) > 15_000_000:
                return
            with open(path, "rb") as f:
                data = R.b64(f.read())
        except OSError:
            return
        self._push(chat, {"type": "file", "name": os.path.basename(path), "data": data})

    def typing(self, chat):
        self._push(chat, {"type": "typing"})

    def step(self, chat, words, tool="", site=""):
        """One step of the reply as it happens ("Read notes.txt"), with the tool
        and the site it reaches, so the phone can show that site's icon."""
        self._push(chat, {"type": "step", "words": words, "tool": tool, "site": site})

    def think(self, chat, text):
        """The reasoning so far, a few times a second, folded on the phone like the window's."""
        t = cc.turn_context().get("t", "")
        now = time.time()
        if now - self._think_at.get(t, 0) < 0.5:
            return
        self._think_at[t] = now
        self._push(chat, {"type": "think", "text": text[-4000:]})

    def heard(self, chat, text, cid=""):
        """What a voice note said, so the phone shows the words instead of "Voice note"."""
        self._push(chat, {"type": "heard", "text": text, **({"c": cid} if cid else {})})

    def step_sites(self, chat, sites):
        """The sites a search just found, for the step before it."""
        if sites:
            self._push(chat, {"type": "sites", "sites": list(sites)[:6]})

    def live(self, chat, text):
        """The reply so far, a few times a second; the finished one follows as text."""
        t = cc.turn_context().get("t", "")
        now = time.time()
        if now - self._live_at.get(t, 0) < 0.35:
            return
        self._live_at[t] = now
        if len(self._live_at) > 50:
            self._live_at = {t: now}
        self._push(chat, {"type": "live", "text": text})


class Room:
    """One phone's room on the relay: its connection, the session with the
    phone when it's there, and the pairing when it's new."""

    def __init__(self, bridge: "Bridge", room: str, ps: bytes, phone: Optional[bytes]):
        self.bridge, self.room, self.ps, self.phone = bridge, room, ps, phone
        self.ws = None
        self.chan: Optional[R.Channel] = None
        self.peer_up = False
        self.pc = bridge.pc
        self.joiner = Joiner()
        self.lookups: set = set()

    @property
    def chat(self) -> str:
        return phone_id(self.phone) if self.phone else "pending"

    def box_key(self) -> bytes:
        return R.mailbox_key(R.dh(self.pc, self.phone), self.room)

    async def deliver(self, obj: Dict[str, Any]) -> None:
        if self.ws is None:
            return
        for piece in split_message(obj):
            if self.chan is not None and self.peer_up:
                await self.ws.send(json.dumps(self.chan.seal(piece)))
            elif self.phone:
                await self.ws.send(json.dumps(R.seal_box(self.box_key(), piece)))

    async def run(self) -> None:
        import websockets
        url = f"{self.bridge.url}/room/{self.room}?role=pc&licence={self.bridge.licence}"
        backoff = 1.0
        while not self.bridge.stopping:
            try:
                async with websockets.connect(url, max_size=1 << 20, ping_interval=25) as ws:
                    self.ws, backoff = ws, 1.0
                    async for raw in ws:
                        await self.on_frame(json.loads(raw))
            except Exception as e:
                if "402" in str(e):
                    print("[relay] the relay needs Hearth Plus for this PC; stopping.", flush=True)
                    self.bridge.stopping = True
                    return
            self.ws, self.chan, self.peer_up = None, None, False
            if self.bridge.stopping or self.room not in self.bridge.wanted():
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def on_frame(self, f: Dict[str, Any]) -> None:
        t = f.get("t")
        if t == "peer":
            self.peer_up = bool(f.get("up"))
            if not self.peer_up:
                self.chan = None
            return
        if t == "pair":                       # a phone that scanned the code
            phone = R.unb64(f.get("phone") or "")
            if self.phone or not R.proof_ok(self.ps, phone, f.get("proof") or ""):
                return
            digits = R.check_digits(R.dh(self.pc, phone), self.room)
            with _LOCK:
                d = _read()
                if (d.get("pending") or {}).get("room") == self.room:
                    d["pending"].update(phone=R.b64(phone), digits=digits)
                    _write(d)
            print(f"[relay] a phone asks to pair; confirm {digits} on the PC", flush=True)
            return
        if t == "hello":                      # a paired phone, connecting
            phone = R.unb64(f.get("phone") or "")
            if not self.phone or phone != self.phone:
                return
            eph = R.new_private()
            phone_eph = R.unb64(f.get("eph") or "")
            to_phone, to_pc = R.session_keys(R.dh(self.pc, phone), R.dh(eph, phone_eph), self.room,
                                             R.raw_public(eph), phone_eph)
            self.chan = R.Channel(to_phone, to_pc)
            self.peer_up = True
            self.bridge.adapter.rooms[self.chat] = self
            await self.ws.send(json.dumps({"t": "hello", "eph": R.b64(R.raw_public(eph))}))
            from . import persona
            await self.deliver({"type": "hi", "name": persona.NAME})
            warm_brain()                      # the picker's answers, ready before it's opened
            return
        if t == "msg" and self.chan is not None:
            m = self.chan.open(f)
            m = self.joiner.add(m) if m else None
            if m and m.get("type") == "req":
                # Lookups run side by side, so a slow one (icons from the web)
                # never holds up a switch the person is waiting on.
                task = asyncio.ensure_future(asyncio.to_thread(self.bridge.on_message, self, m))
                self.lookups.add(task)
                task.add_done_callback(self.lookups.discard)
            elif m:
                await asyncio.to_thread(self.bridge.on_message, self, m)

    async def tell_paired(self) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps({"t": "paired"}))


class Bridge:
    def __init__(self):
        self.url = relay_url().replace("https://", "wss://").replace("http://", "ws://")
        from . import plus
        self.licence = (plus._read().get("key") or "")
        self.pc = _pc_key()
        self.stopping = False
        self.loop = asyncio.new_event_loop()
        self.adapter = Relay(self.loop)
        self.hub = cc.Hub(self.adapter, configured_senders=["phone"])
        self.rooms: Dict[str, Room] = {}

    def wanted(self) -> Dict[str, Dict[str, Any]]:
        """room -> {ps, phone}: every paired phone's room, and a pairing in progress."""
        d = _read()
        out = {p["room"]: {"ps": p["ps"], "phone": p["pub"]} for p in d.get("phones", [])}
        p = d.get("pending") or {}
        if p.get("room") and time.time() - float(p.get("at") or 0) < PAIR_LIFE_S:
            out.setdefault(p["room"], {"ps": p["ps"], "phone": ""})
        return out

    def on_message(self, room: Room, m: Dict[str, Any]) -> None:
        if m.get("type") == "req":
            try:
                out = answer(m, cc.chat_id_for(self.adapter.name, room.chat))
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {e}"}
            # "rid" names the request; "id" is left to the request itself (an agent's id).
            # Older phone apps sent only "id".
            re_id = m.get("rid") or m.get("id")
            fut = asyncio.run_coroutine_threadsafe(room.deliver({**out, "type": "res", "re": re_id}), self.loop)
            try:
                fut.result(30)
            except Exception:
                pass
            return
        if m.get("type") != "user":
            return
        files = []
        for f in m.get("files") or []:
            try:
                files.append(cc.save_upload(R.unb64(f.get("data") or ""), f.get("name") or "file"))
            except Exception:
                continue
        voice = ""
        if m.get("voice"):
            ext = str(m.get("voice_ext") or "webm").strip(".").lower()
            voice = cc.save_upload(R.unb64(m["voice"]), "voice-note." + (ext if ext in ("webm", "mp4", "ogg", "m4a", "wav") else "webm"))
        self.hub.receive(room.chat, "phone", str(m.get("text") or ""), name="Phone", voice=voice, files=files,
                         cid=str(m.get("chat") or ""), regen=bool(m.get("regen")))

    async def keep_rooms(self) -> None:
        """Join new rooms (a pairing started, a phone confirmed), leave dropped ones."""
        tasks: Dict[str, asyncio.Task] = {}
        while not self.stopping:
            want = self.wanted()
            confirmed = _read().get("confirmed")
            for room, info in want.items():
                phone = R.unb64(info["phone"]) if info["phone"] else None
                r = self.rooms.get(room)
                if r is None:
                    r = self.rooms[room] = Room(self, room, R.unb64(info["ps"]), phone)
                    tasks[room] = asyncio.create_task(r.run())
                elif phone and r.phone is None:          # just confirmed on the PC
                    r.phone = phone
                    if confirmed == room:
                        await r.tell_paired()
            for room in list(self.rooms):
                if room not in want:
                    t = tasks.pop(room, None)
                    if t:
                        t.cancel()
                    self.rooms.pop(room, None)
            await asyncio.sleep(1.0)

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.keep_rooms())
        except KeyboardInterrupt:
            pass


def run() -> None:
    if not relay_url():
        print("[relay] no relay address yet (Settings > Plus).", flush=True)
        return
    cc.use_saved_brain(lambda s: print("[relay] " + s, flush=True))
    print("[relay] up. Phones paired:", len(phones()), flush=True)
    Bridge().run()


if __name__ == "__main__":
    from .bridge_status import follow_parent
    follow_parent()
    run()
