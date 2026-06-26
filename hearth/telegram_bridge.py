"""Text Hearth from your phone via a Telegram bot — bot token only, NO OAuth.

Setup (2 minutes):
  1. In Telegram, message @BotFather -> /newbot -> copy the bot token.
  2. Message your new bot once (anything), then open
     https://api.telegram.org/bot<token>/getUpdates to find your numeric chat id
     (or just run this bridge and it logs the chat id of anyone who messages).
  3. Create ~/.hearth/phone_bridge.json:
       {"bot_token": "123456:ABC...", "allowed_chat_ids": [<your id>],
        "ntfy_topic": "hearth-<random>"}
  4. Run:  python -m hearth.telegram_bridge
     (point it at a brain first via the same env vars the CLI uses:
      LOCAL_API_BASE / LOCAL_API_KEY / LOCAL_MODEL — defaults to LM Studio.)

It long-polls getUpdates (no public webhook needed), runs each message through
the same agent loop as the CLI/GUI (run_once), replies with the result, and
sends back any file the agent produced. Only the chat ids in allowed_chat_ids
are answered — everyone else is ignored.

This is a single-owner phone bridge, NOT a multi-channel gateway. The chat-id
allowlist is the only gate, and messages auto-approve tool calls (you're the
owner), so keep allowed_chat_ids tight and the token out of any repo.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
import urllib.request

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "phone_bridge.json")
_TG_MAX = 4096


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _post_json(url: str, payload: dict, timeout: float = 40.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _split_4096(s: str):
    """Split into <=4096-char chunks, preferring a newline (past halfway) then a
    space, then a hard cut. UTF-8 safe (slicing str, not bytes)."""
    s = s or "(no output)"
    out = []
    while s:
        if len(s) <= _TG_MAX:
            out.append(s)
            break
        win = s[:_TG_MAX]
        cut = win.rfind("\n")
        if cut < _TG_MAX // 2:
            sp = win.rfind(" ")
            cut = sp if sp > 0 else _TG_MAX
        out.append(s[:cut])
        s = s[cut:].lstrip("\n")
    return out


def _send_message(token: str, chat_id: int, text: str, reply_to=None) -> None:
    for i, chunk in enumerate(_split_4096(text)):
        body = {"chat_id": chat_id, "text": chunk}
        if i == 0 and reply_to:
            body["reply_to_message_id"] = reply_to
        try:
            _post_json(_api(token, "sendMessage"), body, timeout=20)
        except Exception:
            pass
        time.sleep(0.1)  # dodge Telegram's rate limit between chunks


def _send_one(token: str, chat_id: int, text: str, reply_to=None):
    """Send a single (un-split) message, returning its message_id or None."""
    body = {"chat_id": chat_id, "text": text[:_TG_MAX]}
    if reply_to:
        body["reply_to_message_id"] = reply_to
    try:
        r = _post_json(_api(token, "sendMessage"), body, timeout=15)
        if r.get("ok"):
            return r["result"]["message_id"]
    except Exception:
        pass
    return None


def _edit_message(token: str, chat_id: int, message_id: int, text: str) -> bool:
    try:
        r = _post_json(_api(token, "editMessageText"),
                       {"chat_id": chat_id, "message_id": message_id,
                        "text": text[:_TG_MAX]}, timeout=15)
        return bool(r.get("ok"))
    except Exception:
        return False


def _send_document(token: str, chat_id: int, path: str) -> None:
    """Multipart sendDocument via stdlib (no requests dependency)."""
    try:
        with open(path, "rb") as fh:
            payload = fh.read()
    except OSError:
        return
    boundary = "----HearthFormBoundary" + os.urandom(8).hex()
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    post = f"\r\n--{boundary}--\r\n".encode("utf-8")
    data = pre + payload + post
    req = urllib.request.Request(
        _api(token, "sendDocument"), data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    try:
        urllib.request.urlopen(req, timeout=120)
    except Exception:
        pass


# Detect absolute paths in the reply text so we can send produced files back.
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s'\"<>|]+\.[A-Za-z0-9]{1,5}")


def _files_in(text: str):
    seen, out = set(), []
    for m in _PATH_RE.findall(text or ""):
        p = m.rstrip(".,);")
        if p not in seen and os.path.isfile(p) and os.path.getsize(p) < 45_000_000:
            seen.add(p)
            out.append(p)
    return out[:4]


# System message injected every bridge turn (not persisted, so the history cap
# can't drop it): teaches the model it CAN send files here by naming their path.
_CHANNEL_PRIMER = {
    "role": "system",
    "content": (
        "You're reachable over Telegram right now — a chat bridge, not the "
        "desktop GUI. To send the user a FILE or IMAGE (a screenshot, a PDF, a "
        "chart you made), just include its absolute local path in your reply; "
        "the bridge auto-sends any local path you mention as a document. So you "
        "CAN send files here — never tell the user you can't. Keep replies "
        "chat-length and don't reference GUI-only buttons."
    ),
}


async def _run(prompt: str, history: list, on_tool=None) -> str:
    """Run one turn through the shared agent loop; collect the final reply."""
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

    def _allow(_name, _args):
        return "allow"  # owner-gated by chat-id allowlist; auto-approve tools

    try:
        await headless.run_once(prompt, emit=emit,
                                history=[_CHANNEL_PRIMER] + (history or []),
                                permission_check=_allow,
                                supervised=False)  # phone: destructive guard still fires
    except Exception as e:
        return f"(run failed: {type(e).__name__}: {e})"
    return "".join(parts).strip()


def run() -> None:
    cfg = _load_config()
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        print(f"[telegram] no bot_token in {CONFIG_PATH} — see the module docstring.")
        return
    allowed = set(int(x) for x in (cfg.get("allowed_chat_ids") or []))
    if not allowed:
        print("[telegram] WARNING: allowed_chat_ids is empty — I'll log incoming "
              "chat ids but answer nobody. Add your id to the config.")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    histories: dict = {}
    offset = 0
    # Drop any stale webhook so getUpdates doesn't 409.
    try:
        _post_json(_api(token, "deleteWebhook"), {"drop_pending_updates": False}, timeout=10)
    except Exception:
        pass
    print("[telegram] bridge up — long-polling getUpdates. Ctrl-C to stop.")
    while True:
        try:
            r = _post_json(_api(token, "getUpdates"),
                           {"offset": offset, "timeout": 30,
                            "allowed_updates": ["message"]}, timeout=40)
        except Exception:
            time.sleep(5)
            continue
        if not r.get("ok"):
            # 409 = another poller or a webhook owns the token; back off.
            time.sleep(5)
            continue
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1  # ACK; Telegram drops everything below
            msg = upd.get("message") or {}
            text = msg.get("text")
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None or not text:
                continue
            if chat_id not in allowed:
                print(f"[telegram] ignored message from chat id {chat_id} "
                      f"(add it to allowed_chat_ids to enable).")
                continue
            try:
                _post_json(_api(token, "sendChatAction"),
                           {"chat_id": chat_id, "action": "typing"}, timeout=10)
            except Exception:
                pass
            hist = histories.setdefault(chat_id, [])
            # Live tool feed: one status message we edit as tools fire. Throttled
            # to ~1s/edit to stay under Telegram's edit rate limit. Created lazily
            # on the first tool call so a pure-chat reply shows no status noise.
            from . import bridge_status
            status = {"events": [], "msg_id": None, "last": 0.0}

            def on_tool(events):
                status["events"] = events
                now = time.time()
                if now - status["last"] < 1.0:
                    return
                status["last"] = now
                body = bridge_status.format_status(events, working=True)
                if status["msg_id"] is None:
                    status["msg_id"] = _send_one(token, chat_id, body,
                                                 reply_to=msg.get("message_id"))
                else:
                    _edit_message(token, chat_id, status["msg_id"], body)

            reply = loop.run_until_complete(_run(text, hist, on_tool=on_tool))
            # keep a short rolling history for multi-turn context
            hist.append({"role": "user", "content": text})
            hist.append({"role": "assistant", "content": reply})
            del hist[:-16]  # cap to the last 8 exchanges
            final = reply or "(no output)"
            # Finalize the tool status into a "Tools used" list that STAYS, then
            # send the answer as its own message below — "tools used X" then the
            # reply, never one edited into the other.
            if status["msg_id"] is not None and status["events"]:
                _edit_message(token, chat_id, status["msg_id"],
                              bridge_status.format_status(status["events"], working=False))
            _send_message(token, chat_id, final, reply_to=msg.get("message_id"))
            for f in _files_in(reply):
                _send_document(token, chat_id, f)


if __name__ == "__main__":
    run()
