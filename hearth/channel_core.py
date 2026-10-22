"""What every chat app Hearth answers on shares: who may talk to it, the
conversation, questions before risky steps, and what comes back.

Each app (Telegram, Discord, WhatsApp, or one of the person's own) is a small
adapter that receives messages and can send, edit and attach. This module does
the rest, the same way for all of them:

- Access. People listed in the app's settings are answered. Anyone else gets a
  short pairing code and nothing is run for them until the owner approves the
  code in Hearth (Settings > Connectors, /pair in the terminal, or by asking).
  A code is 8 characters without look-alikes, lasts an hour, and at most three
  wait at once per app.
- The conversation. Each person's chat is one of Hearth's own chats (so it
  shows in the window, marked with the app), carried on from turn to turn.
- Risky steps. When the approval mode would ask, the question goes to the chat
  ("reply yes to let me..."); the next message answers it. Auto asks nothing.
- While a reply runs, more messages are handed to it as it works.
- Voice notes are written down by the speech model on this PC; photos and files
  are saved and the model is told where, so it can look at them.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional

ACCESS_FILE = os.path.join(os.path.expanduser("~"), ".hearth", "channel_access.json")
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0, O, 1, I
CODE_LIFE_S = 3600
MAX_PENDING = 3
ASK_TIMEOUT_S = 300
_FILE_LOCK = threading.Lock()


# ---------------------------------------------------------------- access

def _read() -> Dict[str, Any]:
    try:
        with open(ACCESS_FILE, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(ACCESS_FILE), exist_ok=True)
    tmp = ACCESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, ACCESS_FILE)


def _live(pending: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    return {c: p for c, p in pending.items() if now - float(p.get("at") or 0) < CODE_LIFE_S}


def is_allowed(channel: str, sender: str, configured=()) -> bool:
    sender = str(sender)
    if sender in {str(x) for x in configured or ()}:
        return True
    return sender in (_read().get(channel) or {}).get("approved", {})


def request_code(channel: str, sender: str, name: str = "") -> Optional[str]:
    """The code for an unknown sender: theirs if one is waiting, a new one,
    or None when three already wait (they're ignored until one clears)."""
    sender = str(sender)
    with _FILE_LOCK:
        d = _read()
        ch = d.setdefault(channel, {})
        pending = _live(ch.get("pending") or {})
        mine = next((c for c, p in pending.items() if p.get("sender") == sender), None)
        if mine:
            ch["pending"] = pending
            _write(d)
            return mine
        if len(pending) >= MAX_PENDING:
            return None
        code = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
        pending[code] = {"sender": sender, "name": name or "", "at": time.time()}
        ch["pending"] = pending
        _write(d)
        return code


def pending(channel: str = "") -> List[Dict[str, Any]]:
    """Codes waiting for the owner, newest first."""
    d = _read()
    out = []
    for ch, v in d.items():
        if channel and ch != channel:
            continue
        for code, p in _live((v or {}).get("pending") or {}).items():
            out.append({"channel": ch, "code": code, "sender": p.get("sender"), "name": p.get("name") or "",
                        "at": p.get("at")})
    return sorted(out, key=lambda r: -float(r["at"] or 0))


def approve(channel: str, code: str) -> Dict[str, Any]:
    code = (code or "").strip().upper()
    with _FILE_LOCK:
        d = _read()
        ch = d.setdefault(channel, {})
        p = _live(ch.get("pending") or {}).pop(code, None) if code else None
        if not p:
            return {"ok": False, "error": f"no {channel} code {code} is waiting (they last an hour)"}
        ch["pending"] = {c: v for c, v in _live(ch.get("pending") or {}).items() if c != code}
        ch.setdefault("approved", {})[str(p["sender"])] = {"name": p.get("name") or "", "at": time.time()}
        _write(d)
    return {"ok": True, "channel": channel, "sender": p["sender"], "name": p.get("name") or ""}


def dismiss(channel: str, code: str) -> Dict[str, Any]:
    with _FILE_LOCK:
        d = _read()
        ch = d.setdefault(channel, {})
        live = _live(ch.get("pending") or {})
        gone = live.pop((code or "").strip().upper(), None)
        ch["pending"] = live
        _write(d)
    return {"ok": bool(gone)}


def approved(channel: str = "") -> List[Dict[str, Any]]:
    d = _read()
    return [{"channel": ch, "sender": s, "name": v.get("name") or ""}
            for ch, val in d.items() if not channel or ch == channel
            for s, v in ((val or {}).get("approved") or {}).items()]


def revoke(channel: str, sender: str) -> Dict[str, Any]:
    with _FILE_LOCK:
        d = _read()
        gone = ((d.get(channel) or {}).get("approved") or {}).pop(str(sender), None)
        _write(d)
    return {"ok": bool(gone)}


# ---------------------------------------------------------------- the adapter

class Adapter:
    """What a chat app gives Hearth. `send` returns the sent message's id when
    the app can edit it later; `edit` returns False when it can't."""
    name = "chat"          # settings key and chat source: telegram, discord, whatsapp
    label = "Chat"         # shown to people
    max_len = 4000
    # Tell a stranger their pairing code. Off where replying to strangers would
    # look like spam on a personal account: their request still waits in Hearth.
    replies_to_strangers = True
    # Live progress is one message edited as tools run. Where messages can't be
    # edited, the reply ends with a line saying what was used instead.
    can_edit = True

    def send(self, chat: str, text: str, reply_to: Any = None) -> Any:
        raise NotImplementedError

    def edit(self, chat: str, msg_id: Any, text: str) -> bool:
        return False

    def send_file(self, chat: str, path: str) -> None:
        pass

    def typing(self, chat: str) -> None:
        pass


def split(text: str, n: int) -> List[str]:
    """Pieces no longer than n, broken at a paragraph, line or space when it can."""
    text = text or ""
    out = []
    while len(text) > n:
        cut = max(text.rfind("\n\n", 0, n), text.rfind("\n", 0, n), text.rfind(" ", 0, n))
        cut = cut if cut > n // 2 else n
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return out + ([text] if text else [])


# ---------------------------------------------------------------- conversations

def chat_id_for(channel: str, chat: str) -> str:
    return f"{channel}_" + re.sub(r"[^A-Za-z0-9_-]", "_", str(chat))[:60]


# True in a process answering a chat app. Who may talk is changed only from the
# PC itself: from a phone, someone already let in could let in others, and the
# "may I" question would go to that same person.
IN_A_CHAT_APP = False

_YES = re.compile(r"^\s*(y|ye|yes|yeah|yep|ok|okay|sure|go|go ahead|do it|allow|approve)\b", re.I)


class Hub:
    """Runs each message an adapter receives. One reply at a time per chat;
    messages that arrive meanwhile answer its question or join it."""

    def __init__(self, adapter: Adapter, configured_senders=(), persona: str = ""):
        global IN_A_CHAT_APP
        IN_A_CHAT_APP = True
        self.a = adapter
        self.configured = {str(x) for x in configured_senders or ()}
        self.persona = persona or _persona_name()
        self._busy: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.threads: List[threading.Thread] = []

    # Called by the adapter, from whatever thread it receives on.
    def receive(self, chat: str, sender: str, text: str = "", name: str = "", reply_to: Any = None,
                voice: str = "", files: Optional[List[str]] = None) -> str:
        """What became of the message: 'ran', 'answered', 'joined', 'pairing',
        'ignored' or 'empty'."""
        chat, sender = str(chat), str(sender)
        if not is_allowed(self.a.name, sender, self.configured):
            code = request_code(self.a.name, sender, name)
            if code and self.a.replies_to_strangers:
                self.a.send(chat, f"Hi! I'm {self.persona}, and I only answer people my owner lets in. "
                                  f"Ask them to approve code {code} in Hearth (it lasts an hour).")
                return "pairing"
            return "ignored"
        if voice:
            heard = _transcribe(voice)
            text = (text + "\n" if text else "") + (heard or "(a voice note I couldn't make out)")
        for f in files or []:
            text = (text + "\n" if text else "") + (
                f"[They sent {os.path.basename(f)}, saved at {f}. Look at it with view_image if it's a picture, "
                f"or read_file, before answering about it.]")
        text = text.strip()
        if not text:
            return "empty"
        with self._lock:
            run = self._busy.get(chat)
            if run is not None:
                q = run.get("question")
                if q is not None and not q.is_set():
                    run["answer"] = bool(_YES.match(text))
                    q.set()
                    return "answered"
                run["steer"].append(text)
                return "joined"
            run = {"steer": [], "question": None, "answer": None}
            self._busy[chat] = run
        t = threading.Thread(target=self._turn, args=(chat, text, reply_to, run), daemon=True,
                             name=f"{self.a.name}-turn")
        self.threads.append(t)
        t.start()
        return "ran"

    def _ask(self, chat: str, run: Dict[str, Any], name: str, args: Dict[str, Any]) -> str:
        from .tools import mode_allows
        if mode_allows(name, args):
            return "allow"
        ev = threading.Event()
        with self._lock:
            run["question"], run["answer"] = ev, None
        self.a.send(chat, f"Before I go on: may I {_what(name, args)}? Reply yes to let me, anything else to stop.")
        ev.wait(ASK_TIMEOUT_S)
        with self._lock:
            run["question"] = None
            ok = bool(run.get("answer"))
        return "allow" if ok else "deny"

    def _turn(self, chat: str, text: str, reply_to: Any, run: Dict[str, Any]) -> None:
        from . import bridge_status, chat_sync, headless
        from .redact import redact_secrets_only
        cid = chat_id_for(self.a.name, chat)
        saved = chat_sync.load(cid) or {}
        history = chat_sync.from_chat(saved.get("messages") or [])[-16:]
        primer = bridge_status.channel_primer(self.a.label)
        parts: List[str] = []
        tools_used: List[Any] = []
        status = {"id": None, "last": 0.0}
        err = {"cat": None, "msg": None}
        try:
            self.a.typing(chat)
        except Exception:
            pass

        def emit(kind, **kw):
            if kind == "assistant":
                parts.append(kw.get("content") or "")
            elif kind == "error":
                err["cat"], err["msg"] = kw.get("category") or "error", str(kw.get("message", ""))
            elif kind == "tool_call" and kw.get("name"):
                tools_used.append((kw["name"], kw.get("args")))
                now = time.time()
                if self.a.can_edit and now - status["last"] >= 1.0:
                    status["last"] = now
                    body = bridge_status.format_status(tools_used, working=True)
                    try:
                        if status["id"] is None:
                            status["id"] = self.a.send(chat, body, reply_to)
                        elif not self.a.edit(chat, status["id"], body):
                            pass
                    except Exception:
                        pass

        def steer() -> List[str]:
            with self._lock:
                said, run["steer"] = list(run["steer"]), []
            return said

        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(headless.run_once(
                    text, emit=emit, history=[primer] + history, chat_id=cid,
                    permission_check=lambda n, a: self._ask(chat, run, n, a),
                    drain_steer=steer, supervised=False))
            finally:
                loop.close()
        except Exception as e:
            from .errors import classify_api_error
            info = classify_api_error(e, headless._is_local_endpoint(headless.LOCAL_API_BASE))
            err["cat"], err["msg"] = info.category, info.hint
        reply = headless.bridge_reply_or_reason("".join(parts), err["cat"], err["msg"])
        if tools_used and not self.a.can_edit:
            foot = bridge_status.footer(tools_used)
            if foot:
                reply = (reply + "\n\n" + foot).strip()
        final = redact_secrets_only(reply or "(no output)")[0]
        try:
            if status["id"] is not None and tools_used:
                self.a.edit(chat, status["id"], bridge_status.format_done(tools_used))
            for piece in split(final, self.a.max_len):
                self.a.send(chat, piece, reply_to)
            for f in bridge_status.files_in(reply, 45_000_000):
                self.a.send_file(chat, f)
        except Exception as e:
            print(f"[{self.a.name}] couldn't send the reply: {type(e).__name__}: {e}")
        now = int(time.time() * 1000)
        events = [{"kind": "tool", "name": n, "args": a or {}, "result": "", "ms": 0, "pos": 0} for n, a in tools_used]
        chat_sync.post(cid, f"{self.a.label}: {text[:50]}", self.a.name, [
            {"role": "user", "content": text, "via": self.a.name, "at": now},
            {"role": "assistant", "content": final, "via": self.a.name, "at": now, **({"events": events} if events else {})}])
        with self._lock:
            self._busy.pop(chat, None)
        return None


# ---------------------------------------------------------------- sending first

CONFIG_FILES = {"telegram": "phone_bridge.json", "discord": "discord_bridge.json",
                "whatsapp": "whatsapp_bridge.json"}
_OWNER_KEYS = {"telegram": "allowed_chat_ids", "discord": "allowed_user_ids", "whatsapp": "allowed_numbers"}


def config_of(channel: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(os.path.expanduser("~"), ".hearth", CONFIG_FILES[channel]), encoding="utf-8") as f:
            return json.load(f) or {}
    except (KeyError, OSError, ValueError):
        return {}


def owner_of(channel: str) -> str:
    """Who Hearth writes to first on an app: the first id in its settings, else
    the first person let in by pairing."""
    ids = config_of(channel).get(_OWNER_KEYS.get(channel, ""), []) or []
    if ids:
        return str(ids[0])
    return next((p["sender"] for p in approved(channel) if p["sender"] != "self"), "")


def push(channel: str, text: str) -> str:
    """Send a message to the owner on an app, from this PC. A sentence saying
    what happened; one starting "Error" when it didn't."""
    import urllib.parse
    import urllib.request
    cfg = config_of(channel)
    token, who = (cfg.get("bot_token") or "").strip(), owner_of(channel)
    if channel == "whatsapp":
        return "Error: WhatsApp can only answer messages; it can't start a conversation from this PC."
    if channel not in ("telegram", "discord"):
        return f"Error: no app called {channel} (telegram or discord)."
    if not token:
        return f"Error: {channel.title()} isn't connected (Settings > Connectors)."
    if not who:
        return f"Error: nobody is let in on {channel.title()} yet: message the bot and approve the code it gives."
    try:
        if channel == "telegram":
            data = urllib.parse.urlencode({"chat_id": who, "text": text[:4000]}).encode()
            urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Hearth"}), timeout=12)
            return "Sent to your Telegram."
        h = {"Authorization": f"Bot {token}", "Content-Type": "application/json",
             "User-Agent": "Hearth (https://github.com/0pen-sourcer/hearth, 0.7)"}
        chan = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://discord.com/api/v10/users/@me/channels", data=json.dumps({"recipient_id": who}).encode(),
            headers=h), timeout=12).read().decode())
        urllib.request.urlopen(urllib.request.Request(
            f"https://discord.com/api/v10/channels/{chan['id']}/messages",
            data=json.dumps({"content": text[:1900]}).encode(), headers=h), timeout=12)
        return "Sent to your Discord (a DM)."
    except Exception as e:
        return f"Error sending to {channel.title()}: {type(e).__name__}: {e}"


