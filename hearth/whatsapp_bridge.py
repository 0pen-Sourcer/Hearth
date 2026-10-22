"""Talk to Hearth from WhatsApp, pure Python (no Node, no browser).

EXPERIMENTAL. It links a WhatsApp account as a companion device, the way
WhatsApp Web does, through `neonize` (bindings over the whatsmeow engine, built
into the wheel). You scan a QR once; the session is kept.

It is unofficial: WhatsApp has no bot API for personal accounts, so this drives
a real account and Meta can flag a number for automation. Use a spare number.
Telegram is the official, no-risk option.

Who may talk: the numbers in ~/.hearth/whatsapp_bridge.json ("allowed_numbers"),
and with "allow_self_chat" the linked number's own "you" chat (what it sends to
anyone else is left alone). Anyone else gets no reply, which on a personal
number would look like spam; their request waits in Settings > Connectors.

Everything else is shared with the other apps (hearth/channel_core.py).

    python -m hearth.whatsapp_bridge
"""
from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path

from . import channel_core as cc
from . import env as _env

_env.setdefault("NO_GUI", "1")

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "whatsapp_bridge.json")
_SESSION_DB = os.path.join(os.path.expanduser("~"), ".hearth", "whatsapp", "session.sqlite3")
_WA_MAX = 4000
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_HUB = None


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _msg_text(message) -> str:
    """The text of a message: plain, extended, or a photo's / file's caption."""
    try:
        m = message.Message
        for part in ("extendedTextMessage", "imageMessage", "videoMessage", "documentMessage"):
            inner = getattr(m, part, None)
            t = getattr(inner, "text", "") or getattr(inner, "caption", "") if inner is not None else ""
            if t:
                return t
        return getattr(m, "conversation", "") or ""
    except Exception:
        return ""


def _sender_number(message) -> str:
    try:
        return str(message.Info.MessageSource.Sender.User)
    except Exception:
        return ""


def _own_ids(client, src) -> set:
    """Every id the linked account goes by: its phone number and its LID."""
    ids = set()
    for jid in (getattr(src, "Sender", None), getattr(src, "SenderAlt", None)):
        if getattr(jid, "User", ""):
            ids.add(str(jid.User))
    try:
        me = client.get_me()
        for jid in (me.JID, me.LID):
            if getattr(jid, "User", ""):
                ids.add(str(jid.User))
    except Exception:
        pass
    return ids


def _is_own_chat(src, own: set) -> bool:
    """The account's own "you" chat. A message it sent anywhere else is a
    conversation with someone else and never an instruction."""
    return str(getattr(getattr(src, "Chat", None), "User", "") or "") in own


def _has(m, part: str) -> bool:
    try:
        return m.HasField(part)
    except Exception:
        return getattr(m, part, None) is not None


class WhatsApp(cc.Adapter):
    name, label, max_len = "whatsapp", "WhatsApp", _WA_MAX
    replies_to_strangers = False
    can_edit = False

    def __init__(self, client):
        self.client = client
        self.chats = {}
        self.sent_ids: deque = deque(maxlen=256)

    def _remember(self, resp):
        mid = getattr(resp, "ID", "")
        if mid:
            self.sent_ids.append(str(mid))
        return mid or None

    def send(self, chat, text, reply_to=None):
        jid = self.chats.get(str(chat))
        if jid is None:
            return None
        try:
            return self._remember(self.client.send_message(jid, text))
        except Exception as e:
            print(f"[whatsapp] couldn't send: {type(e).__name__}: {e}")
            return None

    def send_file(self, chat, path):
        jid = self.chats.get(str(chat))
        if jid is None:
            return
        try:
            if path.lower().endswith(_IMAGE_EXTS):
                self._remember(self.client.send_image(jid, path))
            else:
                self._remember(self.client.send_document(jid, path, filename=os.path.basename(path)))
        except Exception as e:
            print(f"[whatsapp] could not send {os.path.basename(path)}: {type(e).__name__}: {e}")


