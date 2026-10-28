"""Where each chat stands: one small card per chat, kept in memory's index.

A card says what the chat is for and where it got to: the goal, whether it is
open, done or dropped, the values settled so far (a budget, a shortlist), what
is left, and the names in it. The memory pass writes it when a chat goes idle,
replacing the old one rather than adding to it. A chat with no goal (small
talk, a one-off question) has no card.

The newest open cards go in front of the first message of a new chat, so "I got
something, take a guess" can be read against what was pending elsewhere.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from . import session_search as _idx

STATUSES = ("open", "done", "dropped")
SHOWN = 4                  # open cards put in front of a new chat's first message
MAX_AGE_DAYS = 21          # an open card untouched this long is dropped
BLOCK_CHARS = 1200         # about 300 tokens

# Words that only describe a chat with nothing going on. A card made of them and
# nothing else (no values, nothing left) is not kept.
_EMPTY = re.compile(r"\b(banter|small ?talk|chit[- ]?chat|greetings?|nothing (substantive|much|ongoing|specific|"
                    r"pending|in particular)|casual (chat|conversation|talk)|just chatting|no (goal|task|topic)|"
                    r"general chat|saying hi|testing (the )?(chat|bot|assistant))\b", re.I)


def _one(s: Any, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def _items(v: Any, limit: int, n: int = 120) -> List[str]:
    out: List[str] = []
    for x in v if isinstance(v, list) else ([v] if isinstance(v, str) and v.strip() else []):
        s = _one(x, n)
        if s and s.lower() not in {o.lower() for o in out}:
            out.append(s)
    return out[:limit]


def not_theirs(chat: str) -> bool:
    # A reminder's own chat is Hearth saying the reminder, not something going on.
    return str(chat or "").startswith("rem_")


def validate(raw: Any) -> Optional[Dict[str, Any]]:
    """A card from what the memory pass returned, or None when there is no goal
    to keep. Never half a card: what does not fit the shape is dropped."""
    if not isinstance(raw, dict):
        return None
    goal = _one(raw.get("goal") or raw.get("topic"), 120)
    if not goal:
        return None
    status = str(raw.get("status") or "open").strip().lower()
    if status not in STATUSES:
        status = "open"
    slots: Dict[str, str] = {}
    got = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
    for k, v in list(got.items())[:8]:
        key = _one(k, 30)
        val = ", ".join(_items(v, 6, 60)) if isinstance(v, list) else _one(v, 120)
        if key and val and val.lower() not in ("null", "none", "n/a", "unknown"):
            slots[key] = val
    left = _items(raw.get("left") if "left" in raw else raw.get("open"), 5)
    card = {"goal": goal, "status": status, "where": _one(raw.get("where"), 200), "slots": slots,
            "left": left, "names": [n.lower() for n in _items(raw.get("names"), 10, 60)]}
    if not slots and not left and _EMPTY.search(goal + " " + card["where"]):
        return None
    return card


def _title(chat: str) -> str:
    try:
        from . import chat_sync
        return str((chat_sync.load(chat) or {}).get("title") or "")
    except Exception:
        return ""


def get(chat: str) -> Optional[Dict[str, Any]]:
    if not chat:
        return None
    conn = _idx._connect()
    try:
        row = conn.execute("SELECT data FROM cards WHERE chat = ?", (chat,)).fetchone()
    finally:
        conn.close()
    try:
        return json.loads(row[0]) if row else None
    except ValueError:
        return None


def put(chat: str, raw: Any, title: str = "", now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Write a chat's card over the one it had. Returns the card kept, or None
    when there was nothing worth a card (the old one, if any, stays)."""
    if not chat or not_theirs(chat):
        return None
    card = validate(raw)
    if card is None:
        return None
    now = now if now is not None else time.time()
    old = get(chat) or {}
    card.update(chat=chat, title=title or old.get("title") or _title(chat),
                created=old.get("created") or now, updated=now)
    if not card["names"]:
        card["names"] = _idx.names(". ".join([card["goal"], card["where"], *card["slots"].values(), *card["left"]]))[:10]
    with _idx._LOCK:
        conn = _idx._connect()
        try:
            conn.execute("INSERT OR REPLACE INTO cards(chat, data, status, updated) VALUES (?,?,?,?)",
                         (chat, json.dumps(card, ensure_ascii=False), card["status"], now))
            conn.commit()
        finally:
            conn.close()
    return card


def set_status(chat: str, status: str) -> bool:
    """Mark a chat's card done, dropped or open again."""
    card = get(chat)
    if not card or status not in STATUSES:
        return False
    card["status"] = status
    card["updated"] = time.time()
    with _idx._LOCK:
        conn = _idx._connect()
        try:
            conn.execute("UPDATE cards SET data = ?, status = ?, updated = ? WHERE chat = ?",
                         (json.dumps(card, ensure_ascii=False), status, card["updated"], chat))
            conn.commit()
        finally:
            conn.close()
    return True


