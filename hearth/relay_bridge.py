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
# How often the reply so far, and the reasoning, go to the phone while a turn runs.
# Durable Objects bill incoming WebSocket messages 20:1, so ten a second for a
# minute is 30 billed requests.
LIVE_EVERY = 0.1
THINK_EVERY = 0.25
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
                           "count": r["count"], "pinned": bool(r.get("pinned"))} for r in rows], "own": own_chat}
    if what == "chat":
        return chat_action(m)
    if what == "agent_sessions":
        # The coding agents' own sessions on this PC, newest first.
        from . import agent_sessions
        from .brand_icons import key_for
        return {"sessions": [dict(r, brand=key_for(r["agent"])) for r in agent_sessions.listing(int(m.get("limit") or 120))]}
    if what == "open":
        from .term_render import tool_words
        cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80]
        from . import agent_sessions
        if agent_sessions.parse_chat(cid):
            return agent_sessions.transcript(cid, int(m.get("last") or 60))
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
                if cc.detail_of(args):
                    step["cmd"] = cc.detail_of(args)
                if e.get("result"):
                    step["out"] = cc.out_of(e.get("result"))
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
            failed = next((str(e.get("text") or "") for e in events if e.get("kind") == "error"), "")
            if failed and not text.strip():
                msgs.append({"role": "assistant", "text": failed, "at": x.get("at") or 0, "steps": steps, "error": True})
                continue
            if text or steps:
                msgs.append({"role": role, "text": text, "at": x.get("at") or 0, "steps": steps,
                             "via": x.get("via") or "", "stats": stats, "thought": thought[-6000:],
                             "context": [context_item(t) for t in (x.get("context") or []) if isinstance(t, dict)]})
        keep = int(m.get("last") or 60)
        return {"chat": cid, "title": d.get("title") or "", "source": d.get("source") or "",
                "messages": msgs[-keep:], "more": max(0, len(msgs) - keep)}
    if what == "team":
        return {"team": [{"id": a.get("id"), "name": a.get("name") or a.get("persona") or "Helper",
                          "role": a.get("persona") or "", "status": a.get("status"), "what": a.get("what") or "",
                          "task": str(a.get("prompt") or "")[:300], "on": a.get("on") or a.get("runtime") or "",
                          "steps": a.get("tools_used") or 0, "blocked": a.get("blocked") or ""}
                         for a in team_now()]}
    if what == "room":
        return room_state()
    if what == "room_art":
        return room_art([str(x) for x in (m.get("files") or [])][:4])
    if what == "story":
        return story(str(m.get("id") or ""))
    if what == "ideas":
        return ideas()
    if what == "memory_item":
        # "wrong / forget" or "done with this" on what a reply had in mind.
        from . import memory_cards
        return memory_cards.act(str(m.get("kind") or ""), str(m.get("item") or ""), str(m.get("action") or ""))
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


def chat_action(m: Dict[str, Any]) -> Dict[str, Any]:
    """Pin, rename or delete a chat from the phone, through the window's own
    conversation routes, so the window's sidebar and the phone agree."""
    cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80]
    action = str(m.get("action") or "")
    if not cid:
        return {"ok": False, "error": "no chat"}
    path = "/api/conversations/" + urllib.parse.quote(cid, safe="")
    if action == "pin":
        out = _window("POST", "/api/conversations/pin", {"id": cid, "pinned": bool(m.get("pinned"))})
        return {"ok": out.get("ok") is not False, "pinned": bool(out.get("pinned")), **_why(out)}
    if action == "rename":
        title = " ".join(str(m.get("title") or "").split())[:120]
        if not title:
            return {"ok": False, "error": "a name can't be empty"}
        d = _window("GET", path)
        if d.get("ok") is False or not d.get("id"):
            return {"ok": False, "error": d.get("error") or "that chat isn't on the PC"}
        d["title"], d["titled"] = title, True        # named by hand: never retitled
        out = _window("POST", "/api/conversations", d)
        return {"ok": out.get("ok") is not False, "title": title, **_why(out)}
    if action == "delete":
        out = _window("DELETE", path)
        return {"ok": out.get("ok") is not False, **_why(out)}
    return {"ok": False, "error": f"unknown action {action!r}"}


def _why(out: Dict[str, Any]) -> Dict[str, Any]:
    return {"error": out.get("error") or "the PC didn't do it"} if out.get("ok") is False else {}


