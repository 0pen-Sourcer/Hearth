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
    snippet: str  # FTS5 snippet() context window (clean text, '…' for elision)
    score: float
    updated: float


# Marks a row that came from the recency fallback rather than a real keyword
# hit. Both used to render identically, so the model could not tell "these
# messages answer your question" from "search matched nothing, here is whatever
# was most recent" — and it would answer confidently off unrelated context.
RECENCY_SCORE = 999.0


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
    # Also re-index when the CLI flat history mtime moved past last index.
    cli_path = os.path.join(os.path.dirname(CONVOS_DIR), "logs", "jarvis_history.json")
    if os.path.isfile(cli_path):
        last = indexed.get("cli-history", 0.0)
        if _convo_mtime(cli_path) > last + 0.5:
            return True
    # And when the never-pruned CLI transcript grew.
    tx_path = os.path.join(os.path.dirname(CONVOS_DIR), "logs", "cli_transcript.jsonl")
    if os.path.isfile(tx_path):
        if _convo_mtime(tx_path) > indexed.get("cli-transcript", 0.0) + 0.5:
            return True
    return False


def rebuild_index() -> int:
    """Drop + rebuild the entire FTS5 index. Returns rows indexed.

    Sources: GUI conversations at ~/Jarvis/conversations/*.json AND the
    CLI's flat history at ~/Jarvis/logs/jarvis_history.json. Without the
    CLI source, search_chats run from the GUI couldn't see rules/quirks
    the user set in CLI sessions (and vice versa).
    """
    if not os.path.isdir(CONVOS_DIR):
        os.makedirs(CONVOS_DIR, exist_ok=True)
    if os.path.isfile(INDEX_PATH):
        try:
            os.remove(INDEX_PATH)
        except OSError:
            pass
    conn = _connect()
    total = 0

    # 1) GUI conversations — one file per chat thread.
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

    # 2) CLI flat history — one big array of messages. Index it as a
    # synthetic "cli-history" conversation so search_chats returns CLI
    # turns alongside GUI ones.
    cli_path = os.path.join(os.path.dirname(CONVOS_DIR), "logs", "jarvis_history.json")
    if os.path.isfile(cli_path):
        try:
            with open(cli_path, "r", encoding="utf-8") as f:
                cli_msgs = json.load(f)
        except (OSError, json.JSONDecodeError):
            cli_msgs = []
        if isinstance(cli_msgs, list):
            cid = "cli-history"
            title = "CLI session history"
            mtime = _convo_mtime(cli_path)
            for idx, msg in enumerate(cli_msgs):
                if not isinstance(msg, dict):
                    continue
                raw = msg.get("content")
                # CLI history mixes plain strings with multimodal/list
                # payloads (image-attached turns, tool_calls). Coerce
                # lists to a flat string of their text parts; ignore
                # anything that doesn't yield text.
                if isinstance(raw, list):
                    parts = []
                    for p in raw:
                        if isinstance(p, dict) and isinstance(p.get("text"), str):
                            parts.append(p["text"])
                    raw = "\n".join(parts)
                content = (raw or "").strip() if isinstance(raw, str) else ""
                if not content:
                    continue
                conn.execute(
                    "INSERT INTO messages(conversation_id, title, message_index, role, content, updated) VALUES (?,?,?,?,?,?)",
                    (cid, title, idx, msg.get("role", "?"), content, float(mtime)),
                )
                total += 1
            conn.execute(
                "INSERT OR REPLACE INTO meta(conversation_id, indexed_mtime) VALUES (?, ?)",
                (cid, mtime),
            )

    # 3) Append-only CLI transcript (never pruned). jarvis_history.json above is
    # the working context and gets trimmed to fit the model window; this JSONL
    # keeps the FULL back-and-forth so old turns stay searchable.
    tx_path = os.path.join(os.path.dirname(CONVOS_DIR), "logs", "cli_transcript.jsonl")
    if os.path.isfile(tx_path):
        cid = "cli-transcript"
        title = "CLI transcript (full)"
        mtime = _convo_mtime(tx_path)
        try:
            with open(tx_path, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = (rec.get("content") or "").strip()
                    if not content:
                        continue
                    conn.execute(
                        "INSERT INTO messages(conversation_id, title, message_index, role, content, updated) VALUES (?,?,?,?,?,?)",
                        (cid, title, idx, rec.get("role", "?"), content, float(mtime)),
                    )
                    total += 1
            conn.execute(
                "INSERT OR REPLACE INTO meta(conversation_id, indexed_mtime) VALUES (?, ?)",
                (cid, mtime),
            )
        except OSError:
            pass

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
                   snippet(messages, 4, '', '', '…', 28),
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
    matches = [
        Match(
            conversation_id=r[0], title=r[1], message_index=r[2], role=r[3],
            content=r[4], snippet=r[5], score=r[6], updated=r[7],
        )
        for r in rows
    ]
    if matches:
        return matches
    # Recency fallback: strict FTS5 found nothing — common for vague, natural
    # questions like "what were we talking about just now" (no literal keyword
    # overlap). Return the most-recent real messages so the model gets ACTUAL
    # recent context instead of zero results, which previously made it fabricate
    # an answer from stale system-prompt memory (the "I don't have it so I'll
    # guess" failure). Recency is exactly what "just now / last / recent" wants.
    return _recent(limit)


def _recent(limit: int = 8) -> List[Match]:
    """Most-recent user/assistant messages across all conversations, newest
    first. The fallback when keyword search misses — gives 'what did we just
    talk about' a real answer instead of nothing."""
    if not os.path.isfile(INDEX_PATH):
        return []
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT conversation_id, title, message_index, role, content,
                   substr(content, 1, 200), ?, updated
              FROM messages
             WHERE role IN ('user', 'assistant') AND length(trim(content)) > 0
          ORDER BY updated DESC, message_index DESC
             LIMIT ?
            """,
            (RECENCY_SCORE, limit),
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
        # Include the actual conversation count + index state so the model
        # can distinguish "no chats exist yet" from "you matched zero of
        # 200 chats — try a broader query". Previously a 0-results return
        # looked the same as an empty store, and the model would tell the
        # user "I have no history" when really they have 50 chats.
        try:
            n_files = 0
            if os.path.isdir(CONVOS_DIR):
                n_files = sum(1 for f in os.listdir(CONVOS_DIR) if f.endswith(".json"))
            n_indexed = 0
            if os.path.isfile(INDEX_PATH):
                conn = _connect()
                try:
                    n_indexed = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                finally:
                    conn.close()
        except Exception:
            n_files = n_indexed = -1
        if n_files == 0:
            return ("No past conversations exist yet — this is the first "
                    "chat. Don't say 'I have no history'; say 'this is "
                    "our first session, what's up?'.")
        return (f"No past conversations matched the query. "
                f"({n_files} conversation file(s) in store, {n_indexed} "
                f"indexed messages — try broader terms, fewer terms, or "
                f"a different phrasing.)")
    import datetime
    _fallback = all(getattr(m, "score", 0) == RECENCY_SCORE for m in matches)
    if _fallback:
        out: List[str] = [
            "No message actually matched that query. Keyword search found "
            "nothing, so these are simply the most RECENT messages, which may "
            "be unrelated to what was asked. Do not present them as the answer "
            "— if they don't cover it, say the search found nothing and ask "
            "the user to name a specific term, file or topic."
        ]
    else:
        out = [f"Top {len(matches)} match(es):"]
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
