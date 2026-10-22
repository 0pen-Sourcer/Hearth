"""Talk to Hearth from Discord, through a bot of your own (official API).

Setup: discord.com/developers/applications, New Application, Bot, copy the
token and give it to Hearth (Settings > Connectors, or tell JARVIS "connect my
Discord"). DM the bot: you get a pairing code to approve in Hearth, once. In a
server it answers only when @mentioned, and only people already let in.
User ids in ~/.hearth/discord_bridge.json ("allowed_user_ids") are answered
without a code. No privileged intents are needed: Discord delivers the text of
DMs and of messages that mention the bot without them.

Everything else is shared with the other apps (hearth/channel_core.py).

    python -m hearth.discord_bridge
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from . import channel_core as cc
from . import env as _env

_env.setdefault("NO_GUI", "1")

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "discord_bridge.json")
_DISCORD_MAX = 2000
_FILE_MAX = 24_000_000


def _log(msg: str) -> None:
    """One timestamped line; started from the window it goes to logs/discord_bridge.log."""
    try:
        print(time.strftime("%H:%M:%S ") + "[discord] " + msg, flush=True)
    except Exception:
        pass


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


class Discord(cc.Adapter):
    """The hub works from its own threads; Discord's client lives on its event
    loop, so every call is handed over to that loop."""
    name, label, max_len = "discord", "Discord", _DISCORD_MAX

    def __init__(self, client, loop):
        self.client, self.loop = client, loop
        self.channels, self.sent = {}, {}

    def _do(self, coro, wait: float = 30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(wait)

    def send(self, chat, text, reply_to=None):
        ch = self.channels.get(str(chat))
        if ch is None:
            return None
        try:
            m = self._do(ch.send((text or "")[:_DISCORD_MAX]))
            self.sent[m.id] = m
            return m.id
        except Exception:
            return None

    def edit(self, chat, msg_id, text):
        m = self.sent.get(msg_id)
        if m is None:
            return False
        try:
            self._do(m.edit(content=(text or "")[:_DISCORD_MAX]))
            return True
        except Exception:
            return False

    def typing(self, chat):
        ch = self.channels.get(str(chat))
        if ch is not None:
            async def once():
                await ch.typing()                 # one "typing…" that Discord clears by itself
            try:
                self._do(once(), wait=5)
            except Exception:
                pass

    def send_file(self, chat, path):
        import discord
        ch = self.channels.get(str(chat))
        if ch is None or os.path.getsize(path) > _FILE_MAX:
            return
        try:
            self._do(ch.send(file=discord.File(path)), wait=120)
        except Exception:
            pass


async def handle(hub: cc.Hub, dc: Discord, me_id: int, message) -> str:
    """One Discord message, given to the shared hub."""
    if message.author.bot or message.author.id == me_id:
        return "ignored"                          # never itself, never another bot
    is_dm = message.guild is None
    mentioned = any(getattr(u, "id", None) == me_id for u in message.mentions)
    sender = str(message.author.id)
    if not is_dm:
        # In a server: only when mentioned, and no pairing codes in public.
        if not mentioned or not cc.is_allowed(dc.name, sender, hub.configured):
            return "ignored"
    text = (message.content or "").replace(f"<@{me_id}>", "").replace(f"<@!{me_id}>", "").strip()
    files, voice = [], ""
    for att in message.attachments or []:
        if (att.size or 0) > _FILE_MAX:
            continue
        try:
            path = cc.save_upload(await att.read(), att.filename or "file")
        except Exception:
            continue
        is_voice = getattr(att, "is_voice_message", lambda: False)()
        if is_voice or (att.content_type or "").startswith("audio/"):
            voice = voice or path
        else:
            files.append(path)
    chat = str(message.channel.id)
    dc.channels[chat] = message.channel
    return await asyncio.to_thread(hub.receive, chat, sender, text, name=str(message.author.display_name or ""),
                                   voice=voice, files=files)


def run() -> None:
    try:
        import discord
    except Exception as e:
        _log(f"discord.py isn't installed ({e}); Settings > Connectors installs it")
        return
    cfg = _load_config()
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        _log(f"no bot token yet: add one in Settings > Connectors ({CONFIG_PATH})")
        return
    cc.use_saved_brain(_log)
    client = discord.Client(intents=discord.Intents.default())
    box = {}

    @client.event
    async def on_ready():
        box["dc"] = Discord(client, asyncio.get_running_loop())
        box["hub"] = cc.Hub(box["dc"], configured_senders=cfg.get("allowed_user_ids") or [])
        _log(f"bridge up as {client.user}. DM it or @mention it.")

    @client.event
    async def on_message(message):
        if "hub" not in box:
            return
        try:
            what = await handle(box["hub"], box["dc"], client.user.id, message)
            if what == "pairing":
                _log("someone new asked to talk; their code is waiting in Settings > Connectors.")
        except Exception as e:
            _log(f"a message failed: {type(e).__name__}: {e}")

    _log("connecting to Discord")
    try:
        client.run(token, log_handler=None)
    except Exception as e:
        _log(f"failed to connect: {type(e).__name__}: {e} (is the bot token right? is another copy using it?)")


if __name__ == "__main__":
    from .bridge_status import follow_parent
    follow_parent()
    run()
