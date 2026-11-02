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
import queue
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
DEFAULT_RELAY = os.environ.get("HEARTH_RELAY_URL") or "https://app.inglen.app"
# The relay's first address, still served by the same worker: a saved one moves to the new name.
# Rooms are named from the keys alone, so a paired phone keeps working across the move.
OLD_RELAYS = ("https://hearth-relay.inglen.workers.dev",)
PAIR_LIFE_S = 600
_LOCK = threading.Lock()


# ---------------------------------------------------------------- state

def _read() -> Dict[str, Any]:
    from . import keystore
    return keystore.read_json(STATE_FILE, keystore.FIELDS["relay.json"])


def _write(d: Dict[str, Any]) -> None:
    from . import keystore
    keystore.write_json(STATE_FILE, d, keystore.FIELDS["relay.json"], indent=1)


def relay_url() -> str:
    try:
        from .web import _load_settings
        u = (_load_settings().get("relay_url") or "").strip()
    except Exception:
        u = ""
    u = (u or DEFAULT_RELAY).rstrip("/")
    return "https://app.inglen.app" if u in OLD_RELAYS else u


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


# ---------------------------------------------------------------- what happened, where the rest is

LIVE_FILE = os.path.join(os.path.expanduser("~"), ".hearth", "relay_live.json")
_LIVE_LOCK = threading.Lock()


def _log(msg: str) -> None:
    """One timestamped line; started from the window it goes to logs/relay_bridge.log,
    which the window's phone logs show beside the other chat apps'."""
    try:
        print(time.strftime("%H:%M:%S ") + "[relay] " + msg, flush=True)
    except Exception:
        pass


def _activity(what: str, **fields: Any) -> None:
    """A line in Inglen's activity record, where every surface's tool calls already go."""
    try:
        from .tools import _log_activity
        _log_activity("phone", what=what, **fields)
    except Exception:
        pass


def _seen(chat: str, on: bool) -> None:
    """Whether a phone is connected now, and since when: Settings > Plus shows it."""
    with _LIVE_LOCK:
        try:
            with open(LIVE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            d = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            d = {}
        phones_now = d.get("phones") if isinstance(d.get("phones"), dict) else {}
        if chat == "*":
            phones_now = {k: dict(v, on=False) for k, v in phones_now.items()}
        else:
            phones_now[chat] = {"on": on, "at": time.time()}
        d = {"phones": phones_now, "bridge": time.time(), "pid": os.getpid()}
        try:
            os.makedirs(os.path.dirname(LIVE_FILE), exist_ok=True)
            tmp = LIVE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, LIVE_FILE)
        except OSError:
            pass


def live() -> Dict[str, Any]:
    """Each paired phone's connection as the bridge last saw it: {id: {on, at}}."""
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("phones") or {} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def bridge_up() -> bool:
    """The phone bridge is running on this PC (it writes its pid as it starts)."""
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            pid = int((json.load(f) or {}).get("pid") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    if pid == os.getpid():
        return True
    from .bridge_status import _alive
    return bool(pid) and _alive(pid)


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
CALL_AWAY_S = 20                    # how long a call waits for a phone that dropped off
DONE_SETTLE_S = 1.5                 # a reply's notification waits this long for a newer send in its turn
PART = 150_000
ICONS_WAIT_S = 8.0              # an icons answer waits this long; the rest go in "later"


def plain_text(s: Any) -> str:
    """Markdown as a notification shows it: the words alone, on one line."""
    t = str(s or "")
    t = re.sub(r"```[^\n]*\n?(.*?)(```|$)", r"\1", t, flags=re.S)        # code blocks: their code
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)                      # pictures: their alt text
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                       # links: their words
    t = re.sub(r"(?m)^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", "", t)  # headings, quotes, list marks
    t = re.sub(r"(?m)^\s*\|?\s*:?-{3,}.*$", "", t)                       # table rules
    t = t.replace("|", " ")
    t = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])", r"\1", t)
    t = re.sub(r"`+([^`]*)`+", r"\1", t)
    t = re.sub(r"<[^>\n]{1,40}>", "", t)                                 # stray tags
    return " ".join(t.split())


