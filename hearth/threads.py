"""What has been going on lately, across chats.

Facts in memory say what is true about someone. This says what is in motion:
for each recent chat, what it was about, where it stands, and what is still
open ("hasn't bought the phone yet", "payment pending"). The memory pass that
runs after a turn keeps it up to date; a few lines of it go in front of each
message, so "I got something, guess" can be read against what was pending.

No model call of its own and no embeddings: newest first, open ones ahead.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .tools import WORKSPACE, atomic_write_json

PATH = os.path.join(WORKSPACE, "memory", "threads.json")
KEEP = 40              # threads kept on disk
SHOWN = 5              # threads put in front of a message
MAX_AGE_DAYS = 21      # older than this is not "lately"
MAX_CHARS = 900        # the block stays small next to the chat itself

_LOCK = threading.Lock()


def _load() -> Dict[str, Any]:
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("threads"), dict) else {"threads": {}}
    except (OSError, ValueError):
        return {"threads": {}}


def get(chat_id: str) -> Optional[Dict[str, Any]]:
    return _load()["threads"].get(chat_id) if chat_id else None


def _clean(items: Any, limit: int = 4) -> List[str]:
    out = []
    for x in items if isinstance(items, list) else []:
        s = " ".join(str(x).split())[:140]
        if s and s not in out:
            out.append(s)
    return out[:limit]


def note(chat_id: str, thread: Dict[str, Any], now: Optional[float] = None) -> bool:
    """Record where a chat stands. `thread` is what the memory pass returned:
    topic, where (one line), open (list), closed (list of open items now done)."""
    if not chat_id or not isinstance(thread, dict):
        return False
    topic = " ".join(str(thread.get("topic") or "").split())[:80]
    where = " ".join(str(thread.get("where") or "").split())[:200]
    if not topic:
        return False
    with _LOCK:
        d = _load()
        old = d["threads"].get(chat_id) or {}
        closed = {c.lower() for c in _clean(thread.get("closed"), 8)}
        still = [o for o in _clean(old.get("open"), 8) if o.lower() not in closed]
        fresh = _clean(thread.get("open"), 4)
        open_now = fresh + [o for o in still if o.lower() not in {f.lower() for f in fresh}]
        d["threads"][chat_id] = {"topic": topic, "where": where or old.get("where", ""),
                                 "open": open_now[:4], "updated": now if now is not None else time.time()}
        if len(d["threads"]) > KEEP:
            keep = sorted(d["threads"].items(), key=lambda kv: kv[1].get("updated", 0), reverse=True)[:KEEP]
            d["threads"] = dict(keep)
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        atomic_write_json(PATH, d, indent=2, ensure_ascii=False)
    return True


def forget(chat_id: str) -> None:
    with _LOCK:
        d = _load()
        if d["threads"].pop(chat_id, None) is not None:
            atomic_write_json(PATH, d, indent=2, ensure_ascii=False)


def _ago(seconds: float) -> str:
    days = int(seconds // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def recent(exclude: Optional[str] = None, now: Optional[float] = None, limit: int = SHOWN) -> List[Dict[str, Any]]:
    """The threads worth having in mind now: recent ones, those with something
    still open ahead of the rest."""
    now = now if now is not None else time.time()
    rows = []
    for cid, t in _load()["threads"].items():
        if cid == exclude:
            continue
        age = now - float(t.get("updated") or 0)
        if age > MAX_AGE_DAYS * 86400:
            continue
        score = math.exp(-age / (7 * 86400)) + (0.6 if t.get("open") else 0.0)
        rows.append((score, cid, t))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [dict(t, chat=cid, ago=_ago(now - float(t.get("updated") or 0))) for _, cid, t in rows[:limit]]


def block(exclude: Optional[str] = None, now: Optional[float] = None) -> str:
    """A few lines for the model, in front of the message. Empty when nothing
    has been going on."""
    rows = recent(exclude, now)
    if not rows:
        return ""
    lines = ["What has been going on lately, from their other chats (bring it up only when it fits):"]
    for t in rows:
        line = f"- {t['topic']} ({t['ago']})"
        if t.get("where"):
            line += f": {t['where']}"
        if t.get("open"):
            line += " Still open: " + "; ".join(t["open"]) + "."
        if sum(len(x) + 1 for x in lines) + len(line) > MAX_CHARS:
            break
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""
