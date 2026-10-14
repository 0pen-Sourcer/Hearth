"""One set of chats for the window and the terminal.

The window keeps each chat as a file in <workspace>/conversations. The
terminal writes its chat there too, as it goes, so a chat started in either
place is listed in both and can be carried on in the other. Each side only
ever adds what is new to a file, so turns taken in the other place stay.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional

from .tools import WORKSPACE, atomic_write_json

CONVOS_DIR = os.path.join(WORKSPACE, "conversations")


def _path(cid: str) -> str:
    import re
    return os.path.join(CONVOS_DIR, re.sub(r"[^A-Za-z0-9._-]+", "_", cid)[:80] + ".json")


def new_id() -> str:
    return "c_t" + secrets.token_hex(7)


def _text(content: Any) -> str:
    if isinstance(content, list):          # text and image parts
        return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def to_chat(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The terminal's messages as the window keeps them: what was said, and
    each tool call of a reply as a tool event on that reply."""
    out: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    calls: Dict[str, Dict[str, Any]] = {}
    at = int(time.time() * 1000)
    for m in messages:
        role = m.get("role")
        if role == "user":
            if pending:
                out.append({"role": "assistant", "content": "", "events": pending})
                pending = []
            text = _text(m.get("content")).strip()
            if text:
                out.append({"role": "user", "content": text, "via": "terminal", "at": at})
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                ev = {"kind": "tool", "name": fn.get("name") or "tool", "args": args, "result": "", "ms": 0, "pos": 0}
                calls[tc.get("id") or ""] = ev
                pending.append(ev)
            text = _text(m.get("content")).strip()
            if text and not m.get("tool_calls"):
                out.append({"role": "assistant", "content": text, "via": "terminal", "at": at,
                            **({"events": pending} if pending else {})})
                pending = []
        elif role == "tool":
            ev = calls.get(m.get("tool_call_id") or "")
            if ev is not None:
                ev["result"] = _text(m.get("content"))[:4000]
    if pending:
        out.append({"role": "assistant", "content": "", "events": pending, "via": "terminal"})
    return out


def from_chat(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A chat file's turns as plain messages for the terminal to go on from."""
    return [{"role": m["role"], "content": _text(m.get("content"))}
            for m in messages if m.get("role") in ("user", "assistant") and _text(m.get("content")).strip()]


def load(cid: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_path(cid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def listing(limit: int = 30) -> List[Dict[str, Any]]:
    """Every chat, newest first: id, title, where it started, how many messages."""
    out = []
    try:
        names = os.listdir(CONVOS_DIR)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json") or fn.startswith("."):
            continue
        try:
            with open(os.path.join(CONVOS_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        out.append({"id": d.get("id") or fn[:-5], "title": d.get("title") or "Untitled",
                    "updated": d.get("updated") or 0, "source": d.get("source") or "window",
                    "count": len(d.get("messages") or [])})
    out.sort(key=lambda c: -int(c["updated"] or 0))
    return out[:limit]


def _title(chat_messages: List[Dict[str, Any]]) -> str:
    first = next((m["content"] for m in chat_messages if m.get("role") == "user" and m.get("content")), "")
    first = " ".join(first.split())
    return (first[:57] + "...") if len(first) > 60 else (first or "Terminal chat")


def append(cid: str, new: List[Dict[str, Any]]) -> int:
    """Add the terminal's newest messages to a chat file, keeping whatever is
    there already. Returns how many turns the file holds now."""
    if not new:
        d = load(cid)
        return len((d or {}).get("messages") or [])
    now = int(time.time() * 1000)
    d = load(cid) or {"id": cid, "title": "", "created": now, "pinned": False, "source": "terminal", "messages": []}
    d["messages"] = list(d.get("messages") or []) + to_chat(new)
    if not d.get("title"):
        d["title"] = _title(d["messages"])
    d["updated"] = now
    d["msg_count"] = len(d["messages"])
    os.makedirs(CONVOS_DIR, exist_ok=True)
    atomic_write_json(_path(cid), d, indent=2, ensure_ascii=False)
    return len(d["messages"])


def title_for(text: str, ask) -> str:
    """A 3-5 word title for a chat from its opening, asked of the brain through
    `ask(prompt) -> str`; the opening's first words when nothing answers."""
    def fallback(t: str) -> str:
        # Only the opener line: `text` may carry the reply too, for the topic.
        t = (t.split("\n", 1)[0]).strip()
        words = t.split()
        return t[:50] if len(words) <= 6 else " ".join(words[:6]) + "…"

    def clean(title: str) -> str:
        title = (title or "").strip().strip('"\'`*').strip()
        for prefix in ("Title:", "title:", "TITLE:", "Here is", "Here's"):
            if title.startswith(prefix):
                title = title[len(prefix):].strip(' :"\'`*').strip()
        title = title.split("\n", 1)[0]
        words = title.split()
        if len(words) > 6:                 # more is the model echoing the prompt
            title = " ".join(words[:6])
        return title[:50].strip(" .,:;")

    prompt = ("Output ONLY a 3-5 word title (no quotes, no preamble) that captures what this "
              "conversation is about. Ignore greetings like 'hi'/'yo'. Nothing else.\n\n"
              f"Conversation:\n{text[:500]}\n\nTitle:")
    try:
        got = clean(ask(prompt))
    except Exception:
        got = ""
    return got or fallback(text)


def retitle(cid: str, title: str) -> None:
    """Give a chat its proper title, unless someone renamed it meanwhile."""
    d = load(cid)
    if not d or not title:
        return
    if d.get("title") and d.get("title") != _title(d.get("messages") or []):
        return
    d["title"] = title
    atomic_write_json(_path(cid), d, indent=2, ensure_ascii=False)


def post(cid: str, title: str, source: str, messages: List[Dict[str, Any]], unread: bool = False) -> None:
    """Add messages the window's way to a chat of its own (a reminder's), making
    the chat when it isn't there yet."""
    now = int(time.time() * 1000)
    d = load(cid) or {"id": cid, "title": title, "created": now, "pinned": False, "source": source, "messages": []}
    d["messages"] = list(d.get("messages") or []) + messages
    d["updated"] = now
    d["msg_count"] = len(d["messages"])
    if unread:
        d["unread"] = True
    os.makedirs(CONVOS_DIR, exist_ok=True)
    atomic_write_json(_path(cid), d, indent=2, ensure_ascii=False)


def newer(cid: str, seen: int) -> List[Dict[str, Any]]:
    """Turns added to a chat file elsewhere since `seen` of them were known."""
    d = load(cid) or {}
    return list((d.get("messages") or [])[seen:])


def keep_terminal_turns(saved: Optional[Dict[str, Any]], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """The window saves a chat whole. Turns the terminal added since the window
    last read the file would be lost; put them back where they were, and mark
    the chat changed so the window reads it again."""
    if not saved:
        return incoming
    have = {(m.get("role"), _text(m.get("content"))) for m in incoming.get("messages") or []}
    missing = [(i, m) for i, m in enumerate(saved.get("messages") or [])
               if m.get("via") == "terminal" and (m.get("role"), _text(m.get("content"))) not in have]
    if not missing:
        return incoming
    merged = list(incoming.get("messages") or [])
    for i, m in missing:
        merged.insert(min(i, len(merged)), m)
    out = dict(incoming, messages=merged, msg_count=len(merged))
    out["updated"] = max(int(incoming.get("updated") or 0), int(saved.get("updated") or 0)) + 1
    return out
