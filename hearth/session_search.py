"""FTS5 cross-session search over the user's chat history.

Builds an SQLite FTS5 virtual table over every message in
~/Jarvis/conversations/*.json so the model can ask "what did we discuss
about the Forge bug last week?" and get a real answer without loading 50
conversation files into context.

Index lives at ~/Jarvis/session_index.db. Auto-rebuilt on tool call if
older than a conversation file's mtime. Pure SQLite stdlib — no extra
deps.

Public API:
    rebuild_index() -> int                # full reindex; returns row count
    search(query, limit=8) -> List[Match] # top FTS5 matches with context
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional

from .tools import WORKSPACE

INDEX_PATH = os.path.join(WORKSPACE, "session_index.db")
CONVOS_DIR = os.path.join(WORKSPACE, "conversations")


@dataclass
class Match:
    conversation_id: str
    title: str
    message_index: int
    role: str
    content: str
    snippet: str  # FTS5 snippet() output with [highlight] markers
    score: float
    updated: float


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(INDEX_PATH)
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
            conversation_id UNINDEXED,
            title           UNINDEXED,
            message_index   UNINDEXED,
            role            UNINDEXED,
            content,
            updated         UNINDEXED,
            tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS meta (
            conversation_id TEXT PRIMARY KEY,
            indexed_mtime   REAL
        );
    """)
    return conn


def _convo_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _needs_reindex() -> bool:
    """Check if any conversation file is newer than its indexed timestamp."""
    if not os.path.isfile(INDEX_PATH):
        return True
    if not os.path.isdir(CONVOS_DIR):
        return False
    try:
        conn = _connect()
        cur = conn.execute("SELECT conversation_id, indexed_mtime FROM meta")
        indexed = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
    except sqlite3.Error:
        return True
    for fn in os.listdir(CONVOS_DIR):
        if not fn.endswith(".json"):
            continue
        cid = fn[:-5]
        path = os.path.join(CONVOS_DIR, fn)
        if cid not in indexed:
            return True
        if _convo_mtime(path) > indexed[cid] + 0.5:
            return True
    return False


def rebuild_index() -> int:
    """Drop + rebuild the entire FTS5 index. Returns rows indexed."""
    if not os.path.isdir(CONVOS_DIR):
        os.makedirs(CONVOS_DIR, exist_ok=True)
    # Wipe and recreate
    if os.path.isfile(INDEX_PATH):
        try:
            os.remove(INDEX_PATH)
        except OSError:
            pass
    conn = _connect()
    total = 0
    for fn in sorted(os.listdir(CONVOS_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(CONVOS_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                convo = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        cid = convo.get("id") or fn[:-5]
        title = convo.get("title") or "Untitled"
        updated = convo.get("updated") or _convo_mtime(path)
        for idx, msg in enumerate(convo.get("messages", [])):
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            conn.execute(
                "INSERT INTO messages(conversation_id, title, message_index, role, content, updated) VALUES (?,?,?,?,?,?)",
                (cid, title, idx, msg.get("role", "?"), content, float(updated)),
            )
            total += 1
        conn.execute(
            "INSERT OR REPLACE INTO meta(conversation_id, indexed_mtime) VALUES (?, ?)",
            (cid, _convo_mtime(path)),
        )
    conn.commit()
    conn.close()
    return total


def search(query: str, limit: int = 8) -> List[Match]:
    """Top FTS5 matches across all past conversations. Auto-rebuilds index
    if any conversation file is newer than its last-indexed timestamp."""
    query = (query or "").strip()
    if not query:
        return []
    if _needs_reindex():
        rebuild_index()
    if not os.path.isfile(INDEX_PATH):
        return []
    # Quote the query for FTS5 to handle phrases naturally — escape any
    # special chars. If the user passed an already-valid FTS query, no-op.
    fts_q = _safe_fts_query(query)
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT conversation_id, title, message_index, role, content,
                   snippet(messages, 4, '[[', ']]', '…', 12),
                   bm25(messages), updated
              FROM messages
             WHERE messages MATCH ?
          ORDER BY bm25(messages)
             LIMIT ?
            """,
            (fts_q, limit),
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return [
        Match(
            conversation_id=r[0], title=r[1], message_index=r[2], role=r[3],
            content=r[4], snippet=r[5], score=r[6], updated=r[7],
        )
        for r in rows
    ]


def _safe_fts_query(q: str) -> str:
    """Wrap user input for FTS5 so simple queries Just Work.
    If the query already contains FTS operators (AND/OR/NEAR), pass through.
    Otherwise quote each token; treat the whole thing as an AND of tokens."""
    q = q.strip()
    if not q:
        return q
    upper_ops = ("AND", "OR", "NOT", "NEAR", "MATCH")
    if any(op in q.upper() for op in upper_ops):
        return q
    tokens = [t.strip('"').strip() for t in q.split() if t.strip()]
    if not tokens:
        return q
    quoted = [f'"{t.replace(chr(34), chr(34)*2)}"' for t in tokens]
    return " ".join(quoted)


def format_matches(matches: List[Match]) -> str:
    """Render matches as readable text for the model's tool result."""
    if not matches:
        return "No past conversations matched. (Index has 0 results — try a different query.)"
    import datetime
    out: List[str] = [f"Top {len(matches)} match(es):"]
    for i, m in enumerate(matches, 1):
        when = datetime.datetime.fromtimestamp(m.updated / (1000 if m.updated > 1e12 else 1))
        when_s = when.strftime("%Y-%m-%d %H:%M")
        out.append(
            f"\n[{i}] {m.title}  ({when_s}, msg #{m.message_index}, {m.role})\n"
            f"    {m.snippet}\n"
            f"    convo_id={m.conversation_id}"
        )
    out.append(
        "\nTo read full context: use the conversation_id with the chat sidebar, "
        "or just summarize what's above for the user."
    )
    return "\n".join(out)
