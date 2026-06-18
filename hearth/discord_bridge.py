"""Reach Hearth from Discord — bot token only, official API, no ban risk.

The clean two-way phone/desktop channel: a Discord bot you DM (or @mention),
relayed to Hearth on your PC, which replies with the answer and any file it made.
Unlike WhatsApp this is an official, supported bot API — nothing to get banned
for, nothing that breaks on a protocol change.

Setup (~3 minutes):
  1. https://discord.com/developers/applications -> New Application -> Bot.
     Copy the bot token. Under "Privileged Gateway Intents", turn ON
     "MESSAGE CONTENT INTENT" (required to read message text).
  2. Invite the bot to a server you own, OR just DM it (enable DMs).
     To find your own user id: Discord Settings -> Advanced -> Developer Mode on,
     then right-click your name -> Copy User ID.
  3. Create ~/.hearth/discord_bridge.json:
       {"bot_token": "...", "allowed_user_ids": [<your id>], "ntfy_topic": ""}
  4. Point it at a brain (same LOCAL_API_BASE / LOCAL_API_KEY / LOCAL_MODEL env
     the CLI uses), then run:  python -m hearth.discord_bridge

Single-owner: only allowed_user_ids are answered; messages auto-approve tool
calls (you're the owner). Keep the allowlist tight and the token out of any repo.
"""
from __future__ import annotations

import json
import os

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "discord_bridge.json")
_DISCORD_MAX = 2000  # Discord's hard per-message character cap


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _split(s: str, n: int = _DISCORD_MAX):
    s = s or "(no output)"
    out = []
    while s:
        if len(s) <= n:
            out.append(s); break
        cut = s.rfind("\n", 0, n)
        if cut < n // 2:
            sp = s.rfind(" ", 0, n)
            cut = sp if sp > 0 else n
        out.append(s[:cut]); s = s[cut:].lstrip("\n")
    return out


import re as _re
_PATH_RE = _re.compile(r"[A-Za-z]:[\\/][^\s'\"<>|]+\.[A-Za-z0-9]{1,5}")


def _files_in(text: str):
    seen, out = set(), []
    for m in _PATH_RE.findall(text or ""):
        p = m.rstrip(".,);")
        if p not in seen and os.path.isfile(p) and os.path.getsize(p) < 24_000_000:
            seen.add(p); out.append(p)
    return out[:4]


async def _run(prompt: str, history: list) -> str:
    from . import headless
    parts = []

    def emit(kind, **kw):
        if kind == "assistant":
            parts.append(kw.get("content") or "")
        elif kind == "error":
            parts.append("[error] " + str(kw.get("message", "")))

    try:
        await headless.run_once(prompt, emit=emit, history=history,
                                permission_check=lambda _n, _a: "allow")
    except Exception as e:
        return f"(run failed: {type(e).__name__}: {e})"
    return "".join(parts).strip()


def run() -> None:
    try:
        import discord
    except Exception as e:
        print(f"[discord] discord.py not available: {e}\n  pip install discord.py")
        return
    cfg = _load_config()
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        print(f"[discord] no bot_token in {CONFIG_PATH} — see the module docstring.")
        return
    allowed = set(int(x) for x in (cfg.get("allowed_user_ids") or []))
    if not allowed:
        print("[discord] WARNING: allowed_user_ids is empty — I'll log incoming "
              "user ids but answer nobody. Add yours to the config.")
    if cfg.get("ntfy_topic"):
        os.environ.setdefault("HEARTH_NTFY_TOPIC", cfg["ntfy_topic"])

    intents = discord.Intents.default()
    intents.message_content = True  # privileged — must be enabled in the dev portal
    client = discord.Client(intents=intents)
    histories: dict = {}

    @client.event
    async def on_ready():
        print(f"[discord] bridge up as {client.user} — DM it or @mention it. Ctrl-C to stop.")

    @client.event
    async def on_message(message):
        if message.author == client.user:
            return
        uid = message.author.id
        if uid not in allowed:
            print(f"[discord] ignored message from user id {uid} "
                  f"(add it to allowed_user_ids to enable).")
            return
        # In a guild channel, only respond when mentioned; in a DM, always.
        is_dm = message.guild is None
        if not is_dm and client.user not in message.mentions:
            return
        text = message.content
        if client.user in message.mentions:
            text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
        if not text:
            return
        hist = histories.setdefault(uid, [])
        try:
            async with message.channel.typing():
                reply = await _run(text, hist)
        except Exception as e:
            reply = f"(error: {type(e).__name__}: {e})"
        hist.append({"role": "user", "content": text})
        hist.append({"role": "assistant", "content": reply})
        del hist[:-16]
        for chunk in _split(reply or "(no output)"):
            try:
                await message.channel.send(chunk)
            except Exception:
                pass
        for f in _files_in(reply):
            try:
                await message.channel.send(file=discord.File(f))
            except Exception:
                pass

    print("[discord] starting — connecting to Discord gateway…")
    try:
        client.run(token)
    except Exception as e:
        print(f"[discord] failed: {type(e).__name__}: {e}\n"
              "  (check the token, and that MESSAGE CONTENT INTENT is enabled in the dev portal)")


if __name__ == "__main__":
    run()