def forget(chat: str) -> bool:
    with _idx._LOCK:
        conn = _idx._connect()
        try:
            n = conn.execute("DELETE FROM cards WHERE chat = ?", (chat,)).rowcount
            conn.commit()
        finally:
            conn.close()
    return bool(n)


def ago(seconds: float) -> str:
    days = int(max(0.0, seconds) // 86400)
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


def everything(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Every card, newest first, for the Memory tab."""
    migrate()
    now = now if now is not None else time.time()
    conn = _idx._connect()
    try:
        rows = conn.execute("SELECT data FROM cards ORDER BY updated DESC").fetchall()
    finally:
        conn.close()
    out = []
    for (data,) in rows:
        try:
            c = json.loads(data)
        except ValueError:
            continue
        if not_theirs(c.get("chat", "")):
            continue
        c["ago"] = ago(now - float(c.get("updated") or 0))
        out.append(c)
    return out


def close_stale(now: Optional[float] = None) -> int:
    """Open cards nobody came back to in MAX_AGE_DAYS are dropped."""
    now = now if now is not None else time.time()
    n = 0
    for c in everything(now):
        if c.get("status") == "open" and now - float(c.get("updated") or 0) > MAX_AGE_DAYS * 86400:
            n += set_status(c["chat"], "dropped")
    return n


def open_cards(exclude: Optional[str] = None, now: Optional[float] = None, limit: int = SHOWN) -> List[Dict[str, Any]]:
    """The open cards worth having in mind now, newest first."""
    now = now if now is not None else time.time()
    rows = []
    for c in everything(now):
        age = now - float(c.get("updated") or 0)
        if c.get("chat") == exclude or c.get("status") != "open" or age > MAX_AGE_DAYS * 86400:
            continue
        rows.append((math.exp(-age / (7 * 86400)) + (0.3 if c.get("left") else 0.0), c))
    rows.sort(key=lambda r: -r[0])
    return [c for _s, c in rows[:limit]]


def card_text(c: Dict[str, Any]) -> str:
    """One line for a card: goal, values, where it stands, what is left."""
    bits = [c.get("goal") or ""]
    if c.get("status") and c.get("status") != "open":
        bits[0] += f" ({c['status']})"
    if c.get("slots"):
        bits.append("; ".join(f"{k}: {v}" for k, v in c["slots"].items()))
    if c.get("where"):
        bits.append(c["where"])
    if c.get("left"):
        bits.append("Left: " + "; ".join(c["left"]))
    return ". ".join(b.rstrip(". ") for b in bits if b) + "."


def block(exclude: Optional[str] = None, now: Optional[float] = None) -> Tuple[str, List[Dict[str, Any]]]:
    """The lines a new chat's first message gets, and the cards in them."""
    rows = open_cards(exclude, now)
    if not rows:
        return "", []
    lines = ["Still going on in their other chats. A message that points at something without naming it "
             "(\"guess what\", \"it came\", \"I did it\") most likely means one of these, so take a guess from "
             "them; otherwise bring them up only when it fits:"]
    used = len(lines[0])
    kept = []
    for c in rows:
        line = f"- {card_text(c)} ({c.get('ago')}, chat \"{c.get('title') or c.get('chat')}\")"
        if used + len(line) > BLOCK_CHARS:
            break
        lines.append(line)
        used += len(line)
        kept.append(c)
    return ("\n".join(lines), kept) if kept else ("", [])


def as_item(c: Dict[str, Any]) -> Dict[str, Any]:
    """A card as the reply's "what I had in mind" list shows it. topic, where,
    open and ago are the fields every surface already reads."""
    return {"kind": "card", "id": c.get("chat", ""), "chat": c.get("chat", ""), "chat_title": c.get("title") or "",
            "topic": c.get("goal", ""), "where": card_text(c), "open": list(c.get("left") or []),
            "ago": c.get("ago", ""), "status": c.get("status", "open")}


# ---------------------------------------------------------------- what went into a reply
MEMORY_TOOLS = ("search_chats", "memory_recall", "read_chat")


def _chat_title(chat: str) -> str:
    return _title(chat) if chat else ""


def fact_item(title: str) -> Optional[Dict[str, Any]]:
    """A saved fact as the reply's list shows it, with the chat it came from.
    Going into a reply counts as using it."""
    from . import memory
    path = os.path.join(memory.MEM_DIR, memory._slug(title) + ".md")
    if not os.path.isfile(path):
        return None
    try:
        fm, body = memory._read_body(path)
    except (OSError, UnicodeDecodeError):
        return None
    memory._bump_recall(path)
    src = fm.get("source_chat") or ""
    return {"kind": "fact", "id": fm.get("name") or title, "topic": fm.get("name") or title,
            "where": fm.get("description") or " ".join(body.split())[:160], "open": [],
            "ago": "", "chat": src, "chat_title": _chat_title(src)}


def tool_items(name: str, args: Dict[str, Any], result: str) -> List[Dict[str, Any]]:
    """What a memory tool call put in front of the model, as list items."""
    result = result or ""
    if name == "memory_recall":
        out = []
        for t in re.findall(r"^## (.+?)(?: \(cold\))?  \(", result, re.M)[:6]:
            it = fact_item(t.strip())
            if it:
                out.append(it)
        return out
    if name == "search_chats":
        q = str(args.get("query") or "").strip()
        when = " ".join(str(args.get(k) or "") for k in ("after", "before") if args.get(k)).strip()
        chats = re.findall(r"convo_id=(\S+)", result)
        titles = re.findall(r"^\[\d+\] (?:Going on, in chat \"(.+?)\"|(.+?)  \()", result, re.M)
        titles = [a or b for a, b in titles]
        n = len(chats)
        where = (f"{n} found" + (": " + ", ".join(dict.fromkeys(titles[:3])) if titles else "")) if n else "nothing found"
        return [{"kind": "search", "id": q or when, "topic": f"Searched \"{q or when}\"", "where": where, "open": [],
                 "ago": "", "chat": chats[0] if chats else "", "chat_title": titles[0] if titles else ""}]
    if name == "read_chat":
        m = re.match(r"Chat: (.+?) \((.*?)id=([^)\s]+)\)", result)
        if not m:
            return []
        return [{"kind": "chat", "id": m.group(3), "topic": f"Read \"{m.group(1)}\"", "where": "", "open": [],
                 "ago": "", "chat": m.group(3), "chat_title": m.group(1)}]
    return []


class Trail:
    """Wraps a turn's emit: each memory tool call adds what it brought to the
    turn's list, and the whole list goes out again as context_used, so every
    surface (which keeps the latest) has it all."""

    def __init__(self, emit, items: Optional[List[Dict[str, Any]]] = None):
        self.emit, self.items = emit, list(items or [])
        self.calls: Dict[str, List[Dict[str, Any]]] = {}

    def __call__(self, kind: str, **f) -> Any:
        name = f.get("name") or ""
        if name in MEMORY_TOOLS:
            if kind == "tool_call":
                self.calls.setdefault(name, []).append(dict(f.get("args") or {}))
            elif kind == "tool_result":
                args = (self.calls.get(name) or [{}]).pop(0) if self.calls.get(name) else {}
                got = []
                try:
                    got = tool_items(name, args, str(f.get("content") or ""))
                except Exception:
                    got = []
                have = {(i.get("kind"), i.get("id")) for i in self.items}
                fresh = [i for i in got if (i.get("kind"), i.get("id")) not in have]
                if fresh:
                    self.items.extend(fresh)
                    try:
                        self.emit("context_used", threads=list(self.items))
                    except Exception:
                        pass
        return self.emit(kind, **f)


def act(kind: str, item_id: str, action: str) -> Dict[str, Any]:
    """"wrong / forget" or "done with this" on an item of the list."""
    from . import memory
    if kind == "card":
        if action == "forget":
            return {"ok": forget(item_id)}
        if action == "done":
            return {"ok": set_status(item_id, "done")}
        if action == "reopen":
            return {"ok": set_status(item_id, "open")}
    if kind == "fact":
        if action == "forget":
            return {"ok": not memory.forget(item_id).startswith("no memory")}
        if action == "done":
            return {"ok": bool(memory.invalidate(item_id, "done with it"))}
    return {"ok": False, "error": f"can't {action} a {kind}"}


# ---------------------------------------------------------------- the old list
def _threads_path() -> str:
    from .tools import WORKSPACE
    return os.path.join(WORKSPACE, "memory", "threads.json")


def from_thread(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An entry of the old "lately" list as a card, or None for one with no goal."""
    if not isinstance(t, dict):
        return None
    left = _items(t.get("open"), 5)
    return validate({"goal": t.get("topic"), "where": t.get("where"), "left": left,
                     "status": "open" if left else "done"})


def migrate() -> int:
    """Turn the old threads.json into cards, once. Entries with no goal are
    left out. The file is kept beside, renamed, and never read again."""
    path = _threads_path()
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    n = 0
    for chat, t in ((data or {}).get("threads") or {}).items():
        if not_theirs(chat) or get(chat):
            continue
        card = from_thread(t)
        if card is None:
            continue
        put(chat, card, now=float(t.get("updated") or time.time()))
        n += 1
    dest = path + ".old"
    if os.path.exists(dest):
        dest = path + f".old-{int(time.time())}"
    try:
        shutil.move(path, dest)
    except OSError:
        pass
    return n
