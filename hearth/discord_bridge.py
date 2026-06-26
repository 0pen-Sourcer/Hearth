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
_LOG_PATH = os.path.join(os.path.expanduser("~"), "Jarvis", "logs", "discord_bridge.log")


def _log(msg: str) -> None:
    """Print AND append to a log file. The bridge is usually spawned windowless
    by the GUI, so a file is the only way to see why it did or didn't respond."""
    line = "[discord] " + msg
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        import time as _t
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(_t.strftime("%H:%M:%S ") + line + "\n")
    except Exception:
        pass


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


async def _run(prompt: str, history: list, on_tool=None) -> str:
    from . import headless
    parts = []
    events = []

    def emit(kind, **kw):
        if kind == "assistant":
            parts.append(kw.get("content") or "")
        elif kind == "error":
            parts.append("[error] " + str(kw.get("message", "")))
        elif kind == "tool_call":
            nm = kw.get("name")
            if nm:
                events.append((nm, kw.get("args")))
                if on_tool:
                    on_tool(list(events))  # live "which tool is firing" feed

    try:
        await headless.run_once(prompt, emit=emit, history=history,
                                permission_check=lambda _n, _a: "allow",
                                supervised=False)  # phone: destructive guard still fires
    except Exception as e:
        return f"(run failed: {type(e).__name__}: {e})"
    return "".join(parts).strip()


def run() -> None:
    try:
        import discord
    except Exception as e:
        _log(f"discord.py not available: {e} (pip install discord.py)")
        return
    cfg = _load_config()
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        _log(f"no bot_token in {CONFIG_PATH}")
        return
    allowed = set(int(x) for x in (cfg.get("allowed_user_ids") or []))
    if not allowed:
        _log("WARNING: allowed_user_ids is empty - I'll log incoming ids but answer nobody.")
    if cfg.get("ntfy_topic"):
        os.environ.setdefault("HEARTH_NTFY_TOPIC", cfg["ntfy_topic"])
    # Brain: the GUI sets LOCAL_API_BASE in the env it spawns us with. If we were
    # started directly (no env), load the saved brain from settings.json so the
    # bridge talks to the same model the app does (e.g. Grok), not the localhost
    # default. Without this a directly-run bridge answered from the wrong brain.
    if not os.environ.get("LOCAL_API_BASE"):
        try:
            from .tools import WORKSPACE
            with open(os.path.join(WORKSPACE, "settings.json"), encoding="utf-8") as f:
                _s = json.load(f)
            if _s.get("llm_url"):
                os.environ["LOCAL_API_BASE"] = _s["llm_url"]
            if _s.get("llm_key"):
                os.environ["LOCAL_API_KEY"] = _s["llm_key"]
            if _s.get("llm_model"):
                os.environ["LOCAL_MODEL"] = _s["llm_model"]
            _log(f"brain from settings: {_s.get('llm_url') or 'localhost default'} model={_s.get('llm_model') or '?'}")
        except Exception as e:
            _log(f"no saved brain loaded ({type(e).__name__}); using localhost default")

    # IMPORTANT: do NOT request the privileged message_content intent. Discord
    # delivers message text for DMs and for messages that @mention the bot even
    # without it - which are exactly our two response cases - so the bridge works
    # with ZERO dev-portal setup. Requesting it while it's OFF in the portal
    # instead crashes the bot on connect (PrivilegedIntentsRequired), which is
    # what made "@mention does nothing" happen.
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    histories: dict = {}

    @client.event
    async def on_ready():
        _log(f"bridge ONLINE as {client.user}. DM it or @mention it.")

    @client.event
    async def on_message(message):
        if message.author == client.user:
            return
        uid = message.author.id
        is_dm = message.guild is None
        mentioned = client.user in message.mentions
        # Diagnostic line — visible when the bridge runs in a terminal. Makes
        # "it just doesn't respond" debuggable (who messaged, did we get text).
        _log(f"msg uid={uid} dm={is_dm} mentioned={mentioned} content_len={len(message.content or '')}")
        if uid not in allowed:
            _log(f"ignored: uid {uid} not in allowed_user_ids {sorted(allowed)}")
            return
        # In a guild channel, only respond when mentioned; in a DM, always.
        if not is_dm and not mentioned:
            return
        text = message.content or ""
        if mentioned:
            text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
        if not text:
            # Mentions and DMs deliver text without any intent, so an empty body
            # here is rare (an attachment-only message, say). Nudge gently.
            _log("empty body after mention strip - nothing to act on")
            try:
                await message.channel.send("Got your ping, but no text to act on. Type your request right after the mention.")
            except Exception:
                pass
            return
        hist = histories.setdefault(uid, [])
        # Live tool feed: one status message that edits itself as tools fire, so
        # the user watches the work instead of a silent pause then a wall of text.
        # Created lazily on the first tool call (a pure-chat reply shows none).
        import asyncio
        from . import bridge_status
        state = {"events": [], "dirty": False, "msg": None}

        def on_tool(events):
            state["events"] = events
            state["dirty"] = True

        stop = asyncio.Event()

        async def _updater():
            while not stop.is_set():
                await asyncio.sleep(0.6)
                if not state["dirty"]:
                    continue
                state["dirty"] = False
                body = bridge_status.format_status(state["events"], working=True)[:_DISCORD_MAX]
                try:
                    if state["msg"] is None:
                        state["msg"] = await message.channel.send(body)
                    else:
                        await state["msg"].edit(content=body)
                except Exception:
                    pass

        updater = asyncio.create_task(_updater())
        try:
            reply = await _run(text, hist, on_tool=on_tool)
        except Exception as e:
            reply = f"(error: {type(e).__name__}: {e})"
        stop.set()
        try:
            await updater
        except Exception:
            pass
        hist.append({"role": "user", "content": text})
        hist.append({"role": "assistant", "content": reply})
        del hist[:-16]
        chunks = _split(reply or "(no output)")
        # Morph the live status message into the actual answer (first chunk), so
        # the same bubble goes work -> result. Fall back to a fresh send.
        first = chunks[0] if chunks else "(no output)"
        if state["msg"] is not None:
            try:
                await state["msg"].edit(content=first)
            except Exception:
                try:
                    await message.channel.send(first)
                except Exception:
                    pass
        else:
            try:
                await message.channel.send(first)
            except Exception:
                pass
        for chunk in chunks[1:]:
            try:
                await message.channel.send(chunk)
            except Exception:
                pass
        for f in _files_in(reply):
            try:
                await message.channel.send(file=discord.File(f))
            except Exception:
                pass

    _log("starting - connecting to Discord gateway")
    try:
        client.run(token)
    except Exception as e:
        _log(f"failed to connect: {type(e).__name__}: {e} (is the bot token valid? is another copy already using it?)")


if __name__ == "__main__":
    run()
