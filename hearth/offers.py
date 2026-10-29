"""What an empty new chat offers, on the PC and the phone: at most three rows.

The chats still going on come first (each opens its chat), then the reminder
ideas Inglen already wrote from memory (each puts its words in the box). Read
from what is kept; nothing here asks a model.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

LIMIT = 3


def sources() -> Dict[str, Any]:
    """{threads, ideas} as kept: the open memory cards with a chat, and the cached reminder ideas."""
    out: Dict[str, Any] = {"threads": [], "ideas": []}
    try:
        from . import memory_cards
        out["threads"] = [{"topic": c.get("goal") or "", "chat": c.get("chat") or "", "ago": c.get("ago") or "",
                           "open": (c.get("left") or [])[:2]} for c in memory_cards.open_cards(limit=3) if c.get("chat")]
    except Exception:
        pass
    try:
        from .tools import WORKSPACE
        with open(os.path.join(WORKSPACE, "logs", "reminder_ideas.json"), encoding="utf-8") as f:
            cached = json.load(f)
        out["ideas"] = [{"title": i.get("title") or "", "ask": i.get("ask") or ""}
                        for i in (cached.get("ideas") or [])[:3] if i.get("ask")]
    except Exception:
        pass
    return out


def rows(src: Dict[str, Any] = None, limit: int = LIMIT) -> List[Dict[str, str]]:
    """At most `limit` rows, one line each: chats to carry on first (two at most when
    there are ideas too), then ideas."""
    src = src if src is not None else sources()
    threads = [t for t in src.get("threads") or [] if t.get("chat") and t.get("topic")]
    ideas = [i for i in src.get("ideas") or [] if i.get("ask")]
    take = min(len(threads), limit if not ideas else max(1, limit - 1))
    out = [{"kind": "chat", "text": t["topic"], "chat": t["chat"]} for t in threads[:take]]
    out += [{"kind": "idea", "text": i.get("title") or i["ask"], "ask": i["ask"]} for i in ideas[:limit - len(out)]]
    return out
