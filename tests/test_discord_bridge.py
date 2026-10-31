"""What a Discord message turns into: DMs always, a server only when mentioned
and only for people already let in (no pairing codes in public), attachments
kept, voice messages handed over to be written down, bots ignored.

    .\\.venv\\Scripts\\python.exe tests\\test_discord_bridge.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-dc-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
sys.path.insert(0, ROOT)

from hearth import channel_core as cc, discord_bridge as D  # noqa: E402

cc.ACCESS_FILE = os.path.join(WS, "channel_access.json")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ME = 999


class Hub:
    configured = {"42"}

    def __init__(self):
        self.got = []

    def receive(self, chat, sender, text="", **k):
        self.got.append({"chat": chat, "sender": sender, "text": text, **k})
        return "ran"


class Att:
    def __init__(self, name, ctype, voice=False):
        self.filename, self.content_type, self.size, self._v = name, ctype, 10, voice

    async def read(self):
        return b"data"

    def is_voice_message(self):
        return self._v


def msg(text="", author=42, bot=False, guild=None, mentions=(), atts=()):
    return NS(content=text, author=NS(id=author, bot=bot, display_name="Ishant"), guild=guild,
              mentions=[NS(id=m) for m in mentions], attachments=list(atts), channel=NS(id=555))


async def main():
    hub, dc = Hub(), D.Discord(None, asyncio.get_running_loop())
    await D.handle(hub, dc, ME, msg("hello"))
    check("a DM goes to the hub, with who sent it", hub.got[-1]["text"] == "hello" and hub.got[-1]["name"] == "Ishant"
          and "555" in dc.channels)
    n = len(hub.got)
    r = await D.handle(hub, dc, ME, msg("hello all", guild=NS(id=1)))
    check("in a server, nothing unless mentioned", r == "ignored" and len(hub.got) == n)
    await D.handle(hub, dc, ME, msg(f"<@{ME}> what's up", guild=NS(id=1), mentions=[ME]))
    check("mentioned, it answers and the mention is taken out", hub.got[-1]["text"] == "what's up")
    r = await D.handle(hub, dc, ME, msg(f"<@{ME}> hi", author=77, guild=NS(id=1), mentions=[ME]))
    check("a stranger in a server isn't given a code in public", r == "ignored" and not cc.pending())
    await D.handle(hub, dc, ME, msg("look", atts=[Att("pic.png", "image/png"), Att("voice-message.ogg", "audio/ogg", True)]))
    got = hub.got[-1]
    check("an attachment is kept and a voice message handed over",
          len(got["files"]) == 1 and got["files"][0].endswith("pic.png") and got["voice"].endswith(".ogg"))
    n = len(hub.got)
    check("another bot is never answered", await D.handle(hub, dc, ME, msg("hi", author=5, bot=True)) == "ignored"
          and len(hub.got) == n)

asyncio.run(main())
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all discord bridge checks passed")