def note_body(s: Any, limit: int = 200) -> str:
    """One or two plain sentences for a notification's body."""
    t = plain_text(s)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", t)
    out = parts[0]
    if len(parts) > 1 and len(out) + 1 + len(parts[1]) <= limit:
        out += " " + parts[1]
    if len(out) > limit:
        cut = out[:limit - 1]
        out = (cut.rsplit(" ", 1)[0] if " " in cut[limit // 2:] else cut).rstrip(" ,;:-") + "…"
    return out


def stopped_note(name: str, error: Any) -> str:
    """An agent's error as a notification says it: "Claude Code stopped: its usage limit
    resets at 5:30 pm", not the agent's own banner ("You've hit your 5-hour limit · resets 5:30pm")."""
    t = plain_text(error)
    if re.search(r"\blimit\b", t, re.I):
        m = re.search(r"(?:resets|try again)(?:\s+(?:at|on))?\s+([^·(]+?)\s*(?:\(|·|\.(?:\s|$)|$)", t, re.I)
        when = m.group(1).strip(" .,") if m else ""
        when = re.sub(r"(\d)\s*([ap])\.?m\.?\b", lambda g: f"{g.group(1)} {g.group(2).lower()}m", when, flags=re.I)
        kind = re.search(r"\b(weekly|daily|monthly|hourly|\d+[- ]hours?|session)\s+(?:usage\s+)?limit\b", t, re.I)
        what = f"{kind.group(1).lower().replace(' ', '-')} limit" if kind else "usage limit"
        at = "on" if re.match(r"(?i)(mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2}[/-])", when) else "at"
        return f"{name} stopped: its {what} " + (f"resets {at} {when}" if when else "was reached")
    t = re.sub(r"^(?:API Error|Error)\s*:?\s*", "", t, flags=re.I)
    if not t:
        return f"{name} stopped."
    return f"{name} stopped: " + (t[0].lower() + t[1:] if t[1:2].islower() else t)


_ASKED = re.compile(r"^(?:Before I go on: may I |[\w .-]{1,40} asks: may it )", re.I)
_RUNS = re.compile(r"^(run|start) `(.+)`\?\s*(.*)$", re.S | re.I)


def ask_parts(who: str, text: str, cmd: str = "") -> Dict[str, str]:
    """A question as the card and the notification say it: who wants what, in plain
    words, and the command apart from them."""
    t = _ASKED.sub("", str(text or "").strip())
    m = _RUNS.match(t)
    if m:
        cmd, why = cmd or m.group(2), m.group(3)
        head = f"{who} wants to {m.group(1).lower()} a command"
    else:
        what, _, why = t.partition("?")
        what = what.strip()
        head = f"{who} wants to {what[:1].lower() + what[1:]}" if what else f"{who} asks"
    return {"who": who, "head": plain_text(head), "cmd": cmd.strip(), "why": plain_text(why)}


def ask_note(p: Dict[str, str]) -> Dict[str, str]:
    """The notification for a question: who wants what on top, the command (or the reason) under it."""
    body = p["cmd"] or p["why"]
    if len(p["head"]) > 60 and not body:
        return {"title": f"{p['who']} asks", "body": p["head"]}
    return {"title": p["head"], "body": body}


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

def _pictures(got: Dict[str, Any]) -> Dict[str, Any]:
    """A picture a step looked at (Codex's view_image, Claude Code reading a .png) as a
    card the phone shows and opens; the path alone showed only "Looked at an image"."""
    from . import relay_media
    for msg in got.get("messages") or []:
        for st in list(msg.get("steps") or []) + [x for p in msg.get("parts") or [] for x in p.get("steps") or []]:
            path = st.pop("img", "") if isinstance(st, dict) else ""
            if path and "file" not in st:
                c = relay_media.card(path)
                if c is not None:
                    st["file"] = c
        if msg.get("role") == "assistant" and not msg.get("error"):
            cards = session_files(msg.get("text") or "", got.get("cwd") or "")
            if cards:
                msg["files"] = cards
        elif msg.get("role") == "user" and msg.get("files"):
            # What the phone sent with the message, as cards with it (a file since removed drops out).
            cards = [relay_media.card(p) for p in msg["files"] if isinstance(p, str)]
            msg["files"] = [c for c in cards if c is not None]
            if not msg["files"]:
                del msg["files"]
    return got


def session_files(text: str, cwd: str) -> List[Dict[str, Any]]:
    """Cards for the files an agent's reply names (a path, or a markdown link, relative to its folder):
    the ones the Files tab could serve, or in the session's own project folder."""
    from . import relay_media
    from .bridge_status import files_in
    cards = []
    for p in files_in(text, relay_media.MAX_FILE, base=cwd, also=(cwd,) if cwd else ()):
        c = relay_media.card(p)
        if c is not None:
            cards.append(c)
    return cards


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
    if what == "memory":
        return memory_view(m)
    if what == "voice":
        return voice_view(m)
    if what == "commands":
        # The slash commands the phone offers when "/" is typed (remote_commands answers them).
        from . import remote_commands
        return {"commands": remote_commands.listing("relay")}
    if what == "media":
        # One part of a file the PC carded: the phone fetches a file when it is opened.
        from . import relay_media
        return relay_media.chunk(str(m.get("media") or ""), int(m.get("offset") or 0), int(m.get("length") or 0))
    if what == "call_me":
        from . import call_me
        return call_me.view()
    if what == "changes":
        # File changes the agent made, newest first, for the phone's Recent changes (hearth/undo.py).
        from . import undo as _undo
        return {"changes": _undo.recent(30)}
    if what == "undo":
        from . import undo as _undo
        return _undo.undo(str(m.get("id") or ""))
    if what == "about":
        # The phone's About: the version of Inglen on this PC.
        from .updater import HEARTH_VERSION
        return {"version": HEARTH_VERSION}
    if what == "agent_catalog":
        from .agent_launch import catalog
        return catalog()
    if what == "agent_folders":
        from .agent_launch import folders
        return folders(str(m.get("path") or ""))
    if what == "agent_subagents":
        # The helpers a coding agent's session started (Codex's Subagents panel).
        from . import agent_sessions
        return {"chat": str(m.get("chat") or ""), "subagents": agent_sessions.subagents(str(m.get("chat") or ""))}
    if what == "agent_sessions":
        # The coding agents' own sessions on this PC, newest first.
        from . import agent_sessions
        from .brand_icons import key_for
        rows = agent_sessions.listing(int(m.get("limit") or 120))
        if m.get("query"):
            # What they searched for, inside the conversations: the sessions that mention it and where.
            return {"hits": agent_sessions.search(str(m["query"]), rows)}
        return {"sessions": [dict(r, brand=key_for(r["agent"])) for r in rows]}
    if what == "open":
        from .term_render import tool_words
        cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80]
        from . import agent_sessions
        if agent_sessions.parse_chat(cid):
            return _pictures(agent_sessions.transcript(cid, int(m.get("last") or 60)))
        d = chat_sync.load(cid) or {}
        msgs = []
        for x in d.get("messages") or []:
            role = x.get("role")
            if role not in ("user", "assistant"):
                continue
            events = [e for e in x.get("events") or [] if isinstance(e, dict)]
            steps = []
            for e in events:
                if e.get("kind") == "ask" and role == "assistant":
                    # A question the turn asked, answered: the folded line where it was asked.
                    msgs.append({"role": "assistant", "ask": True, "text": str(e.get("text") or ""),
                                 "answer": bool(e.get("answer")), "at": e.get("at") or x.get("at") or 0})
                    continue
                if e.get("kind") != "tool" or e.get("name") in cc.QUIET_STEPS:
                    continue
                args = e.get("args") if isinstance(e.get("args"), dict) else {}
                if e.get("name") == "web_search" and not args.get("query") and e.get("result"):
                    # Codex steps saved before their query was read kept it as the result.
                    args = {"query": str(e.get("result"))[:90]}
                said = e.get("words") if isinstance(e.get("words"), dict) else {}
                step = {"w": said.get("done") or tool_words(e.get("name") or "", args), "tool": e.get("name") or "",
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
            # The files a message carried, as cards the phone opens from this PC: what the person sent
            # (kept where it was saved) and what a reply named, the way the reply sent them.
            files = []
            from . import relay_media
            from .bridge_status import files_in
            sent = cc.sent_files(dict(x, content=text)) if role == "user" else []
            for p in (sent if role == "user" else files_in(text, relay_media.MAX_FILE)):
                c = relay_media.card(p)
                if c:
                    files.append(c)
            # What they wrote, without the note telling the model about the files: the files show as cards.
            if role == "user":
                text = cc.said_text(dict(x, content=text))
            failed = next((str(e.get("text") or "") for e in events if e.get("kind") == "error"), "")
            if failed and not text.strip():
                msgs.append({"role": "assistant", "text": failed, "at": x.get("at") or 0, "steps": steps, "error": True})
                continue
            if text or steps or files:
                msgs.append({"role": role, "text": text, "at": x.get("at") or 0, "steps": steps, "files": files,
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
        # Fetched side by side; whatever isn't in within ICONS_WAIT_S is named in "later"
        # and keeps fetching here (it is kept on disk), so one slow site can't make the
        # phone's ask time out and lose the whole batch.
        from concurrent.futures import ThreadPoolExecutor, wait
        from .web import _favicon
        from .brand_icons import brand_icon
        jobs = [("brand:" + k, brand_icon, k) for k in [str(x) for x in (m.get("keys") or [])][:40]]
        jobs += [(d, _favicon, d) for d in [str(x) for x in (m.get("sites") or [])][:24]]
        pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="icons")
        futs = {pool.submit(fn, arg): name for name, fn, arg in jobs}
        done, _ = wait(futs, timeout=ICONS_WAIT_S)
        pool.shutdown(wait=False)
        out, later = {}, []
        for f, name in futs.items():
            if f not in done:
                later.append(name)
                continue
            try:
                icon = f.result()
            except Exception:
                icon = None
            if icon:
                out[name] = f"data:{icon[1]};base64,{base64.b64encode(icon[0]).decode()}"
        return {"icons": out, "later": later}
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
        from .clock import style
        return {"theme": s.get("ui_theme") or "", "accent": s.get("ui_accent") or "", "clock": style(s)}
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
        # What the person wrote, without the note about files they attached.
        return " ".join(cc.said_text(dict(x, content=chat_sync._text(x.get("content")))).split())

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
    from .step_words import lead
    got = _window("GET", "/api/team/story?id=" + urllib.parse.quote(agent_id, safe=""), timeout=10)
    if "steps" not in got:
        got = subagents.agent_story(agent_id)
    if not got:
        return {"error": "no such helper"}
    steps = []
    for st in (got.get("steps") or [])[-60:]:
        st = dict(st)
        if st.get("tool"):
            st["w"] = f"{lead(st['tool'], st.get('result') is not None)} {st.get('what') or ''}".strip()
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
    """What a new chat on the phone offers: the chats still going on and the
    reminder ideas, as kept (hearth/offers.py; the phone picks its rows from them)."""
    from . import offers
    return offers.sources()


# The brain and the settings the phone can change go through the window's own
# routes (its address is handed to this bridge when the window starts it), so a
# switch from the phone is the same switch as one made in the window.
WINDOW_URL = os.environ.get("HEARTH_WINDOW_URL", "").rstrip("/")
_SETTABLE = {"approval_mode": ("ask", "edits", "auto"), "think_default": (True, False), "think_level": None,
             "gen_stats": (True, False),
             # The look, as the window's Appearance writes it: either side follows the other.
             "ui_theme": ("classic", "dark", "light", "system"),
             "ui_accent": ("ember", "sage", "ocean", "rose", "violet", "mono"),
             "clock_style": ("auto", "12", "24")}


def _front(raw: str) -> tuple:
    """A memory file's front matter and body."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", raw or "", re.S)
    fm: Dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if _ and k.strip():
                fm[k.strip()] = v.strip()
    return fm, (raw[m.end():] if m else raw or "").strip()


def voice_view(m: Dict[str, Any]) -> Dict[str, Any]:
    """The PC's voice (Settings, Voice, the one calls and Read aloud use), and the ones it has.
    Picked from the phone, it is saved through the window, which applies it and tells the window."""
    from . import voice as _voice
    from .call import voices
    pick = str(m.get("voice") or "")
    if pick:
        if pick not in _voice.list_voices():
            return {"ok": False, "error": "That voice isn't on the PC"}
        r = _window("POST", "/api/settings", {"voice_name": pick})
        if r.get("ok") is False:
            return r
    settings = _window("GET", "/api/settings")
    return {"voice": str(settings.get("voice_name") or _voice.current_voice()), "voices": voices()}


def memory_view(m: Dict[str, Any]) -> Dict[str, Any]:
    """The Memory tab, for the phone: what is saved (who saved it, from which chat) and
    where chats stand ("Going on"), with forget and done, as the window has them."""
    from . import chat_sync, memory, memory_cards
    action = str(m.get("action") or "")
    if action == "forget":                     # a saved fact
        from .tools import execute_tool
        out = str(execute_tool("memory_forget", {"title": str(m.get("name") or "")[:200]}) or "")
        if out.lstrip().lower().startswith(("error", "no memory found")):
            return {"ok": False, "error": out.split(":", 1)[-1].strip() if ":" in out else out}
        return {"ok": True}
    if action in ("done", "drop"):             # a "Going on" card
        r = memory_cards.act("card", str(m.get("chat") or ""), "done" if action == "done" else "forget")
        return {"ok": bool(r.get("ok")), **({"error": r.get("error")} if r.get("error") else {})}
    facts = []
    titles: Dict[str, str] = {}
    mem_dir = memory.MEM_DIR
    for fn in sorted(os.listdir(mem_dir)) if os.path.isdir(mem_dir) else []:
        if not fn.endswith(".md") or fn == "MEMORY.md":
            continue
        try:
            with open(os.path.join(mem_dir, fn), encoding="utf-8") as f:
                fm, body = _front(f.read(60000))
        except OSError:
            continue
        desc = fm.get("description", "")
        text = body if body and body.rstrip(".") != desc.rstrip(".") else ""
        src = fm.get("source_chat", "")
        if src and src not in titles:
            titles[src] = ((chat_sync.load(src) or {}).get("title") or "") if re.fullmatch(r"[\w-]{1,80}", src) else ""
        # The whole of it for the phone to open, as the window's Memory tab shows it.
        facts.append({"name": fm.get("name") or fn[:-3], "text": desc or text[:300], "more": text[:20000],
                      "type": fm.get("type", ""), "noticed": "auto-extracted" in fm.get("tags", ""),
                      "updated": fm.get("updated", ""), "chat": src if titles.get(src) else "",
                      "chat_title": titles.get(src, "")})
    facts.sort(key=lambda x: x["updated"], reverse=True)
    cards = [{"chat": c.get("chat") or "", "goal": c.get("goal") or "", "ago": c.get("ago") or "",
              "title": c.get("title") or "", "left": (c.get("left") or [])[:3]}
             for c in memory_cards.everything() if c.get("status") == "open"]
    return {"facts": facts, "cards": cards}


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
                "think_default": bool(settings.get("think_default")), "think_level": settings.get("think_level") or "",
                # The window's "stats under replies" (on unless turned off there); the phone shows them only when
                # it was turned on on purpose, so it gets the setting as it is, unset included.
                "gen_stats": settings.get("gen_stats")}
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
    EVERY_S = 4             # a session open on the phone shows new steps within this
    QUIET_S = 20

    def __init__(self):
        self.seen: Optional[Dict[str, int]] = None   # chat -> when it was last written, at the last look
        self.waiting: Dict[str, Dict[str, Any]] = {}  # written to, not quiet yet
        self.told: Dict[str, int] = {}
        self.moved: List[str] = []                    # sessions written to since the last look
        self.quiet: List[str] = []                    # sessions that went quiet at this look

    def look(self, rows: List[Dict[str, Any]], now_ms: float, busy: set) -> List[Dict[str, Any]]:
        self.moved, self.quiet = [], []
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
            self.quiet.append(k)
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
        what = " ".join(str(r.get("title") or r.get("project") or "").split())
        what = what[:40].rstrip() + ("..." if len(what) > 40 else "")
        # Named for the agent ("Codex") and its session, with what it said last as the body: a
        # lock screen shows one line of body, and that line is the news. A title like "finished"
        # would be wrong for a turn that asks, and these apps change how they stop and ask.
        who = (r.get("label") or "An agent") + (f" · {what}" if what else "")
        if not last.get("error") and parts and "text" not in parts[-1]:
            st = (parts[-1].get("steps") or [{}])[-1]
            if st.get("tool") in agent_sessions.QUESTION_TOOLS and not st.get("out"):
                return {"kind": "ask", "title": who, "body": st.get("cmd") or "It has a question for you.",
                        "c": r["chat"]}
            return None                            # it ended on a step: still at work, or waiting on a tool
        text = (parts[-1].get("text") if parts else "") or last.get("text") or ""
        if not last.get("error") and NO_REPLY.match(text.strip()):
            return None                            # a turn with nothing to say (Claude Code's "No response requested.")
        if last.get("error"):
            text = stopped_note(r.get("label") or "The agent", text)
        return {"kind": "done", "title": who, "body": " ".join(text.split())[:240], "c": r["chat"],
                **({"stopped": True} if last.get("error") else {})}

    @staticmethod
    def worth_a_call(n: Dict[str, Any]) -> bool:
        """A finished turn is worth ringing about when it has news: a question, an error other than
        a usage limit (a notification says that), or a reply longer than an "OK"."""
        if n.get("kind") == "ask":
            return True
        body = str(n.get("body") or "").strip()
        if n.get("stopped"):
            return not re.search(r"stopped: its [\w-]+ limit", body)
        return len(body) >= 24 and not TRIVIAL.match(body)


NO_REPLY = re.compile(r"^(?:no response requested\.?|\(no content\)|)$", re.I)
TRIVIAL = re.compile(r"^(?:ok(?:ay)?|done|sure|yes|no|thanks?(?: you)?|got it|noted|all set)\W*$", re.I)
STRETCH_S = 1800           # a session that rang does not ring again for this long (one stretch of work)


CALL_NOTE = ("This is a voice call from their phone: your reply is spoken aloud as you write it, so keep it "
             "short and conversational, with no lists, tables, links or code. When they say goodbye, say a short "
             "goodbye and then call end_session, which hangs up the call.")


CALL_QUIET_S = 30          # nothing from the phone this long (no mic, no heartbeat): the call ends


class Relay(cc.Adapter):
    """Sends go out as encrypted frames on the phone's live connection, or to
    its mailbox on the server when it's away."""
    name, label, max_len = "relay", "Phone", 20000
    max_file = 100 * 1024 * 1024           # sent as a card and opened from the PC, so no app limit applies

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.rooms: Dict[str, "Room"] = {}     # chat (phone id) -> room
        self._live_at: Dict[str, float] = {}   # turn -> when its live text last went out
        self._think_at: Dict[str, float] = {}  # turn -> when its reasoning last went out
        self._latest: Dict[str, str] = {}      # "live:<turn>" -> the newest text, for a send that is waiting
        self._later: Dict[str, threading.Timer] = {}
        self.asks: Dict[str, str] = {}         # chat -> the id of the question waiting there
        self.asked: Dict[str, Dict[str, Any]] = {}  # question id -> when, where and what it asked
        self._told: List[str] = []             # turns whose finish a notification has said
        self._done_later: Dict[str, threading.Timer] = {}   # turn -> its notification, waiting on a newer send
        self._done_lock = threading.Lock()
        self.hub: Optional[cc.Hub] = None      # the hub answering here, for what a question needs

    def notify(self, chat: str, obj: Dict[str, Any], urgent: bool = False) -> None:
        """A notification for the phone while the app isn't on screen: sealed for
        the phone, handed to the relay, which sends it on by Web Push."""
        room = self.rooms.get(str(chat))
        if room is None:
            return
        # Plain words, one or two sentences: a notification shows no markdown, and a push carries about 4 KB.
        title = plain_text(obj.get("title"))
        obj = dict(obj, body=note_body(obj.get("body")), title=(title[:59] + "…") if len(title) > 60 else title)
        fut = asyncio.run_coroutine_threadsafe(room.notice(obj, urgent), self.loop)
        try:
            fut.result(10)
        except Exception:
            pass

    def finished(self, chat: str, title: str, text: str, c: str, t: str) -> None:
        """One notification per turn, saying its newest words: a turn that sends more than
        once (a file's note, then the reply) is told once, a moment after its last send.
        High priority, so a phone asleep (Android's Doze) shows it now, not when woken."""
        if not t or t in self._told:
            return
        body = " ".join(text.split())[:240]
        with self._done_lock:
            old = self._done_later.pop(t, None)
            if old is not None:
                old.cancel()

            def go():
                with self._done_lock:
                    if self._done_later.get(t) is not timer:
                        return
                    self._done_later.pop(t, None)
                    self._told = (self._told + [t])[-200:]
                self.notify(chat, {"kind": "done", "title": title, "body": body, "c": c}, urgent=True)
            timer = threading.Timer(DONE_SETTLE_S, go)
            timer.daemon = True
            self._done_later[t] = timer
        timer.start()

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

    def push_now(self, chat: str, obj: Dict[str, Any]) -> Optional[str]:
        """A frame about this moment (an agent at work): only to a phone that is connected. Kept in
        the mailbox, it would read as current when the phone opens hours later, with the PC off."""
        room = self.rooms.get(str(chat))
        if room is None or not room.peer_up or room.chan is None:
            return None
        return self._push(chat, obj)

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
            from .call_me import agent_name
            self.finished(chat, agent_name(), text, ctx.get("c", ""), ctx["t"])
        return mid

    def edit(self, chat, msg_id, text):
        return self._push(chat, {"type": "edit", "of": msg_id, "text": text}) is not None

    def _needs_tap(self, chat, c) -> bool:
        """Whether the question being asked now may only be allowed with a tap (not a spoken yes)."""
        hub = self.hub
        if hub is None:
            return False
        with hub._lock:
            run = hub._busy.get(c) or hub._busy.get(str(chat)) or {}
            return bool(run.get("tap"))

    def ask_voice(self, chat, text, aid, tap, detail=None):
        """A step in a spoken turn the brain asks about in its own words: the card shows on the
        phone and the call waits on it, and nothing is read out from a template."""
        return self.ask(chat, text, aid=aid, tap=tap, aloud=False, detail=detail)

    takes_detail = True                    # channel_core hands over the whole command

    def ask(self, chat, text, *, aid="", tap=None, aloud=True, detail=None):
        # The question's own id: an answer from a notification counts only for it.
        aid, c = aid or secrets.token_hex(8), cc.turn_context().get("c", "")
        self.asks[c or str(chat)] = aid
        tap = self._needs_tap(chat, c) if tap is None else tap
        self.asked[aid] = {"at": time.time(), "key": c or str(chat), "c": c, "chat": str(chat), "text": text, "tap": tap}
        if len(self.asked) > 100:
            for k in list(self.asked)[:50]:
                self.asked.pop(k, None)
        call = self._call_for(chat)
        if call is not None:
            if aloud:
                call.ask(text, tap=tap, aid=aid)
            else:
                call.ask_quietly(tap=tap, aid=aid)
        from .call_me import agent_name
        p = ask_parts(agent_name(), text, str((detail or {}).get("cmd") or ""))
        mid = self._push(chat, {"type": "ask", "text": text, "ask": aid, "who": p["who"], "cmd": p["cmd"],
                                **({"tap": True} if tap else {})})
        self.notify(chat, dict(ask_note(p), kind="ask", c=c, ask=aid), urgent=True)
        return mid

    def send_file(self, chat, path):
        """A card for the file (name, size, type, a thumbnail); the phone fetches the rest when it's
        opened, so nothing big waits in the relay's mailbox for a phone that's away."""
        from . import relay_media
        c = relay_media.card(path)
        if c is not None:
            self._push(chat, {"type": "file", **c})

    def typing(self, chat):
        call = self._call_for(chat)
        if call is not None:
            call.begin()                    # a turn in the call's chat, said or typed: its reply is spoken
        self._push(chat, {"type": "typing"})

    step_detail = True                  # steps carry what they ran, and their output follows

    def step(self, chat, words, tool="", site="", cmd=""):
        """One step of the reply as it happens ("Read notes.txt"), with the tool,
        the site it reaches (for that site's icon) and what it ran (for the step's detail)."""
        call = self._call_for(chat)
        if call is not None:
            if tool == "end_session":
                call.hang_up()                  # its goodbye is said, then the call ends
                return
            call.step(words)
        # Thinking still waiting on its pace goes first, so it sits above this step and not under the next.
        t = cc.turn_context().get("t", "")
        timer = self._later.pop(f"think:{t}", None)
        if timer is not None:
            timer.cancel()
            newest = self._latest.get(f"think:{t}")
            if newest is not None:
                self._think_at[t] = time.time()
                self._push(chat, {"type": "think", "text": newest})
        self._push(chat, {"type": "step", "words": words, "tool": tool, "site": site, **({"cmd": cmd} if cmd else {})})

    def call_note(self, chat, cid: str = "") -> str:
        """What a turn in a call's chat is told: it is talking, not writing."""
        room = self.rooms.get(str(chat))
        call = getattr(room, "call", None) if room is not None else None
        if call is None or call.ended or (cid and call.cid and cid != call.cid):
            return ""
        return CALL_NOTE

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
        self._paced(chat, "think", text[-20000:], THINK_EVERY, self._think_at)

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
        self.saying = None                    # a reply read aloud to the phone in the PC's voice
        self.call_gone_told = False
        self.call_heard = 0.0                 # when the phone last said anything about the call

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
        url = f"{self.bridge.url}/room/{self.room}?role=pc"
        # The licence goes in headers, kept out of the address (and so out of logs):
        # the key and this PC's activation of it, which the relay checks together.
        headers = {"Authorization": f"Bearer {self.bridge.licence}",
                   "X-Licence-Instance": self.bridge.instance}
        backoff = 1.0
        while not self.bridge.stopping:
            try:
                async with websockets.connect(url, max_size=1 << 20, ping_interval=25,
                                              **_header_kw(headers)) as ws:
                    self.ws, backoff = ws, 1.0
                    async for raw in ws:
                        await self.on_frame(json.loads(raw))
            except Exception as e:
                if "402" in str(e):
                    _log("the relay needs Inglen Plus for this PC; stopping.")
                    self.bridge.stopping = True
                    return
                if "429" in str(e):
                    _log("this Plus licence is in use on too many PCs; stopping.")
                    self.bridge.stopping = True
                    return
            if self.peer_up and self.phone:
                _seen(self.chat, False)
            self.ws, self.chan, self.peer_up = None, None, False
            if self.bridge.stopping or self.room not in self.bridge.wanted():
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def on_frame(self, f: Dict[str, Any]) -> None:
        t = f.get("t")
        if t == "peer":
            was, self.peer_up = self.peer_up, bool(f.get("up"))
            if not self.peer_up:
                if was and self.phone:
                    _seen(self.chat, False)
                    _log(f"phone {self.chat} went away")
                self.chan = None
                if self.call is not None:
                    # A phone that drops mid-call has a while to come back before the call ends.
                    asyncio.get_running_loop().call_later(CALL_AWAY_S, self._call_gone, self.call)
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
            _log(f"a phone asks to pair; confirm {digits} on the PC")
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
            _seen(self.chat, True)
            _log(f"phone {self.chat} connected")
            await self.ws.send(json.dumps({"t": "hello", "eph": R.b64(R.raw_public(eph))}))
            from .call_me import agent_name
            # The turns running now: the phone drops any other it still draws as
            # working (one whose end it missed while it was closed).
            await self.deliver({"type": "hi", "name": agent_name(), "running": self.bridge.running()})
            warm_brain()                      # the picker's answers, ready before it's opened
            return
        if t == "box" and self.phone:          # Allow or Deny tapped on a notification
            m = R.open_box(self.box_key(), f, self.seen_boxes)
            if m and m.get("type") == "answer":
                await asyncio.to_thread(self.bridge.on_answer, self, m)
            elif m and m.get("type") == "call.decline":
                await asyncio.to_thread(self.bridge.decline, str(m.get("ring") or ""))
            return
        if t == "msg" and self.chan is not None:
            m = self.chan.open(f)
            m = self.joiner.add(m) if m else None
            if m and str(m.get("type") or "").startswith("call."):
                self.on_call(m)
                return
            if m and str(m.get("type") or "").startswith("say."):
                self.on_say(m)
                return
            if m and m.get("type") == "answer":
                await asyncio.to_thread(self.bridge.on_answer, self, m)
                return
            if m and m.get("type") == "req":
                # Lookups run side by side, so a slow one (icons from the web)
                # never holds up a switch the person is waiting on.
                task = asyncio.ensure_future(asyncio.to_thread(self.bridge.on_message, self, m))
                self.lookups.add(task)
                task.add_done_callback(self.lookups.discard)
            elif m:
                await asyncio.to_thread(self.bridge.on_message, self, m)

    def on_say(self, m: Dict[str, Any]) -> None:
        """Read aloud on the phone, in the PC's own voice (Settings > Voice, as the call):
        say.start {text, ref} streams the voice as say.down frames, then say.end;
        say.stop cuts it. One at a time per phone."""
        kind, ref = m.get("type"), str(m.get("ref") or "")
        old, self.saying = self.saying, None
        if old is not None:
            old.stop()
        if kind != "say.start" or not str(m.get("text") or "").strip():
            return
        from . import voice as _voice
        adapter, chat = self.bridge.adapter, self.chat

        def send(obj):
            prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
            try:
                adapter._push(chat, dict(obj, ref=ref))
            finally:
                cc._TURN.ctx = prev

        class Sink:
            def __call__(self, samples, sr):
                send({"type": "say.down", "sr": int(sr), "data": base64.b64encode(samples.astype("<i2").tobytes()).decode()})

            def flush(self):
                send({"type": "say.flush"})

        speaker = _voice.Speaker(sink=Sink(), first_chunk=40)
        self.saying = speaker

        def run():
            said = speaker.speak(str(m["text"]), blocking=True)
            if said == "no_voice":
                _log("read aloud: voice isn't set up on this PC")
                send({"type": "say.error", "detail": "Voice isn't set up on your PC"})
            elif said == "error":
                _log(f"read aloud failed: {_voice.status().get('last_load_error') or _voice.last_speak_error()['error']}")
                send({"type": "say.error", "detail": "The voice on your PC couldn't speak. Its log says why."})
            send({"type": "say.end"})
            if self.saying is speaker:
                self.saying = None
        threading.Thread(target=run, daemon=True, name="relay-say").start()

    def on_call(self, m: Dict[str, Any]) -> None:
        """A call from the phone: its audio frames, its start and its end."""
        # Runs on the connection's loop: anything that sends (and so waits on this loop) goes to a thread.
        kind = m.get("type")
        call = self.call
        self.call_heard = time.time()
        if kind in ("call.up", "call.alive"):
            if call is not None:
                if kind == "call.up":
                    call.up(str(m.get("data") or ""), int(m.get("rate") or 16000))
            elif not self.call_gone_told:
                # A phone still talking into a call that ended here (it was away too long): say so once.
                self.call_gone_told = True
                asyncio.ensure_future(self.deliver({"type": "call.ended", "gone": True, "id": secrets.token_hex(6)}))
            return
        if kind == "call.decline":
            threading.Thread(target=self.bridge.decline, args=(str(m.get("ring") or ""),), daemon=True).start()
            return
        if kind == "call.start":
            if call is not None and not call.ended:
                threading.Thread(target=call.end, args=("pc",), daemon=True).start()
            from .call import Call
            adapter, chat = self.bridge.adapter, self.chat
            cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(m.get("chat") or ""))[:80]
            ring = self.bridge.answered(str(m.get("ring") or "")) if m.get("ring") else None

            def send(obj):
                # Only to a phone that is there: call frames never wait in the mailbox.
                if self.peer_up and self.chan is not None:
                    adapter._push(chat, obj)

            from . import agent_sessions

            def say(text):
                if agent_sessions.parse_chat(cid):
                    # A call from a coding agent's session talks to that agent, in that session.
                    self.bridge.agent_say(self, {"chat": cid, "text": text, "spoken": True})
                    return "ran"
                return self.bridge.hub.receive(chat, "phone", text, name="Phone", cid=cid, spoken=True)

            def save(changed):
                return _window("POST", "/api/settings", changed)

            def post(text):
                # What it says first when it called: kept in the chat, so the model knows why it called.
                from . import chat_sync
                chat_sync.post(cid, "Call", "relay", [{"role": "assistant", "content": text, "via": "call",
                                                       "at": int(time.time() * 1000)}])
                prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), {"c": cid, "t": secrets.token_hex(5)}
                try:
                    send({"type": "text", "text": text})
                finally:
                    cc._TURN.ctx = prev

            _log("a call from the phone" + (" (it answered a ring)" if ring else ""))
            _activity("call", chat=cid)
            self.call = Call(send, say, cid=cid, save=save, ref=str(m.get("ref") or "")[:40],
                             opener=ring, post=post if ring and cid else None)
            self.call_gone_told = False
            threading.Thread(target=self.call.start, daemon=True, name="relay-call").start()
            threading.Thread(target=self._keep_hearing, args=(self.call,), daemon=True, name="relay-call-beat").start()
            return
        if call is None:
            return
        if kind == "call.end":
            self.call = None
            threading.Thread(target=call.end, daemon=True).start()
        elif kind == "call.hush":
            threading.Thread(target=call.hush, daemon=True).start()
        elif kind == "call.set":
            voice = m.get("voice")
            lang = m.get("language")
            threading.Thread(target=call.set, args=(str(voice) if voice else None,
                                                    str(lang) if lang is not None else None),
                             daemon=True).start()

    def _keep_hearing(self, call) -> None:
        """A phone that goes quiet (frozen, killed, out of signal without the socket noticing)
        stops sending its mic and its heartbeat: the call ends here instead of staying half open."""
        while self.call is call and not call.ended:
            time.sleep(1.0)
            if self.call is call and time.time() - self.call_heard > CALL_QUIET_S:
                self.call = None
                call.end("pc")

    def _call_gone(self, call) -> None:
        """The phone left mid-call and hasn't come back: the call ends here."""
        if self.call is call and not self.peer_up:
            self.call = None
            threading.Thread(target=call.end, args=("pc",), daemon=True).start()

    async def tell_paired(self) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps({"t": "paired"}))


