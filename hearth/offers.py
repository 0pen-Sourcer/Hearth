"""What an empty new chat offers, on the PC and the phone: at most three ideas.

Each is a fresh message to send: something to ask, or a reminder to set (with the
bell). They come from what the person has been doing across their chats (memory's
note on each chat) and what memory keeps about them; each knows the chat it came
from, which a long press or right click can open. Nothing here opens an old chat
by itself, and nothing here asks a model: it reads what web.reminder_ideas kept.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

LIMIT = 3


def sources() -> Dict[str, Any]:
    """{ideas} as kept, without the ones called not useful. "threads" stays, empty, for older phones."""
    out: Dict[str, Any] = {"threads": [], "ideas": []}
    try:
        from .tools import WORKSPACE
        with open(os.path.join(WORKSPACE, "logs", "reminder_ideas.json"), encoding="utf-8") as f:
            cached = json.load(f)
        nope = {str(x).strip().lower() for x in cached.get("not_useful") or []}
        out["ideas"] = [{"title": i.get("title") or "", "ask": i.get("ask") or "", "kind": i.get("kind") or "reminder",
                         **({"chat": i["chat"], "chat_title": i.get("chat_title") or ""} if i.get("chat") else {})}
                        for i in (cached.get("ideas") or []) if i.get("ask") and i["ask"].strip().lower() not in nope][:LIMIT]
    except Exception:
        pass
    return out


def rows(src: Dict[str, Any] = None, limit: int = LIMIT) -> List[Dict[str, str]]:
    """At most `limit` rows, one line each, every one an idea that fills the message box."""
    src = src if src is not None else sources()
    return [{"kind": i.get("kind") or "reminder", "text": i.get("title") or i["ask"], "ask": i["ask"],
             "chat": i.get("chat") or "", "chat_title": i.get("chat_title") or ""}
            for i in (src.get("ideas") or []) if i.get("ask")][:limit]