def handle(hub: cc.Hub, wa: WhatsApp, allowed: set, allow_self: bool, message) -> str:
    """One WhatsApp message, given to the shared hub."""
    src = message.Info.MessageSource
    if getattr(src, "IsGroup", False):
        return "ignored"
    if str(getattr(message.Info, "ID", "")) in wa.sent_ids:
        return "ignored"                         # a reply this bridge sent
    number = _sender_number(message)
    if getattr(src, "IsFromMe", False):
        if not (allow_self and _is_own_chat(src, _own_ids(wa.client, src))):
            return "ignored"
        sender = "self"
    else:
        sender = number
        if number in allowed:
            hub.configured.add(number)
    m = message.Message
    files, voice = [], ""
    for part, name in (("imageMessage", "photo.jpg"), ("documentMessage", ""), ("videoMessage", "video.mp4"),
                       ("audioMessage", "voice.ogg")):
        if not _has(m, part):
            continue
        inner = getattr(m, part)
        fname = getattr(inner, "fileName", "") or name or "file"
        try:
            data = wa.client.download_any(m)
        except Exception:
            data = None
        if not data:
            continue
        path = cc.save_upload(data, fname)
        if part == "audioMessage":
            voice = path
        else:
            files.append(path)
    chat = str(getattr(src.Chat, "User", "") or number)
    wa.chats[chat] = src.Chat
    if sender == "self":
        hub.configured.add("self")
    return hub.receive(chat, sender, _msg_text(message).strip(), voice=voice, files=files)


def run() -> None:
    global _HUB
    try:
        from neonize.client import NewClient
        from neonize.events import MessageEv, ConnectedEv, QREv, PairStatusEv, LoggedOutEv
    except Exception as e:
        print(f"[whatsapp] neonize isn't available ({e}); Settings > Connectors installs it")
        return
    cfg = _load_config()
    allowed = {str(x).lstrip("+").strip() for x in (cfg.get("allowed_numbers") or [])}
    allow_self = bool(cfg.get("allow_self_chat"))
    cc.use_saved_brain(lambda s: print("[whatsapp] " + s))
    Path(os.path.dirname(_SESSION_DB)).mkdir(parents=True, exist_ok=True)
    client = NewClient(_SESSION_DB)
    wa = WhatsApp(client)
    _HUB = hub = cc.Hub(wa, configured_senders=allowed)

    @client.event(QREv)
    def _on_qr(_c, qr):
        code = getattr(qr, "Codes", None) or getattr(qr, "codes", None) or getattr(qr, "Code", "")
        if isinstance(code, (list, tuple)):
            code = code[0] if code else ""
        print("\n[whatsapp] Scan this in WhatsApp, Settings, Linked Devices, Link a Device:\n")
        try:
            import segno
            segno.make(str(code)).terminal(compact=True)
        except Exception:
            print(str(code))
        print("\n[whatsapp] (the QR refreshes now and then; scan the newest)\n")

    @client.event(ConnectedEv)
    def _on_connected(_c, _e):
        print("[whatsapp] connected, linked and listening.")

    @client.event(PairStatusEv)
    def _on_pair(_c, e):
        print(f"[whatsapp] paired: {getattr(getattr(e, 'ID', None), 'User', '')}")

    @client.event(LoggedOutEv)
    def _on_logout(_c, _e):
        print("[whatsapp] logged out: unlink in Settings > Connectors and link again.")

    @client.event(MessageEv)
    def _on_message(_c, message):
        try:
            src = message.Info.MessageSource
            if handle(hub, wa, allowed, allow_self, message) == "ignored" and not getattr(src, "IsFromMe", False) \
                    and not getattr(src, "IsGroup", False) and str(getattr(message.Info, "ID", "")) not in wa.sent_ids:
                print(f"[whatsapp] {_sender_number(message)} isn't let in; their request waits in Settings > Connectors.")
        except Exception as e:
            print(f"[whatsapp] a message failed: {type(e).__name__}: {e}")

    print("[whatsapp] starting. Ctrl-C to stop. The first run shows a QR to scan.")
    print("[whatsapp] use a spare number: this is unofficial and experimental.")
    client.connect()


if __name__ == "__main__":
    from .bridge_status import follow_parent
    follow_parent()
    run()
