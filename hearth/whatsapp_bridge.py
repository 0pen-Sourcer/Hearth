"""Reach Hearth from WhatsApp — pure-Python, no Node, no Chromium.

EXPERIMENTAL. This links a WhatsApp account as a companion device (the same
"Linked Devices" mechanism as WhatsApp Web) via `neonize` — Python bindings over
the `whatsmeow` Go engine, shipped compiled inside the wheel, so there's no Node
runtime and no headless browser. You scan a QR once; the session persists.

⚠ READ THIS FIRST — it is unofficial:
  - WhatsApp does not offer a bot API for personal accounts, so this drives a
    real account. Meta CAN flag/ban a number for automation. For occasional
    personal use behind an allowlist the risk is low, but it is NOT zero —
    **use a spare / secondary number, not your primary.**
  - It can break when WhatsApp changes their protocol (then: `pip install -U
    neonize` and re-pair). Telegram (hearth.telegram_bridge) is the official,
    zero-ban-risk option — prefer it unless you specifically need WhatsApp.

Setup:
  1. pip install neonize     (already a Hearth dependency)
  2. Create ~/.hearth/whatsapp_bridge.json:
       {"allowed_numbers": ["<your number, digits only, country code, no +>"],
        "allow_self_chat": true,
        "ntfy_topic": ""}
     allowed_numbers = who may talk to it (you). allow_self_chat lets you message
     the linked number's own "you" chat.
  3. Point it at a brain via the same env vars the CLI uses (LOCAL_API_BASE /
     LOCAL_API_KEY / LOCAL_MODEL), then run:
       python -m hearth.whatsapp_bridge
  4. Scan the QR it prints (WhatsApp → Settings → Linked Devices → Link a Device).

Like the Telegram bridge, this is a single-owner relay: the allowlist is the gate
and messages auto-approve tool calls (you're the owner). Keep the allowlist tight.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "whatsapp_bridge.json")
_SESSION_DB = os.path.join(os.path.expanduser("~"), ".hearth", "whatsapp", "session.sqlite3")
_WA_MAX = 4000  # WhatsApp text cap is generous; chunk well under it


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _split(s: str, n: int = _WA_MAX):
    s = s or "(no output)"
    out = []
    while s:
        if len(s) <= n:
            out.append(s); break
        cut = s.rfind("\n", 0, n)
        if cut < n // 2:
            cut = s.rfind(" ", 0, n)
        if cut <= 0:
            cut = n
        out.append(s[:cut]); s = s[cut:].lstrip("\n")
    return out


def _msg_text(message) -> str:
    """Pull plain text out of a neonize MessageEv (conversation or extended)."""
    try:
        m = message.Message
        return (getattr(m, "conversation", "") or
                getattr(getattr(m, "extendedTextMessage", None), "text", "") or "")
    except Exception:
        return ""


def _sender_number(message) -> str:
    """Sender's phone number (JID user part), digits only."""
    try:
        return str(message.Info.MessageSource.Sender.User)
    except Exception:
        return ""


async def _run(prompt: str, history: list) -> str:
    """One agent turn through the shared loop; collect the final reply."""
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

    try:
        await headless.run_once(prompt, emit=emit, history=history,
                                permission_check=lambda _n, _a: "allow",
                                supervised=False)  # phone: destructive guard still fires
    except Exception as e:
        return f"(run failed: {type(e).__name__}: {e})"
    reply = "".join(parts).strip()
    # WhatsApp can't reliably edit a sent message, so no live tool feed — append a
    # one-line "used: ..." footer instead so the user still sees what ran.
    if events:
        from . import bridge_status
        foot = bridge_status.footer(events)
        if foot:
            reply = (reply + "\n\n" + foot).strip()
    return reply


def run() -> None:
    try:
        from neonize.client import NewClient
        from neonize.events import MessageEv, ConnectedEv, QREv, PairStatusEv, LoggedOutEv
    except Exception as e:
        print(f"[whatsapp] neonize not available: {e}\n  pip install neonize")
        return

    cfg = _load_config()
    allowed = {str(x).lstrip("+").strip() for x in (cfg.get("allowed_numbers") or [])}
    allow_self = bool(cfg.get("allow_self_chat"))
    if not allowed and not allow_self:
        print(f"[whatsapp] WARNING: no allowed_numbers in {CONFIG_PATH} — I'll log "
              f"incoming numbers but answer nobody. Add yours to the config.")
    if cfg.get("ntfy_topic"):
        os.environ.setdefault("HEARTH_NTFY_TOPIC", cfg["ntfy_topic"])

    Path(os.path.dirname(_SESSION_DB)).mkdir(parents=True, exist_ok=True)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    histories: dict = {}

    client = NewClient(_SESSION_DB)

    @client.event(QREv)
    def _on_qr(_c, qr: QREv):
        code = getattr(qr, "Codes", None) or getattr(qr, "codes", None) or getattr(qr, "Code", "")
        if isinstance(code, (list, tuple)):
            code = code[0] if code else ""
        print("\n[whatsapp] Scan this in WhatsApp → Settings → Linked Devices → Link a Device:\n")
        try:
            import segno
            segno.make(str(code)).terminal(compact=True)
        except Exception:
            print(str(code))
        print("\n[whatsapp] (QR refreshes periodically; rescan if it expires)\n")

    @client.event(ConnectedEv)
    def _on_connected(_c, _e):
        print("[whatsapp] connected — linked and listening.")

    @client.event(PairStatusEv)
    def _on_pair(_c, e):
        print(f"[whatsapp] paired: {getattr(getattr(e, 'ID', None), 'User', '')}")

    @client.event(LoggedOutEv)
    def _on_logout(_c, _e):
        print("[whatsapp] logged out — delete the session + re-pair to reconnect.")

    @client.event(MessageEv)
    def _on_message(_c, message):
        try:
            src = message.Info.MessageSource
            if getattr(src, "IsGroup", False):
                return  # single-owner relay; ignore groups
            from_me = bool(getattr(src, "IsFromMe", False))
            number = _sender_number(message)
            if from_me and not allow_self:
                return
            if not from_me and number not in allowed:
                print(f"[whatsapp] ignored message from {number} "
                      f"(add it to allowed_numbers to enable).")
                return
            text = _msg_text(message).strip()
            if not text:
                return
            chat = src.Chat
            hist = histories.setdefault(number or "self", [])
            reply = loop.run_until_complete(_run(text, hist))
            hist.append({"role": "user", "content": text})
            hist.append({"role": "assistant", "content": reply})
            del hist[:-16]
            for chunk in _split(reply or "(no output)"):
                client.send_message(chat, chunk)
        except Exception as e:
            print(f"[whatsapp] handler error: {type(e).__name__}: {e}")

    print("[whatsapp] starting — Ctrl-C to stop. First run shows a QR to scan.")
    print("[whatsapp] reminder: use a SPARE number; this is an unofficial, "
          "experimental bridge (ban risk on automation).")
    client.connect()  # blocks; runs neonize's own loop


if __name__ == "__main__":
    run()
