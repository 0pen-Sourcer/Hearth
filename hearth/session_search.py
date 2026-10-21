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
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional

from .tools import WORKSPACE, is_write_temp

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
        # A staged write carries the .json extension and no chat id that was
        # ever indexed, so it read as an unindexed conversation and this said
        # the index was stale on every call. One left behind by a killed save
        # meant the whole index was rebuilt for every search.
        if not fn.endswith(".json") or is_write_temp(fn):
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
        if not fn.endswith(".json") or is_write_temp(fn):
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


# Phrases that mean "I'm asking about WHEN, or about recent talk" — for these,
# recency has to beat text score. Otherwise BM25 ranks by keyword density, and the
# generic words in a temporal question (were/doing/yesterday) match nearly
# everywhere, so a pure relevance sort returns the OLDEST dense match instead of
# the most recent one.
_TEMPORAL_RE = re.compile(
    r"\b("
    r"yesterday|today|tonight|this morning|this afternoon|this evening|"
    r"last night|earlier|recently|just now|moments? ago|a moment ago|"
    r"last time|previously|the other day|(a\s+)?few\s+(days?|hours?)\s+ago|"
    r"\d+\s+(days?|hours?|weeks?)\s+ago|this week|last week|a while ago|"
    r"what were we|what did we|what was i|what have we|"
    r"continue where|pick up where|where we left"
    r")\b",
    re.IGNORECASE,
)


def _is_temporal_query(q: str) -> bool:
    return bool(_TEMPORAL_RE.search(q or ""))


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
    fts_q = _safe_fts_query(query)
    # Temporal/recency questions rank by RECENCY first (newest match wins, bm25
    # only breaks ties); everything else ranks by relevance with recency breaking
    # ties. Both ORDER BY strings are hardcoded constants — no user input reaches
    # the SQL, so the f-string is injection-safe.
    _order = ("updated DESC, bm25(messages)" if _is_temporal_query(query)
              else "bm25(messages), updated DESC")
    sql = f"""
            SELECT conversation_id, title, message_index, role, content,
                   snippet(messages, 4, '', '', '…', 28),
                   bm25(messages), updated
              FROM messages
             WHERE messages MATCH ?
          ORDER BY {_order}
             LIMIT ?
            """
    conn = _connect()
    rows = []
    # A query written with operators that FTS5 still cannot parse is searched
    # again as plain keywords rather than dropped.
    for attempt in dict.fromkeys((fts_q, _quoted_fts_query(query))):
        try:
            rows = conn.execute(sql, (attempt, limit)).fetchall()
            break
        except sqlite3.OperationalError:
            continue
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


# FTS5 reads AND, OR and NOT only as whole uppercase words, and NEAR only as
# NEAR(...). Anything else is text: "for", "notes" and "candy" hold no operator.
_FTS_OPERATOR = re.compile(r"(?:^|\s)(?:AND|OR|NOT)(?=\s|$)|\bNEAR\s*\(")


def _safe_fts_query(q: str) -> str:
    """A query that uses FTS5 operators passes through; any other is searched
    as the AND of its words, each quoted so punctuation is plain text."""
    q = (q or "").strip()
    if _FTS_OPERATOR.search(q):
        return q
    return _quoted_fts_query(q)


