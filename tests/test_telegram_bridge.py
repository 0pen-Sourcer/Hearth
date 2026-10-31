"""What a Telegram message turns into: text, a photo or file fetched and kept,
a voice note handed over to be written down, and another bot ignored.

    .\\.venv\\Scripts\\python.exe tests\\test_telegram_bridge.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-tg-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
sys.path.insert(0, ROOT)

from hearth import telegram_bridge as T  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Hub:
    def __init__(self):
        self.got = []

    def receive(self, chat, sender, text="", **k):
        self.got.append({"chat": chat, "sender": sender, "text": text, **k})
        return "ran"


fetched = []


class TG(T.Telegram):
    def download(self, file_id, name):
        fetched.append((file_id, name))
        return os.path.join(WS, "uploads", name)


hub, tg = Hub(), TG("token")
me = {"id": 42, "first_name": "Ishant", "is_bot": False}
T.handle(hub, tg, {"message_id": 1, "chat": {"id": 42}, "from": me, "text": "hi"})
check("text goes to the hub with who sent it", hub.got[-1]["text"] == "hi" and hub.got[-1]["name"] == "Ishant"
      and hub.got[-1]["reply_to"] == 1)
T.handle(hub, tg, {"message_id": 2, "chat": {"id": 42}, "from": me, "caption": "what's this?",
                   "photo": [{"file_id": "small"}, {"file_id": "big"}]})
check("a photo is fetched at its largest and its caption kept",
      fetched[-1][0] == "big" and hub.got[-1]["text"] == "what's this?" and len(hub.got[-1]["files"]) == 1)
T.handle(hub, tg, {"message_id": 3, "chat": {"id": 42}, "from": me, "document": {"file_id": "d1", "file_name": "report.pdf"}})
check("a document keeps its name", fetched[-1] == ("d1", "report.pdf"))
T.handle(hub, tg, {"message_id": 4, "chat": {"id": 42}, "from": me, "voice": {"file_id": "v1"}})
check("a voice note is handed over to be written down", hub.got[-1]["voice"].endswith(".ogg") and fetched[-1][0] == "v1")
n = len(hub.got)
check("another bot is never answered",
      T.handle(hub, tg, {"message_id": 5, "chat": {"id": 7}, "from": {"id": 7, "is_bot": True}, "text": "hi"}) == "ignored"
      and len(hub.got) == n)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all telegram bridge checks passed")