def push_any(text: str) -> bool:
    """To whichever app is connected, Telegram first. False when none could."""
    for ch in ("telegram", "discord"):
        if config_of(ch).get("bot_token") and owner_of(ch):
            if not push(ch, text).startswith("Error"):
                return True
    return False


def use_saved_brain(log: Callable[[str], None] = print) -> None:
    """A bridge started on its own (not by the window, which passes the brain
    in its environment) thinks with the brain saved in settings, not a default."""
    if os.environ.get("LOCAL_API_BASE"):
        return
    try:
        from .tools import WORKSPACE
        with open(os.path.join(WORKSPACE, "settings.json"), encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        return
    for key, env in (("llm_url", "LOCAL_API_BASE"), ("llm_key", "LOCAL_API_KEY"), ("llm_model", "LOCAL_MODEL")):
        if s.get(key):
            os.environ[env] = str(s[key])
    log(f"thinking with the saved brain: {s.get('llm_model') or s.get('llm_url') or 'the default'}")


def _persona_name() -> str:
    try:
        from .persona import NAME
        return NAME
    except Exception:
        return "JARVIS"


def _what(name: str, args: Dict[str, Any]) -> str:
    """A risky step in a few words, for the question."""
    a = args or {}
    if name == "run_command":
        return f"run `{str(a.get('command') or '')[:160]}`"
    if name in ("write_file", "edit_file", "append_file"):
        return f"change {a.get('path') or 'a file'}"
    if name in ("delete_file", "delete_path"):
        return f"delete {a.get('path') or 'a file'}"
    if name in ("send_email",):
        return f"send an email to {a.get('to') or 'someone'}"
    return f"use {name}" + (f" ({', '.join(f'{k}={str(v)[:40]}' for k, v in list(a.items())[:2])})" if a else "")


def _transcribe(path: str) -> str:
    try:
        from . import listen
        return listen.transcribe(path)
    except Exception:
        return ""


def save_upload(data: bytes, name: str) -> str:
    """Keep a file someone sent in the workspace's uploads folder."""
    from .tools import WORKSPACE
    folder = os.path.join(WORKSPACE, "uploads")
    os.makedirs(folder, exist_ok=True)
    base = re.sub(r"[^\w.\- ]", "_", os.path.basename(name or "file"))[:80] or "file"
    path = os.path.join(folder, base)
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{stem}-{n}{ext}"
        n += 1
    with open(path, "wb") as f:
        f.write(data)
    return path