def _quoted_fts_query(q: str) -> str:
    tokens = [t.strip('"').strip() for t in (q or "").split()]
    tokens = [t for t in tokens if t and t not in ("AND", "OR", "NOT")]
    if not tokens:
        return (q or "").strip()
    return " ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens)


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
                n_files = sum(1 for f in os.listdir(CONVOS_DIR)
                              if f.endswith(".json") and not is_write_temp(f))
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
            return "There are no earlier chats; this is the first one."
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
    out.append("\nread_chat with a convo_id reads that whole chat in order.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Reading one chat whole
# ---------------------------------------------------------------------------

_PER_MESSAGE = 2000


def _find_chat(chat: str) -> tuple:
    """(chat, candidates): the chat named by id or title, or the closest titles
    when the name fits several."""
    from . import chat_sync
    want = (chat or "").strip()
    if not want:
        return None, []
    every = chat_sync.listing(limit=100000)
    for c in every:
        if c["id"] == want:
            return c, []
    low = want.lower()
    words = set(re.findall(r"\w+", low))

    def fit(c):
        t = (c.get("title") or "").lower()
        if t == low:
            return 3.0
        if low in t:
            return 2.0
        tw = set(re.findall(r"\w+", t))
        return len(words & tw) / max(1, len(words))
    ranked = sorted(((fit(c), c) for c in every), key=lambda x: (-x[0], -int(x[1]["updated"] or 0)))
    ranked = [(f, c) for f, c in ranked if f >= 0.5]
    if not ranked:
        return None, []
    top = ranked[0][0]
    tied = [c for f, c in ranked if f == top]
    if len(tied) == 1 or top >= 3.0:
        return tied[0], []
    return None, [c for _f, c in ranked[:6]]


def _when(ms) -> str:
    import datetime
    try:
        v = float(ms or 0)
        return datetime.datetime.fromtimestamp(v / (1000 if v > 1e12 else 1)).strftime("%Y-%m-%d %H:%M") if v else ""
    except (TypeError, ValueError, OSError):
        return ""


def _line(i: int, m: dict) -> str:
    from .chat_sync import _text
    text = _text(m.get("content")).strip()
    if len(text) > _PER_MESSAGE:
        text = text[:_PER_MESSAGE].rstrip() + f" … ({len(text) - _PER_MESSAGE} more characters)"
    steps = [e.get("name") for e in (m.get("events") or []) if isinstance(e, dict) and e.get("kind") == "tool" and e.get("name")]
    did = f"  [used: {', '.join(steps[:12])}{'…' if len(steps) > 12 else ''}]" if steps else ""
    when = _when(m.get("at"))
    return f"#{i} {m.get('role')}{(' · ' + when) if when else ''}{did}\n{text}"


def read_chat(chat: str, start: Optional[int] = None, budget: int = 11000) -> str:
    """One chat in order, for picking up where it left off. Long chats show the
    first message and the most recent ones; `start` reads on from a message."""
    from . import chat_sync
    found, near = _find_chat(chat)
    if not found:
        if near:
            rows = "\n".join(f"- {c['title']}  ({_when(c['updated'])}, {c['count']} messages)  id={c['id']}" for c in near)
            return f"More than one chat fits \"{chat}\". Read one by its id:\n{rows}"
        return f"No chat is called \"{chat}\". search_chats finds one by what was said in it."
    d = chat_sync.load(found["id"]) or {}
    msgs = [m for m in (d.get("messages") or [])
            if m.get("role") in ("user", "assistant") and not m.get("silent")
            and chat_sync._text(m.get("content")).strip() != "__HEARTH_NOTIFY_FLUSH__"]
    head = f"Chat: {found['title']} ({_when(found['updated'])}, {len(msgs)} messages, id={found['id']})"
    if not msgs:
        return head + "\nIt has no messages."
    lines = [_line(i, m) for i, m in enumerate(msgs)]
    if start is not None:
        start = max(0, min(int(start), len(lines) - 1))
        out, used, i = [], 0, start
        while i < len(lines) and (not out or used + len(lines[i]) <= budget):
            out.append(lines[i]); used += len(lines[i]); i += 1
        more = f"\n[#{i}–#{len(lines) - 1} follow; read_chat with start={i} goes on]" if i < len(lines) else ""
        return head + "\n\n" + "\n\n".join(out) + more
    # The first message says what the chat was for; the latest say where it got to.
    first = lines[0]
    tail, used, i = [], len(first), len(lines) - 1
    while i > 0 and (not tail or used + len(lines[i]) <= budget):
        tail.insert(0, lines[i]); used += len(lines[i]); i -= 1
    gap = f"[#1–#{i} left out; read_chat with start=1 reads them]" if i >= 1 else ""
    return head + "\n\n" + "\n\n".join([first] + ([gap] if gap else []) + tail)