def drop_from(cid: str, text: str, back: int = 0) -> bool:
    """An edited message from the phone: the chat goes back to just before it,
    so the new words are asked in its place. `back` counts the person's
    messages after it; the text has to match, or nothing is dropped."""
    from . import chat_sync
    d = chat_sync.load(cid)
    msgs = list((d or {}).get("messages") or [])
    want = " ".join(str(text or "").split())
    if not msgs or not want:
        return False

    def said(x):
        # A message that carried a file has the note about it after the words.
        return re.sub(r"\s*\[They sent .+$", "", " ".join(chat_sync._text(x.get("content")).split()))

    mine = [i for i, x in enumerate(msgs) if x.get("role") == "user"]
    at = None
    if 0 <= back < len(mine) and said(msgs[mine[-1 - back]]) == want:
        at = mine[-1 - back]
    else:
        at = next((i for i in reversed(mine) if said(msgs[i]) == want), None)
    if at is None:
        return False
    d["messages"], d["msg_count"] = msgs[:at], at
    chat_sync.atomic_write_json(chat_sync._path(cid), d, indent=2, ensure_ascii=False)
    return True


def team_now() -> List[Dict[str, Any]]:
    """The helpers as the window has them. Only the process that started a
    helper knows it is still running, so this process's own reading would call
    the window's helpers stopped; it is the fallback when no window is up."""
    r = _window("GET", "/api/team", timeout=10)
    if isinstance(r.get("agents"), list):
        return r["agents"]
    from . import subagents
    return subagents.team_state()


# ---------------------------------------------------------------- the room

_TEAM_ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "team")
_ART_V: Dict[str, Any] = {}


def _art_files(man: Dict[str, Any]) -> List[str]:
    rooms = man.get("rooms") or [{"file": "room.png"}]
    names = [r.get("file") for r in rooms] + (["props.png"] if man.get("props") else []) \
        + [f"{r}.png" for r in man.get("roles") or []]
    return [n for n in names if isinstance(n, str) and re.fullmatch(r"[\w.-]+\.png", n)]