def _header_kw(headers: Dict[str, str]) -> Dict[str, Any]:
    """websockets 14+ calls them additional_headers; older ones extra_headers."""
    import inspect
    import websockets
    try:
        params = inspect.signature(websockets.connect).parameters
    except (TypeError, ValueError):
        params = {}
    return {"extra_headers": headers} if "extra_headers" in params else {"additional_headers": headers}


class Bridge:
    def __init__(self):
        self.url = relay_url().replace("https://", "wss://").replace("http://", "ws://")
        from . import plus
        self.licence = (plus._read().get("key") or "")
        self.instance = (plus._read().get("instance") or "")
        self.pc = _pc_key()
        self.stopping = False
        self.loop = asyncio.new_event_loop()
        self.adapter = Relay(self.loop)
        self.hub = cc.Hub(self.adapter, configured_senders=["phone"])
        self.adapter.hub = self.hub
        self.rooms: Dict[str, Room] = {}
        self._mirrored: Dict[str, str] = {}                 # a window turn -> its text so far, for the phones
        self._said: Dict[str, List[str]] = {}
        self.agent_runs: Dict[str, Dict[str, Any]] = {}     # session chat -> its run, while an agent answers
        self.phone_sessions: Dict[str, float] = {}          # session chat -> when the phone last sent into it
        from . import call_me
        self.rings = call_me.Rings()                        # the agent calling the phone by itself
        self._ring_timer: Optional[threading.Timer] = None
        self._rang: Dict[str, Dict[str, Any]] = {}          # ring id -> the ring, kept a while after it stops

    def wanted(self) -> Dict[str, Dict[str, Any]]:
        """room -> {ps, phone}: every paired phone's room, and a pairing in progress."""
        d = _read()
        out = {p["room"]: {"ps": p["ps"], "phone": p["pub"]} for p in d.get("phones", [])}
        p = d.get("pending") or {}
        if p.get("room") and time.time() - float(p.get("at") or 0) < PAIR_LIFE_S:
            out.setdefault(p["room"], {"ps": p["ps"], "phone": ""})
        return out

    def running(self) -> List[str]:
        """The turns answering now, here and in coding agents' sessions."""
        hub = getattr(self, "hub", None)
        out = []
        if hub is not None:
            with hub._lock:
                out += [str(r.get("t")) for r in hub._busy.values() if r.get("t")]
        out += [str(r.get("t")) for r in list(self.agent_runs.values()) if r.get("t")]
        return out

    def on_message(self, room: Room, m: Dict[str, Any]) -> None:
        if m.get("type") == "req":
            try:
                if m.get("what") == "agent_start":
                    # A new session in a coding agent's own harness: the phone opens it at once,
                    # and its first message starts it.
                    from .agent_launch import reserve
                    out = reserve(str(m.get("agent") or ""), str(m.get("cwd") or ""), str(m.get("text") or ""))
                    if not out.get("error"):
                        self.agent_say(room, {"chat": out["chat"], "text": str(m.get("text") or ""), "model": m.get("model") or ""})
                else:
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
            # Pictures and files sent into a coding agent's session go with the message, saved where it can open them.
            paths = []
            for f in m.get("files") or []:
                try:
                    paths.append(cc.save_upload(R.unb64(f.get("data") or ""), f.get("name") or "file"))
                except Exception:
                    continue
            self.agent_say(room, dict(m, paths=paths))
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
        if self.hub.answer_voice(key, aid, yes):
            self.hub.receive(room.chat, "phone", "yes" if yes else "no", cid=cid, spoken=True)
        elif run is not None:
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
        text = (str(m.get("text") or "").strip() + agent_sessions.attached(m.get("paths") or [])).strip()
        # The model picked under the phone's message box for this session ('' is the session's own).
        model = re.sub(r"[^\w.:/@\[\]-]", "", str(m.get("model") or ""))[:120]
        effort = re.sub(r"[^a-z]", "", str(m.get("effort") or "").lower())[:12]   # its thinking effort, picked the same way
        turn = secrets.token_hex(5)

        def push(obj: Dict[str, Any]) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                room.deliver(dict(obj, id=secrets.token_hex(6), c=cid, t=turn)), self.loop)
            try:
                fut.result(15)
            except Exception:
                pass

        agent = (agent_sessions.parse_chat(cid) or ("", ""))[0]
        name = agent_sessions.label(agent)
        run = self.agent_runs.get(cid)
        if run is not None:
            q = run.get("q")
            from .call import spoken_answer
            said = spoken_answer(text)
            if q is not None and not q.is_set() and said:
                run["answer"] = said in ("allow", "always")
                self.adapter.asks.pop(cid, None)
                q.set()
            elif text:
                # Said while it works: Codex steers the turn it is on, Claude Code takes it next
                # (each says so); the other agents get it when this turn ends.
                run["steer"].put(text)
                if agent not in ("codex", "claude-code"):
                    push({"type": "text", "note": True, "text": f"{name} gets this when it finishes."})
            return
        if not text:
            return
        _log(f"{name} session {cid[-8:]}: a message from the phone" + (f" (on {model})" if model else ""))
        _activity("agent_session", agent=agent, chat=cid, text=text[:200], **({"model": model} if model else {}))
        run = self.agent_runs[cid] = {"q": None, "answer": False, "t": turn, "steer": queue.Queue(), "keys": [cid]}
        self.phone_sessions[cid] = time.time()
        sent = {"live": 0.0, "think": 0.0}
        thought: List[str] = []

        def on_call():
            c = getattr(room, "call", None)
            return c if c is not None and not c.ended and c.cid == cid else None

        def emit(kind: str, **f: Any) -> None:
            nonlocal cid
            now = time.time()
            if kind == "error" and str(f.get("message") or "").startswith(f"{name} stopped:"):
                # Said the same on the call, in the chat and in the notification.
                f["message"] = stopped_note(name, str(f["message"])[len(name) + 9:])
            call = on_call()
            if call is not None:
                if kind == "live":
                    call.live(f.get("text") or "")
                elif kind == "step":
                    call.step(f.get("words") or "")
                elif kind in ("text", "error"):
                    call.done(f.get("text") or f.get("message") or "")
            if kind == "session":
                # A new session's own id: the phone moves the chat it opened onto it.
                real = agent_sessions.chat_id(str(f.get("agent") or agent), str(f.get("id") or ""))
                if real != cid:
                    was, cid = cid, real
                    self.agent_runs[real] = run
                    self.phone_sessions[real] = time.time()
                    run["keys"].append(real)
                    push({"type": "agent_session", "from": was, "chat": real})
            elif kind == "step":
                if thought and sent.get("thought_n") != len(thought):
                    # The thinking before this step reaches the phone before the step, so it sits above it.
                    sent["thought_n"] = len(thought)
                    push({"type": "think", "text": "".join(thought)[-20000:]})
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
                    sent["think"], sent["thought_n"] = now, len(thought)
                    push({"type": "think", "text": "".join(thought)[-20000:]})
            elif kind == "note":
                # Inglen saying what it did ("Sent to Codex on your PC"), not the agent: drawn muted.
                push({"type": "text", "text": f.get("text") or "", "note": True})
            elif kind == "text":
                push({"type": "text", "text": f.get("text") or ""})
                try:
                    # The files its reply names, as cards the phone opens from here (the Files tab's rules).
                    got = agent_sessions.parse_chat(cid)
                    where = (agent_sessions.read(*got) or {}).get("cwd") or "" if got else ""
                    for c in session_files(f.get("text") or "", where):
                        push({"type": "file", **c})
                except Exception:
                    pass
                self.adapter.finished(room.chat, name, f.get("text") or "", cid, turn)
                _log(f"{name} session {cid[-8:]} replied")
                _activity("agent_session_reply", agent=agent, chat=cid, chars=len(f.get("text") or ""))
            elif kind == "error":
                _log(f"{name} session {cid[-8:]} stopped: {f.get('message') or ''}"[:300])
                _activity("agent_session_error", agent=agent, chat=cid, message=str(f.get("message") or "")[:300])
                said = str(f.get("message") or "")
                push({"type": "text", "text": said or "It stopped.", "error": True})
                if said:
                    self.adapter.finished(room.chat, name, said, cid, turn)

        def ask(question: str) -> bool:
            run["q"], run["answer"] = threading.Event(), False
            aid = secrets.token_hex(8)
            self.adapter.asks[cid] = aid
            p = ask_parts(name, question)
            _activity("agent_session_ask", agent=agent, chat=cid, question=question[:300])
            push({"type": "ask", "text": question, "ask": aid, "who": name, "cmd": p["cmd"]})
            self.adapter.notify(room.chat, dict(ask_note(p), kind="ask", c=cid, ask=aid), urgent=True)
            run["q"].wait(cc.ASK_TIMEOUT_S)
            run["q"] = None
            return bool(run["answer"])

        def drain() -> List[str]:
            out: List[str] = []
            while True:
                try:
                    out.append(run["steer"].get_nowait())
                except queue.Empty:
                    return out

        def go() -> None:
            try:
                call = on_call()
                if call is not None:
                    call.begin()                     # its reply is spoken on the call
                push({"type": "typing"})
                asyncio.run(agent_sessions.say(cid, text, emit, ask, drain=drain, model=model, effort=effort))
            finally:
                def finished() -> None:
                    for k in run["keys"]:
                        if self.agent_runs.get(k) is run:
                            self.agent_runs.pop(k, None)
                    left = drain()
                    if left:
                        # What came after its last turn ended starts the next one.
                        self.agent_say(room, {"chat": cid, "text": "\n\n".join(left), "model": model, "effort": effort})
                self.loop.call_soon_threadsafe(finished)

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

    # ---------------------------------------------------------------- calling the phone
    def _phones(self) -> List["Room"]:
        return [r for r in list(self.adapter.rooms.values()) if getattr(r, "phone", None)]

    def ring(self, ev: Dict[str, Any]) -> Optional[str]:
        """Call the paired phones (call_me said this reason may): a notification with Answer and
        Decline, and the incoming call on the app's own screen where it is open. The ring id, or None."""
        from . import call_me
        rooms = self._phones()
        if not rooms or any(r.call is not None and not r.call.ended for r in rooms):
            return None
        rid = secrets.token_hex(6)
        ev = dict(ev, c=ev.get("c") or "call_" + secrets.token_hex(6))
        got = self.rings.start(ev, rid)
        if got is None:
            return None
        self._rang[rid] = got
        if len(self._rang) > 20:
            self._rang.pop(next(iter(self._rang)), None)
        name = call_me.agent_name()
        frame = {"type": "call.ring", "ring": rid, "why": got["why"], "c": got["c"], "name": name,
                 "for_s": call_me.RING_S}
        note = {"kind": "ring", "title": f"{name} is calling", "body": got["why"], "c": got["c"], "ring": rid}
        prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
        try:
            for r in rooms:
                if r.peer_up and r.chan is not None:
                    self.adapter._push(r.chat, frame)      # never kept in the mailbox: a ring is now or never
                self.adapter.notify(r.chat, note, urgent=True)
        finally:
            cc._TURN.ctx = prev
        t = threading.Timer(call_me.RING_S, self._missed, args=(rid,))
        t.daemon = True
        self._ring_timer = t
        t.start()
        _log(f"calling the phone: {got['reason']}")
        return rid

    def _stopped(self, ring: Dict[str, Any], missed: bool) -> None:
        from . import call_me
        name = call_me.agent_name()
        prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
        try:
            for r in self._phones():
                if r.peer_up and r.chan is not None:
                    self.adapter._push(r.chat, {"type": "call.unring", "ring": ring["id"], "missed": missed})
                if missed:
                    # Takes the ringing notification's place: a plain one that opens the chat.
                    self.adapter.notify(r.chat, {"kind": "missed", "title": f"Missed call from {name}",
                                                 "body": ring.get("why") or "", "c": ring.get("c") or "",
                                                 "ring": ring["id"]})
        finally:
            cc._TURN.ctx = prev

    def _missed(self, rid: str) -> None:
        ring = self.rings.drop(rid, missed=True)
        if ring is not None:
            self._stopped(ring, missed=True)

    def decline(self, rid: str) -> None:
        ring = self.rings.drop(rid)
        if ring is not None:
            if self._ring_timer is not None:
                self._ring_timer.cancel()
            self._stopped(ring, missed=False)

    def answered(self, rid: str) -> Optional[Dict[str, Any]]:
        """The ring a call from the phone answers (also one that just went unanswered), for what it says first."""
        ring = self.rings.take(rid)
        if ring is not None and self._ring_timer is not None:
            self._ring_timer.cancel()
        return ring or self._rang.get(rid)

    def watch_questions(self) -> None:
        while not self.stopping:
            time.sleep(10)
            try:
                self.check_questions()
            except Exception:
                continue

    def check_questions(self, idle: Optional[float] = None) -> None:
        """A question that has waited for an answer longer than the set minutes calls the phone."""
        from . import call_me
        waiting = [(aid, q) for aid, q in list(self.adapter.asked.items())
                   if self.adapter.asks.get(q["key"]) == aid and not q.get("rang")]
        if not waiting:
            return
        r = call_me.rules()
        for aid, q in waiting:
            if not call_me.question_due(q["at"], r):
                continue
            ev = call_me.want("question", q["text"], chat=q["c"] or q["key"], ask=aid, tap=q["tap"],
                              key="question:" + aid, r=r, idle=idle)
            if ev is not None and self.ring(ev):
                q["rang"] = True

    def from_here(self, c: str) -> bool:
        """A session the phone sent into, or one Inglen ran: work they handed off, not a session
        they are working in at the PC."""
        from . import agent_sessions
        if time.time() - self.phone_sessions.get(c, 0.0) < 12 * 3600:
            return True
        got = agent_sessions.parse_chat(c)
        return bool(got) and tuple(got) in agent_sessions._RAN_HERE

    def worth_telling(self, n: Dict[str, Any], idle: Optional[float] = None) -> bool:
        """An agent's finished turn is news for the phone unless they are at the PC, in a session they
        are working in there themselves. Work handed off from the phone or Inglen is always told."""
        from . import call_me
        return self.from_here(str(n.get("c") or "")) or not call_me.at_pc(idle)

    def agent_call(self, n: Dict[str, Any], idle: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """The ring an agent's finished turn makes: only for work handed off from the phone or Inglen,
        only when it has news, and one per session per stretch of work."""
        from . import call_me
        c = str(n.get("c") or "")
        if not c or not self.from_here(c) or not AgentWatch.worth_a_call(n):
            return None
        if any(getattr(r, "peer_up", False) for r in self._phones()):
            return None                            # the app is open on the phone: the reply shows there
        ev = call_me.want("job", f"{n.get('title') or 'An agent'} {'has a question' if n.get('kind') == 'ask' else 'finished'}: "
                                 f"{n.get('body') or ''}", chat=c, key="agent:" + c, idle=idle)
        return dict(ev, once_s=STRETCH_S) if ev is not None else None

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
                if self.worth_telling(n):
                    for chat in list(self.adapter.rooms):     # each paired phone
                        self.adapter.notify(chat, n)
                try:
                    ev = self.agent_call(n)
                    if ev is not None:
                        self.ring(ev)
                except Exception:
                    pass
            # A session open on a phone shows what its agent wrote since, without reopening it,
            # and that its agent is at work (the phone shows it working until it goes quiet).
            for c, busy_now in [(c, None) for c in watch.moved] + [(c, False) for c in watch.quiet]:
                if c in self.agent_runs:
                    continue
                if busy_now is None:
                    # Written to: at work unless what it wrote last is its finished reply.
                    got = agent_sessions.parse_chat(c)
                    d = agent_sessions.read(*got) if got else {}
                    busy_now = agent_sessions.working(d.get("messages") or [], agent_sessions._mtime(d.get("path") or ""))
                prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
                try:
                    for chat in list(self.adapter.rooms):
                        self.adapter.push_now(chat, {"type": "changed", "what": "chat", "c": c, "working": busy_now})
                finally:
                    cc._TURN.ctx = prev

    # Settings a phone shows: a change on the PC redraws its brain chip and modes at once.
    _PHONE_SETTINGS = re.compile(r"^(llm_|agent_|brain|approval_mode|think_|gen_stats|preferred_model|ui_theme|ui_accent|theme|clock_)")

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
        if kind == "call_me":
            from . import call_me
            # Checked again here: the rules may have changed since it was asked for.
            if call_me.allowed(ev):
                threading.Thread(target=self.ring, args=(ev,), daemon=True, name="relay-ring").start()
            return
        if kind == "phone_notice":
            # A reminder (or the notify tool) for the phone: a notification on each paired phone.
            note = {"kind": "reminder", "title": str(ev.get("title") or "Inglen"),
                    "body": str(ev.get("body") or ""), "c": str(ev.get("c") or "")}
            threading.Thread(target=lambda: [self.adapter.notify(chat, note, urgent=True) for chat in phones],
                             daemon=True, name="relay-notice").start()
            return
        if kind == "to_phone":
            threading.Thread(target=self.to_phone, args=(ev,), daemon=True, name="relay-to-phone").start()
            return
        out: Optional[Dict[str, Any]] = None
        if kind == "settings" and any(self._PHONE_SETTINGS.match(str(k)) for k in ev.get("keys") or []):
            _BRAIN_CACHE.clear()                  # the phone's next look at the brain reads it fresh
            out = {"type": "changed", "what": "brain"}
        elif kind == "chat" and ev.get("chat"):
            out = {"type": "changed", "what": "chat", "c": ev["chat"], **({"gone": True} if ev.get("gone") else {})}
        elif kind == "turn" and ev.get("from") not in (None, "", self.adapter.name) and ev.get("chat"):
            # A turn the window, the terminal or a chat app is running, shown on the phones as it goes.
            self._mirror(phones, ev)
            return
        if out:
            prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
            try:
                for chat in phones:
                    self.adapter._push(chat, out)
            finally:
                cc._TURN.ctx = prev

    def to_phone(self, ev: Dict[str, Any]) -> None:
        """A message or a file the window or terminal sent to the phone (send_to_phone): into the
        phone's own chat, like a text, with a notification for a phone that isn't looking."""
        from .call_me import agent_name
        text, path = str(ev.get("text") or ""), str(ev.get("path") or "")
        prev, cc._TURN.ctx = getattr(cc._TURN, "ctx", None), None
        try:
            for chat in list(self.adapter.rooms):
                if text:
                    self.adapter._push(chat, {"type": "text", "text": text})
                if path and os.path.isfile(path):
                    self.adapter.send_file(chat, path)
                self.adapter.notify(chat, {"kind": "done", "title": agent_name(),
                                           "body": text or os.path.basename(path), "c": ""}, urgent=True)
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
                    self.adapter._push(chat, {"type": "user", "text": ev.get("text") or "", "via": ev.get("from") or "window"})
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
        threading.Thread(target=self.watch_questions, daemon=True, name="relay-question-watch").start()
        try:
            self.loop.run_until_complete(self.keep_rooms())
        except KeyboardInterrupt:
            pass


def run() -> None:
    if not relay_url():
        _log("no relay address yet (Settings > Plus).")
        return
    cc.use_saved_brain(_log)
    _seen("*", False)
    _log(f"up on {relay_url()}. Phones paired: {len(phones())}")
    Bridge().run()


if __name__ == "__main__":
    from .bridge_status import follow_parent
    follow_parent()
    run()
