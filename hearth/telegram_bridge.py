"""Talk to Hearth from Telegram, through a bot of your own (a bot token, no OAuth).

Setup: in Telegram, message @BotFather, /newbot, and give Hearth the token
(Settings > Connectors, or tell JARVIS "connect my Telegram"). Then message the
bot: you get a pairing code to approve in Hearth, once. Chat ids already in
~/.hearth/phone_bridge.json ("allowed_chat_ids") are answered without one.

It long-polls getUpdates (no public webhook), and everything else, who may
talk, the conversation, questions before risky steps, voice notes, photos and
files back, is shared with the other apps (hearth/channel_core.py).

    python -m hearth.telegram_bridge
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.request

from . import channel_core as cc
from . import env as _env

# A message from the phone has no one at this screen following along: keep the
# desktop-activity overlay off for these runs.
_env.setdefault("NO_GUI", "1")

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "phone_bridge.json")
_TG_MAX = 4096
_FILE_MAX = 20_000_000          # the most the Bot API lets a bot download


def _load_config() -> dict:
    from . import keystore
    return keystore.read_json(CONFIG_PATH)


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _post_json(url: str, payload: dict, timeout: float = 40.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Telegram(cc.Adapter):
    name, label, max_len = "telegram", "Telegram", _TG_MAX

    def __init__(self, token: str):
        self.token = token

    def send(self, chat, text, reply_to=None):
        body = {"chat_id": chat, "text": (text or "")[:_TG_MAX]}
        if reply_to:
            body["reply_to_message_id"] = reply_to
        try:
            r = _post_json(_api(self.token, "sendMessage"), body, timeout=20)
            return r["result"]["message_id"] if r.get("ok") else None
        except Exception:
            return None

    def edit(self, chat, msg_id, text):
        try:
            r = _post_json(_api(self.token, "editMessageText"),
                           {"chat_id": chat, "message_id": msg_id, "text": (text or "")[:_TG_MAX]}, timeout=15)
            return bool(r.get("ok"))
        except Exception:
            return False

    def typing(self, chat):
        try:
            _post_json(_api(self.token, "sendChatAction"), {"chat_id": chat, "action": "typing"}, timeout=10)
        except Exception:
            pass

    def send_file(self, chat, path):
        """A picture as a photo, anything else as a document."""
        try:
            with open(path, "rb") as fh:
                payload = fh.read()
        except OSError:
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        method, field = ("sendPhoto", "photo") if ctype.startswith("image/") and len(payload) < 10_000_000 \
            else ("sendDocument", "document")
        boundary = "----HearthFormBoundary" + os.urandom(8).hex()
        name = os.path.basename(path).replace('"', "")
        pre = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n"
               f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
               f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
        data = pre + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(_api(self.token, method), data=data, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            urllib.request.urlopen(req, timeout=120)
        except Exception:
            pass

    def download(self, file_id: str, name: str) -> str:
        """Fetch a file someone sent into the workspace; '' when it can't be."""
        try:
            info = _post_json(_api(self.token, "getFile"), {"file_id": file_id}, timeout=20)
            fp = (info.get("result") or {}).get("file_path")
            if not fp or int((info.get("result") or {}).get("file_size") or 0) > _FILE_MAX:
                return ""
            with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{fp}", timeout=120) as r:
                data = r.read()
            return cc.save_upload(data, name or os.path.basename(fp))
        except Exception:
            return ""


def handle(hub: cc.Hub, tg: Telegram, msg: dict) -> str:
    """One incoming Telegram message, given to the shared hub."""
    sender = msg.get("from") or {}
    if sender.get("is_bot"):
        return "ignored"                         # never answer another bot
    chat = (msg.get("chat") or {}).get("id")
    if chat is None:
        return "ignored"
    text = msg.get("text") or msg.get("caption") or ""
    files, voice = [], ""
    if msg.get("photo"):
        biggest = msg["photo"][-1]
        p = tg.download(biggest.get("file_id"), f"photo-{msg.get('message_id')}.jpg")
        if p:
            files.append(p)
    for key in ("document", "audio", "video"):
        if msg.get(key):
            d = msg[key]
            p = tg.download(d.get("file_id"), d.get("file_name") or f"{key}-{msg.get('message_id')}")
            if p:
                files.append(p)
    if msg.get("voice"):
        voice = tg.download(msg["voice"].get("file_id"), f"voice-{msg.get('message_id')}.ogg")
    who = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or sender.get("username") or ""
    return hub.receive(str(chat), str(chat), text, name=who, reply_to=msg.get("message_id"), voice=voice, files=files)


def run() -> None:
    cfg = _load_config()
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        print(f"[telegram] no bot token yet: add one in Settings > Connectors ({CONFIG_PATH}).")
        return
    tg = Telegram(token)
    hub = cc.Hub(tg, configured_senders=cfg.get("allowed_chat_ids") or [])
    try:
        _post_json(_api(token, "deleteWebhook"), {"drop_pending_updates": False}, timeout=10)
    except Exception:
        pass
    print("[telegram] bridge up, waiting for messages. Ctrl-C to stop.")
    offset = 0
    while True:
        try:
            r = _post_json(_api(token, "getUpdates"),
                           {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}, timeout=40)
        except Exception:
            time.sleep(5)
            continue
        if not r.get("ok"):
            time.sleep(5)                        # another poller or a webhook holds the token
            continue
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            try:
                what = handle(hub, tg, upd.get("message") or {})
                if what == "pairing":
                    print("[telegram] someone new asked to talk; their code is waiting in Settings > Connectors.")
            except Exception as e:
                print(f"[telegram] a message failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    from .bridge_status import follow_parent
    follow_parent()
    run()