def _manifest() -> Dict[str, Any]:
    try:
        with open(os.path.join(_TEAM_ART, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def art_version() -> str:
    """One name for this set of room pictures: the phone keeps them under it
    and asks again only when it changes."""
    man = _manifest()
    if not man:
        return ""
    files = ["manifest.json"] + _art_files(man)
    stamp = []
    for f in files:
        try:
            st = os.stat(os.path.join(_TEAM_ART, f))
            stamp.append((f, st.st_size, st.st_mtime_ns))
        except OSError:
            stamp.append((f, 0, 0))
    if _ART_V.get("stamp") != stamp:
        h = hashlib.sha1()
        for f, _, _ in stamp:
            try:
                with open(os.path.join(_TEAM_ART, f), "rb") as fh:
                    h.update(f.encode() + b"\0" + fh.read())
            except OSError:
                pass
        _ART_V.update(stamp=stamp, v=h.hexdigest()[:16])
    return _ART_V["v"]


def room_art(files: List[str]) -> Dict[str, Any]:
    """The room's manifest, and the pictures asked for as data URLs (a few at
    a time: each is a few hundred KB and goes in parts)."""
    man = _manifest()
    if not man:
        return {"error": "the room's pictures aren't on this PC"}
    allowed = set(_art_files(man))
    images = {}
    for f in files:
        if f not in allowed:
            continue
        try:
            with open(os.path.join(_TEAM_ART, f), "rb") as fh:
                images[f] = "data:image/png;base64," + base64.b64encode(fh.read()).decode()
        except OSError:
            continue
    return {"v": art_version(), "manifest": man, "files": sorted(allowed), "images": images,
            "hashes": _art_hashes(sorted(allowed))}


_HASHES: Dict[str, Any] = {"v": "", "h": {}}


def _art_hashes(files: List[str]) -> Dict[str, str]:
    """SHA-256 of each picture. The relay serves the same pictures to the phone, which
    keeps its copy only when it matches; a PC with other pictures sends its own."""
    v = art_version()
    if _HASHES["v"] != v:
        out = {}
        for f in files:
            try:
                with open(os.path.join(_TEAM_ART, f), "rb") as fh:
                    out[f] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
        _HASHES.update(v=v, h=out)
    return dict(_HASHES["h"])


def room_state() -> Dict[str, Any]:
    """Everything the phone's room needs each time it looks: the helpers, what
    JARVIS is doing, the character and home room picked on the PC, and which
    pictures it is drawn with."""
    from .brand_icons import key_for
    agents = []
    for a in team_now():
        a = dict(a)
        a["prompt"] = str(a.get("prompt") or "")[:600]
        a["preview"] = str(a.get("preview") or "")[:600]
        a["brand"] = key_for(str(a.get("runtime") or ""))
        agents.append(a)
    now = _window("GET", "/api/room/now", timeout=5)
    if "state" not in now:
        from . import activity
        now = {"state": "idle", "waiting": "", "elsewhere": activity.current()}
    try:
        from .web import _load_settings
        s = _load_settings() or {}
    except Exception:
        s = {}
    return {"agents": agents, "main": now, "art": art_version(),
            "look": {"look": s.get("room_look") or "her", "home": s.get("room_home") or "cabin",
                     "wipe": s.get("room_wipe") or "pixel"}}


def story(agent_id: str) -> Dict[str, Any]:
    """One helper's work so far, as the window's panel beside the room shows
    it, each step already said in words."""
    from . import subagents
    from .term_render import _VERB
    got = _window("GET", "/api/team/story?id=" + urllib.parse.quote(agent_id, safe=""), timeout=10)
    if "steps" not in got:
        got = subagents.agent_story(agent_id)
    if not got:
        return {"error": "no such helper"}
    steps = []
    for st in (got.get("steps") or [])[-60:]:
        st = dict(st)
        if st.get("tool"):
            verb = _VERB.get(st["tool"], (str(st["tool"]).replace("_", " ").capitalize(), ""))[0]
            st["w"] = f"{verb} {st.get('what') or ''}".strip()
        if st.get("result") is not None:
            st["result"] = str(st["result"])[:300]
        steps.append(st)
    ch = got.get("changes") or {}
    return {"id": agent_id, "prompt": str(got.get("prompt") or "")[:2000], "by": got.get("by") or "",
            "steps": steps, "final": str(got.get("final") or "")[:3000], "error": got.get("error") or "",
            "ended": got.get("ended"), "changed": len(ch.get("files") or []) if isinstance(ch, dict) else 0}


def context_item(t: Dict[str, Any]) -> Dict[str, Any]:
    """One thing a reply had in mind, as the phone shows it: topic and ago as
    before, with its kind (card, fact, search, chat), id and source chat."""
    return {"topic": t.get("topic") or "", "ago": t.get("ago") or "", "kind": t.get("kind") or "card",
            "id": t.get("id") or "", "where": t.get("where") or "", "open": list(t.get("open") or [])[:3],
            "chat": t.get("chat") or "", "chat_title": t.get("chat_title") or ""}


def ideas() -> Dict[str, Any]:
    """What a new chat on the phone offers: the chats still going on (each
    opens its chat) and the reminder ideas Hearth already wrote, from its cache."""
    out: Dict[str, Any] = {"threads": [], "ideas": []}
    try:
        from . import memory_cards
        out["threads"] = [{"topic": c.get("goal") or "", "chat": c.get("chat") or "", "ago": c.get("ago") or "",
                           "open": (c.get("left") or [])[:2]} for c in memory_cards.open_cards(limit=3) if c.get("chat")]
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
        return {"ok": False, "error": "the Inglen window isn't running on the PC"}
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

class AgentWatch:
    """Which coding-agent sessions on the PC just finished a turn: written to since the
    last look, quiet for QUIET_S since, and ending on the agent's own words (a turn
    still running ends on a step, or on the person's message). Sessions that were
    already there when it started, and ones the phone is driving, are left alone."""
    EVERY_S = 10
    QUIET_S = 20

    def __init__(self):
        self.seen: Optional[Dict[str, int]] = None   # chat -> when it was last written, at the last look
        self.waiting: Dict[str, Dict[str, Any]] = {}  # written to, not quiet yet
        self.told: Dict[str, int] = {}
        self.moved: List[str] = []                    # sessions written to since the last look

    def look(self, rows: List[Dict[str, Any]], now_ms: float, busy: set) -> List[Dict[str, Any]]:
        self.moved = []
        if self.seen is None:
            self.seen = {r["chat"]: r["at"] for r in rows}
            return []
        for r in rows:
            k, at = r["chat"], r["at"]
            before = self.seen.get(k)
            self.seen[k] = at
            if before is not None and at > before:
                self.moved.append(k)
            if k in busy:
                self.waiting.pop(k, None)
            elif before is None or at > before:
                self.waiting[k] = r
        out = []
        for k, r in list(self.waiting.items()):
            if now_ms - r["at"] < self.QUIET_S * 1000:
                continue
            del self.waiting[k]
            if self.told.get(k) == r["at"]:
                continue
            self.told[k] = r["at"]
            note = self._note(r)
            if note:
                out.append(note)
        return out

    @staticmethod
    def _note(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from . import agent_sessions
        got = agent_sessions.read(r["agent"], r["id"]) or {}
        msgs = got.get("messages") or []
        last = msgs[-1] if msgs else None
        if not last or last.get("role") != "assistant":
            return None
        parts = last.get("parts") or []
        what = r.get("title") or r.get("project") or "a session"
        # Named for the agent only ("Codex"), with what it said: these apps change how they
        # stop and ask, and a title like "finished" would be wrong for a turn that asks.
        who = r.get("label") or "An agent"
        if not last.get("error") and parts and "text" not in parts[-1]:
            st = (parts[-1].get("steps") or [{}])[-1]
            if st.get("tool") in agent_sessions.QUESTION_TOOLS and not st.get("out"):
                return {"kind": "ask", "title": who, "body": f"{what}: {st.get('cmd') or 'It has a question for you.'}",
                        "c": r["chat"]}
            return None                            # it ended on a step: still at work, or waiting on a tool
        text = (parts[-1].get("text") if parts else "") or last.get("text") or ""
        return {"kind": "done", "title": who, "body": f"{what}: {' '.join(text.split())[:200]}", "c": r["chat"]}


class Relay(cc.Adapter):
    """Sends go out as encrypted frames on the phone's live connection, or to
    its mailbox on the server when it's away."""
    name, label, max_len = "relay", "Phone", 20000

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.rooms: Dict[str, "Room"] = {}     # chat (phone id) -> room
        self._live_at: Dict[str, float] = {}   # turn -> when its live text last went out
        self._think_at: Dict[str, float] = {}  # turn -> when its reasoning last went out
        self._latest: Dict[str, str] = {}      # "live:<turn>" -> the newest text, for a send that is waiting
        self._later: Dict[str, threading.Timer] = {}
        self.asks: Dict[str, str] = {}         # chat -> the id of the question waiting there
        self._told: List[str] = []             # turns whose finish a notification has said

    def notify(self, chat: str, obj: Dict[str, Any], urgent: bool = False) -> None:
        """A notification for the phone while the app isn't on screen: sealed for
        the phone, handed to the relay, which sends it on by Web Push."""
        room = self.rooms.get(str(chat))
        if room is None:
            return
        # A push carries about 4 KB; a long command in a question must not lose the notification.
        body = " ".join(str(obj.get("body") or "").split())
        obj = dict(obj, body=body[:300] + ("..." if len(body) > 300 else ""), title=str(obj.get("title") or "")[:80])
        fut = asyncio.run_coroutine_threadsafe(room.notice(obj, urgent), self.loop)
        try:
            fut.result(10)
        except Exception:
            pass

    def finished(self, chat: str, title: str, text: str, c: str, t: str) -> None:
        """One notification per finished reply."""
        if not t or t in self._told:
            return
        self._told = (self._told + [t])[-200:]
        self.notify(chat, {"kind": "done", "title": title, "body": " ".join(text.split())[:240], "c": c})

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

    def _call_for(self, chat):
        """The call in progress in this phone's room, when the turn is in its chat."""
        room = self.rooms.get(str(chat))
        call = getattr(room, "call", None) if room is not None else None
        if call is None or call.ended:
            return None
        c = cc.turn_context().get("c", "")
        return call if not c or not call.cid or c == call.cid else None

    def send(self, chat, text, reply_to=None):
        self._turn_done()
        call = self._call_for(chat)
        if call is not None:
            call.done(text)
        mid = self._push(chat, {"type": "text", "text": text})
        ctx = cc.turn_context()
        if ctx.get("t"):
            from . import persona
            self.finished(chat, persona.NAME, text, ctx.get("c", ""), ctx["t"])
        return mid

    def edit(self, chat, msg_id, text):
        return self._push(chat, {"type": "edit", "of": msg_id, "text": text}) is not None

    def ask(self, chat, text):
        call = self._call_for(chat)
        if call is not None:
            call.ask(text)
        # The question's own id: an answer from a notification counts only for it.
        aid, c = secrets.token_hex(8), cc.turn_context().get("c", "")
        self.asks[c or str(chat)] = aid
        mid = self._push(chat, {"type": "ask", "text": text, "ask": aid})
        from . import persona
        self.notify(chat, {"kind": "ask", "title": f"{persona.NAME} asks", "body": text, "c": c, "ask": aid}, urgent=True)
        return mid

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

    step_detail = True                  # steps carry what they ran, and their output follows

    def step(self, chat, words, tool="", site="", cmd=""):
        """One step of the reply as it happens ("Read notes.txt"), with the tool,
        the site it reaches (for that site's icon) and what it ran (for the step's detail)."""
        call = self._call_for(chat)
        if call is not None:
            call.step(words)
        self._push(chat, {"type": "step", "words": words, "tool": tool, "site": site, **({"cmd": cmd} if cmd else {})})

    def step_out(self, chat, out):
        """What the step before it gave back, cut short, for the step's detail on the phone."""
        if out:
            self._push(chat, {"type": "step_out", "out": out})

    def _paced(self, chat, kind: str, text: str, every: float, last: Dict[str, float]) -> None:
        """Send the newest text at most once every `every` seconds; a piece that lands
        inside the wait goes when it ends, so the phone never sits on an old piece."""
        ctx = cc.turn_context()
        t = ctx.get("t", "")
        key = f"{kind}:{t}"
        self._latest[key] = text
        now = time.time()
        wait = every - (now - last.get(t, 0))
        if wait <= 0:
            last[t] = now
            if len(last) > 50:
                last.clear(); last[t] = now
            self._push(chat, {"type": kind, "text": text})
            return
        if key in self._later:
            return                           # one send is already waiting; it takes the newest text

        def go():
            self._later.pop(key, None)
            newest = self._latest.get(key)
            if newest is None:
                return
            last[t] = time.time()
            prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), ctx
            try:
                self._push(chat, {"type": kind, "text": newest})
            finally:
                cc._TURN.ctx = prev
        timer = threading.Timer(wait, go)
        timer.daemon = True
        self._later[key] = timer
        timer.start()

    def _turn_done(self) -> None:
        """The finished reply is on its way: nothing paced for this turn is sent after it."""
        t = cc.turn_context().get("t", "")
        for kind in ("live", "think"):
            key = f"{kind}:{t}"
            timer = self._later.pop(key, None)
            if timer is not None:
                timer.cancel()
            self._latest.pop(key, None)

    def think(self, chat, text):
        """The reasoning so far, four times a second, folded on the phone like the window's."""
        self._paced(chat, "think", text[-4000:], THINK_EVERY, self._think_at)

    def heard(self, chat, text, cid=""):
        """What a voice note said, so the phone shows the words instead of "Voice note"."""
        self._push(chat, {"type": "heard", "text": text, **({"c": cid} if cid else {})})

    def step_sites(self, chat, sites):
        """The sites a search just found, for the step before it."""
        if sites:
            self._push(chat, {"type": "sites", "sites": list(sites)[:6]})

    def live(self, chat, text):
        """The reply so far, a few times a second; the finished one follows as text."""
        call = self._call_for(chat)
        if call is not None:
            call.live(text)                 # every piece, so the voice starts at the first clause
        self._paced(chat, "live", text, LIVE_EVERY, self._live_at)


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
        self.seen_boxes: set = set()          # answers from notifications already taken
        self.call = None                      # a voice call from the phone, while one is on

    @property
    def chat(self) -> str:
        return phone_id(self.phone) if self.phone else "pending"

    def box_key(self) -> bytes:
        return R.mailbox_key(R.dh(self.pc, self.phone), self.room)

    async def notice(self, obj: Dict[str, Any], urgent: bool = False) -> None:
        if self.ws is None or not self.phone:
            return
        box = R.seal_box(self.box_key(), obj)
        await self.ws.send(json.dumps({"t": "push", "iv": box["iv"], "c": box["c"], "urgent": urgent}))

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
                    print("[relay] the relay needs Inglen Plus for this PC; stopping.", flush=True)
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
        if t == "box" and self.phone:          # Allow or Deny tapped on a notification
            m = R.open_box(self.box_key(), f, self.seen_boxes)
            if m and m.get("type") == "answer":
                await asyncio.to_thread(self.bridge.on_answer, self, m)
            return
        if t == "msg" and self.chan is not None:
            m = self.chan.open(f)
            m = self.joiner.add(m) if m else None
            if m and str(m.get("type") or "").startswith("call."):
                self.on_call(m)
                return
            if m and m.get("type") == "req":
                # Lookups run side by side, so a slow one (icons from the web)
                # never holds up a switch the person is waiting on.
                task = asyncio.ensure_future(asyncio.to_thread(self.bridge.on_message, self, m))
                self.lookups.add(task)
                task.add_done_callback(self.lookups.discard)
            elif m:
                await asyncio.to_thread(self.bridge.on_message, self, m)

    def on_call(self, m: Dict[str, Any]) -> None:
        """A call from the phone: its audio frames, its start and its end."""
        kind = m.get("type")
        if kind == "call.up":
            if self.call is not None:
                self.call.up(str(m.get("data") or ""), int(m.get("rate") or 16000))
            return
        if kind == "call.start":
            if self.call is not None and not self.call.ended:
                self.call.end()
            from .call import Call
            adapter, chat = self.bridge.adapter, self.chat
            cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80]

            def send(obj):
                adapter._push(chat, obj)

            def say(text):
                return self.bridge.hub.receive(chat, "phone", text, name="Phone", cid=cid, spoken=True)

            self.call = Call(send, say, cid=cid)
            threading.Thread(target=self.call.start, daemon=True, name="relay-call").start()
            return
        if kind == "call.end" and self.call is not None:
            call, self.call = self.call, None
            threading.Thread(target=call.end, daemon=True).start()

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
        self._mirrored: Dict[str, str] = {}                 # a window turn -> its text so far, for the phones
        self._said: Dict[str, List[str]] = {}
        self.agent_runs: Dict[str, Dict[str, Any]] = {}     # session chat -> its run, while an agent answers

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
        from . import agent_sessions
        if agent_sessions.parse_chat(str(m.get("chat") or "")):
            self.agent_say(room, m)
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
        edit = m.get("edit") if isinstance(m.get("edit"), dict) else None
        if edit and m.get("chat"):
            cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat")))[:80]
            with self.hub._lock:
                busy = cid in self.hub._busy
            if busy:
                # Cutting the chat under a turn still running would tangle the two.
                self.adapter._push(room.chat, {"type": "text", "c": cid,
                                               "text": "I'm still answering in this chat; edit once I'm done."})
                return
            drop_from(cid, str(edit.get("text") or ""), int(edit.get("back") or 0))
        self.hub.receive(room.chat, "phone", str(m.get("text") or ""), name="Phone", voice=voice, files=files,
                         cid=str(m.get("chat") or ""), regen=bool(m.get("regen")))

    def on_answer(self, room: Room, m: Dict[str, Any]) -> None:
        """Allow or Deny from a notification. It answers only the question it was
        shown for, and only while that question still waits: an old answer
        played again, or one for a question answered in the app, does nothing."""
        cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("c") or ""))[:80]
        key, aid, yes = cid or room.chat, str(m.get("ask") or ""), bool(m.get("yes"))
        if not aid or self.adapter.asks.get(key) != aid:
            return
        self.adapter.asks.pop(key, None)
        run = self.agent_runs.get(cid)
        if run is not None:
            q = run.get("q")
            if q is None or q.is_set():
                return
            run["answer"] = yes
            q.set()
        else:
            # Set as the answer in one step: sent as a message, it could start a turn of its own
            # if the question were answered in the app meanwhile.
            with self.hub._lock:
                busy = self.hub._busy.get(cid) or self.hub._busy.get(room.chat) or {}
                q = busy.get("question")
                if q is None or q.is_set():
                    return
                busy["answer"] = yes
                q.set()
        # The app shows the question answered when it opens.
        fut = asyncio.run_coroutine_threadsafe(
            room.deliver({"type": "asked", "ask": aid, "yes": yes, "c": cid, "id": secrets.token_hex(6)}), self.loop)
        try:
            fut.result(15)
        except Exception:
            pass

    def agent_say(self, room: Room, m: Dict[str, Any]) -> None:
        """A message in one of the coding agents' sessions: that agent carries the
        session on, and the phone sees its steps and reply as they come. A yes or
        no while it waits on a question answers the question."""
        from . import agent_sessions
        cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80]
        text = str(m.get("text") or "").strip()
        turn = secrets.token_hex(5)

        def push(obj: Dict[str, Any]) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                room.deliver(dict(obj, id=secrets.token_hex(6), c=cid, t=turn)), self.loop)
            try:
                fut.result(15)
            except Exception:
                pass

        run = self.agent_runs.get(cid)
        if run is not None:
            q = run.get("q")
            if q is not None and not q.is_set():
                run["answer"] = bool(cc._YES.match(text))
                self.adapter.asks.pop(cid, None)
                q.set()
            else:
                push({"type": "text", "text": "It's still working on your last message; send this once it's done."})
            return
        if not text:
            return
        run = self.agent_runs[cid] = {"q": None, "answer": False}
        sent = {"live": 0.0, "think": 0.0}
        thought: List[str] = []

        def emit(kind: str, **f: Any) -> None:
            now = time.time()
            if kind == "step":
                push({"type": "step", "words": f.get("words") or "", "tool": f.get("tool") or "",
                      **({"cmd": f["cmd"]} if f.get("cmd") else {})})
            elif kind == "step_out" and f.get("out"):
                push({"type": "step_out", "out": f["out"]})
            elif kind == "live" and now - sent["live"] >= LIVE_EVERY:
                sent["live"] = now
                push({"type": "live", "text": f.get("text") or ""})
            elif kind == "think":
                thought.append(str(f.get("text") or ""))
                if now - sent["think"] >= THINK_EVERY:
                    sent["think"] = now
                    push({"type": "think", "text": "".join(thought)[-4000:]})
            elif kind == "text":
                push({"type": "text", "text": f.get("text") or ""})
                self.adapter.finished(room.chat, name, f.get("text") or "", cid, turn)
            elif kind == "error":
                push({"type": "text", "text": f.get("message") or "It stopped.", "error": True})

        name = agent_sessions.label((agent_sessions.parse_chat(cid) or ("", ""))[0])

        def ask(question: str) -> bool:
            run["q"], run["answer"] = threading.Event(), False
            aid = secrets.token_hex(8)
            self.adapter.asks[cid] = aid
            push({"type": "ask", "text": question, "ask": aid})
            self.adapter.notify(room.chat, {"kind": "ask", "title": f"{name} asks", "body": question, "c": cid, "ask": aid},
                                urgent=True)
            run["q"].wait(cc.ASK_TIMEOUT_S)
            run["q"] = None
            return bool(run["answer"])

        def go() -> None:
            try:
                push({"type": "typing"})
                asyncio.run(agent_sessions.say(cid, text, emit, ask))
            finally:
                self.agent_runs.pop(cid, None)

        threading.Thread(target=go, daemon=True, name="relay-agent-session").start()

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
                    if phone:
                        # Reachable while it's away too: its mailbox, and notifications.
                        self.adapter.rooms.setdefault(r.chat, r)
                elif phone and r.phone is None:          # just confirmed on the PC
                    r.phone = phone
                    self.adapter.rooms.setdefault(r.chat, r)
                    if confirmed == room:
                        await r.tell_paired()
            for room in list(self.rooms):
                if room not in want:
                    t = tasks.pop(room, None)
                    if t:
                        t.cancel()
                    self.rooms.pop(room, None)
            await asyncio.sleep(1.0)

    def watch_agents(self) -> None:
        """Tell the phone when a coding agent finishes on the PC, as Shed does: Codex
        or Claude Code run in their own window, not through the phone, still reach it."""
        watch = AgentWatch()
        while not self.stopping:
            time.sleep(AgentWatch.EVERY_S)
            if not self.adapter.rooms:
                continue
            try:
                from . import agent_sessions
                notes = watch.look(agent_sessions.listing(60), time.time() * 1000, set(self.agent_runs))
            except Exception:
                continue
            for n in notes:
                for chat in list(self.adapter.rooms):     # each paired phone
                    self.adapter.notify(chat, n)
            # A session open on a phone shows what its agent wrote since, without reopening it.
            for c in watch.moved:
                if c in self.agent_runs:
                    continue
                prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
                try:
                    for chat in list(self.adapter.rooms):
                        self.adapter._push(chat, {"type": "changed", "what": "chat", "c": c})
                finally:
                    cc._TURN.ctx = prev

    # Settings a phone shows: a change on the PC redraws its brain chip and modes at once.
    _PHONE_SETTINGS = re.compile(r"^(llm_|agent_|brain|approval_mode|think_|preferred_model|ui_theme|ui_accent|theme)")

    def follow_window(self) -> None:
        """Pass what changes on the PC (live_bus) to every paired phone as it happens:
        a chat changed, the brain or modes changed, and a reply the window is writing."""
        if not WINDOW_URL:
            return
        from . import live_bus
        live_bus.listen(WINDOW_URL, self.on_window_event, stop=lambda: self.stopping)

    def on_window_event(self, ev: Dict[str, Any]) -> None:
        phones = list(self.adapter.rooms)
        if not phones:
            return
        kind = ev.get("kind")
        out: Optional[Dict[str, Any]] = None
        if kind == "settings" and any(self._PHONE_SETTINGS.match(str(k)) for k in ev.get("keys") or []):
            _BRAIN_CACHE.clear()                  # the phone's next look at the brain reads it fresh
            out = {"type": "changed", "what": "brain"}
        elif kind == "chat" and ev.get("chat"):
            out = {"type": "changed", "what": "chat", "c": ev["chat"], **({"gone": True} if ev.get("gone") else {})}
        elif kind == "turn" and ev.get("from") == "window" and ev.get("chat"):
            self._mirror(phones, ev)
            return
        if out:
            prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
            try:
                for chat in phones:
                    self.adapter._push(chat, out)
            finally:
                cc._TURN.ctx = prev

    def _mirror(self, phones: List[str], ev: Dict[str, Any]) -> None:
        """A reply the window is writing, shown on the phones in that chat as it comes."""
        t, cid, kind = str(ev.get("turn") or ""), ev["chat"], ev.get("type")
        prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), {"c": cid, "t": t}
        try:
            text = self._mirrored.get(t, "")
            if kind == "start":
                self._mirrored[t] = ""
                for chat in phones:
                    self.adapter._push(chat, {"type": "user", "text": ev.get("text") or "", "via": "window"})
            elif kind == "chunk":
                self._mirrored[t] = text + str(ev.get("text") or "")
                for chat in phones:
                    self.adapter.live(chat, self._mirrored[t])
            elif kind == "think":
                for chat in phones:
                    self.adapter.think(chat, str(ev.get("text") or ""))
            elif kind == "step":
                for chat in phones:
                    self.adapter._push(chat, {"type": "step", "words": ev.get("words") or "", "tool": ev.get("tool") or "",
                                              **({"cmd": ev["cmd"]} if ev.get("cmd") else {})})
            elif kind == "step_out":
                for chat in phones:
                    self.adapter._push(chat, {"type": "step_out", "out": ev.get("out") or ""})
            elif kind == "said":
                # One turn can say something, work, then say more: the phones get the
                # whole reply once, at the end, as the window keeps it.
                said = self._said.setdefault(t, [])
                if ev.get("text"):
                    said.append(str(ev["text"]))
                self._mirrored[t] = "\n\n".join(said)
            elif kind == "error":
                for chat in phones:
                    self.adapter._push(chat, {"type": "text", "text": ev.get("text") or "", "error": True})
            elif kind == "end":
                final = "\n\n".join(self._said.pop(t, [])) or self._mirrored.get(t, "")
                self._mirrored.pop(t, None)
                self.adapter._turn_done()
                if final.strip():
                    for chat in phones:
                        self.adapter._push(chat, {"type": "text", "text": final})
            if len(self._mirrored) > 50:
                self._mirrored.clear()
                self._said.clear()
        finally:
            cc._TURN.ctx = prev

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        threading.Thread(target=self.watch_agents, daemon=True, name="relay-agent-watch").start()
        threading.Thread(target=self.follow_window, daemon=True, name="relay-follow-window").start()
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
