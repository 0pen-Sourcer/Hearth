"""Memory's index: every exchange of every chat, the saved facts, and the
"where were we" card of each chat, in one SQLite file.

An exchange (what was said and the reply to it) is one row of an FTS5 table,
with the chat, when it happened and the names it mentions. Facts are indexed
from their markdown files, which stay the copy a person edits; the index is
rebuilt from them whenever they change. Cards (memory_cards) live here too,
and are the one table that is not rebuilt from elsewhere.

Ranking is one score, no embeddings: BM25 plus the names a query shares with a
row, how recent it is, how much it matters, and a lift for a chat still open.
Words for time ("yesterday", "last week", "in June") become a date range by
rule, never by asking a model.

Public API:
    rebuild_index() -> int                         # every chat indexed again
    search(query, limit=8, after=, before=) -> List[Match]
    format_matches(matches) -> str
    time_range(text) -> (start, end, rest)
    names(text) -> List[str]
    fact_candidates(text, limit=10) -> List[dict]
    read_chat(chat, start=None) -> str
"""

from __future__ import annotations

import calendar
import datetime as _dt
import json
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .tools import WORKSPACE, is_write_temp

INDEX_PATH = os.path.join(WORKSPACE, "session_index.db")
CONVOS_DIR = os.path.join(WORKSPACE, "conversations")
NOTIFY_FLUSH = "__HEARTH_NOTIFY_FLUSH__"

_SCHEMA = 2
_LOCK = threading.RLock()


@dataclass
class Match:
    conversation_id: str
    title: str
    message_index: int        # the message number read_chat gives this exchange
    role: str                 # "exchange", or "card" for a chat's card
    content: str              # what was said
    snippet: str
    score: float
    updated: float            # when, in seconds
    reply: str = ""
    kind: str = "round"


# Marks a row that came from the recency fallback rather than a real keyword
# hit, so "these answer it" and "nothing matched, here is the latest" read apart.
RECENCY_SCORE = 999.0


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    path = path or INDEX_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS rounds USING fts5(
            chat UNINDEXED, title UNINDEXED, msg UNINDEXED, at UNINDEXED,
            said, reply, names, tokenize='porter unicode61');
        CREATE TABLE IF NOT EXISTS chats (chat TEXT PRIMARY KEY, mtime REAL, rounds INTEGER);
        CREATE VIRTUAL TABLE IF NOT EXISTS facts USING fts5(
            slug UNINDEXED, name, text, names, archived UNINDEXED, valid_to UNINDEXED,
            importance UNINDEXED, updated UNINDEXED, source_chat UNINDEXED,
            tokenize='porter unicode61');
        CREATE TABLE IF NOT EXISTS cards (chat TEXT PRIMARY KEY, data TEXT, status TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS passes (chat TEXT PRIMARY KEY, rounds INTEGER, at REAL);
    """)
    row = conn.execute("SELECT v FROM kv WHERE k='schema'").fetchone()
    if not row or int(row[0]) < _SCHEMA:
        # The first index held one row per message; it is derived, so it goes.
        conn.executescript("DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS meta;")
        conn.execute("INSERT OR REPLACE INTO kv(k, v) VALUES ('schema', ?)", (str(_SCHEMA),))
        conn.commit()
    return conn


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# ---------------------------------------------------------------- names
_NAME_STOP = frozenset("""
i im i'm ive i've id i'd ill i'll the a an and or but so if then this that these those it its it's
my your our their his her we you they he she me us them what when where why how which who whom
yes no ok okay hey hi hello thanks thank sure well also just now today tomorrow yesterday
monday tuesday wednesday thursday friday saturday sunday please can could would should will
do does did is are was were be been have has had not don't dont let lets let's there here
""".split())
_RUN = re.compile(r"(?:[A-Z][\w'+-]*|[A-Z]{2,}\w*)(?:[ \t]+(?:[A-Z][\w'+-]*|\d[\w+-]*(?:\.\d+)?|[A-Z]{2,}\w*))*")
_HANDLE = re.compile(r"[@#][A-Za-z0-9_][\w.-]{1,40}")
_PRICE = re.compile(r"(?:[₹$€£]|\brs\.?\s?|\binr\s?|\busd\s?)\s?\d[\d,.]*\s?[kKmMlL]?\b|\b\d[\d,.]*\s?(?:k|lakh|rupees|dollars)\b", re.I)
_MIXED = re.compile(r"\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{2,}(?:[-.][A-Za-z0-9]+)*\b")


def _norm_name(s: str) -> str:
    return " ".join(s.strip(" .,:;!?'\"()[]").lower().split())


def names(text: str, limit: int = 24) -> List[str]:
    """Proper nouns, products, handles, prices and model-like words in a text,
    lowercased: what ties one chat to another without vectors."""
    text = text or ""
    found: List[str] = []

    def add(s: str) -> None:
        n = _norm_name(s)
        if len(n) < 2 or n in _NAME_STOP or n in found:
            return
        found.append(n)

    for m in _RUN.finditer(text):
        words = m.group(0).split()
        # A capital that only starts a sentence is not a name on its own.
        start = m.start()
        before = text[:start].rstrip()
        sentence_start = not before or before[-1] in ".!?\n:"
        while words and words[0].lower().strip(".,'") in _NAME_STOP:
            words = words[1:]
            sentence_start = False
        if not words:
            continue
        if len(words) == 1 and sentence_start and not any(c.isdigit() for c in words[0]) \
                and not words[0].isupper():
            continue
        add(" ".join(words))
    for rx in (_HANDLE, _PRICE, _MIXED):
        for m in rx.finditer(text):
            add(m.group(0))
    return found[:limit]


def _name_hits(row_names: str, q_tokens: set) -> int:
    hits = 0
    for n in (row_names or "").split("|"):
        toks = [t for t in re.findall(r"\w+", n) if len(t) >= 2]
        if toks and any(t in q_tokens for t in toks if not t.isdigit() or len(t) >= 3):
            hits += 1
    return hits


# ---------------------------------------------------------------- time words
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_DAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "couple of": 2, "few": 3}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
_NUMW = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple of|few)"


def time_range(text: str, now: Optional[float] = None) -> Tuple[Optional[float], Optional[float], str]:
    """A span of time named in `text`, as (start, end) in seconds, and the text
    without those words. (None, None, text) when it names none."""
    t = text or ""
    now_dt = _dt.datetime.fromtimestamp(now if now is not None else time.time())
    day0 = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    low = t.lower()

    def out(a: _dt.datetime, b: _dt.datetime, m: "re.Match") -> Tuple[float, float, str]:
        rest = (t[:m.start()] + " " + t[m.end():])
        return a.timestamp(), b.timestamp(), " ".join(rest.split())

    rules: List[Tuple[str, Any]] = [
        (r"\b(?:earlier today|this morning|this afternoon|this evening|tonight|today|just now|a moment ago|moments ago)\b",
         lambda m: (day0, now_dt)),
        (r"\blast night\b", lambda m: (day0 - _dt.timedelta(hours=6), day0 + _dt.timedelta(hours=6))),
        (r"\b(?:yesterday|day before)\b", lambda m: (day0 - _dt.timedelta(days=1), day0)),
        (r"\bthis week\b", lambda m: (day0 - _dt.timedelta(days=day0.weekday()), now_dt)),
        (r"\blast week\b", lambda m: (day0 - _dt.timedelta(days=day0.weekday() + 7),
                                      day0 - _dt.timedelta(days=day0.weekday()))),
        (r"\bthis month\b", lambda m: (day0.replace(day=1), now_dt)),
        (r"\blast month\b", lambda m: ((day0.replace(day=1) - _dt.timedelta(days=1)).replace(day=1), day0.replace(day=1))),
        (r"\bthis year\b", lambda m: (day0.replace(month=1, day=1), now_dt)),
        (r"\blast year\b", lambda m: (day0.replace(year=day0.year - 1, month=1, day=1), day0.replace(month=1, day=1))),
        (rf"\b(?:in the |over the )?(?:past|last) {_NUMW} (day|week|month|year)s?\b",
         lambda m: (now_dt - _dt.timedelta(days=_count(m.group(1)) * _UNIT_DAYS[m.group(2)]), now_dt)),
        (rf"\b{_NUMW} (day|week|month|year)s? ago\b", lambda m: _ago(day0, _count(m.group(1)), m.group(2))),
        (r"\bthe other day\b", lambda m: (day0 - _dt.timedelta(days=7), day0)),
        (r"\b(?:recently|lately)\b", lambda m: (now_dt - _dt.timedelta(days=14), now_dt)),
        (r"\b(?:on |last )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lambda m: _weekday(day0, m.group(1))),
        (r"\b(?:in|during|back in|on|since) (january|february|march|april|may|june|july|august|september|october|"
         r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
         lambda m: _month(day0, m.group(1))),
    ]
    for rx, span in rules:
        m = re.search(rx, low)
        if m:
            a, b = span(m)
            return out(a, b, m)
    return None, None, t


def _count(w: str) -> int:
    w = (w or "").strip().lower()
    return int(w) if w.isdigit() else _NUM.get(w, 1)


def _ago(day0: _dt.datetime, n: int, unit: str):
    span = _UNIT_DAYS[unit]
    centre = day0 - _dt.timedelta(days=n * span)
    pad = max(1, span // 2) if span > 1 else 0
    return centre - _dt.timedelta(days=pad), centre + _dt.timedelta(days=pad + 1)


def _weekday(day0: _dt.datetime, name: str):
    back = (day0.weekday() - _DAYS[name]) % 7 or 7
    d = day0 - _dt.timedelta(days=back)
    return d, d + _dt.timedelta(days=1)


def _month(day0: _dt.datetime, name: str):
    mo = _MONTHS.get(name[:3] if name not in _MONTHS else name) or _MONTHS[name]
    year = day0.year if mo <= day0.month else day0.year - 1
    a = day0.replace(year=year, month=mo, day=1)
    b = (a + _dt.timedelta(days=32)).replace(day=1)
    return a, b


def _when_arg(v: Any, edge: str, now: Optional[float] = None) -> Optional[float]:
    """A before/after value: a date, a date and time, or words for a time."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000 if float(v) > 1e12 else 1)
    s = str(v).strip()
    try:
        return _dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        pass
    a, b, _rest = time_range(s, now)
    if a is None:
        return None
    return a if edge == "start" else b


# ---------------------------------------------------------------- what gets indexed
_INJECTED = (re.compile(r"<lately>[\s\S]*?</lately>\s*"),
             re.compile(r"\s*\[Current local time:[^\]]*\]\s*$"))


def clean_said(text: str) -> str:
    """What a person typed, without what Hearth put in front of it or after it."""
    for rx in _INJECTED:
        text = rx.sub("", text or "")
    return text.strip()


def _text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return content if isinstance(content, str) else ""


def rounds_of(messages: List[Dict[str, Any]], default_at: float = 0.0) -> List[Dict[str, Any]]:
    """A chat's messages as exchanges: each thing said with the reply to it.
    `msg` is the number read_chat gives the message that opens the exchange."""
    out: List[Dict[str, Any]] = []
    n = -1
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or m.get("silent"):
            continue
        text = _text(m.get("content")).strip()
        if text == NOTIFY_FLUSH:
            continue
        n += 1
        at = m.get("at")
        try:
            at = float(at) / (1000 if float(at) > 1e12 else 1) if at else default_at
        except (TypeError, ValueError):
            at = default_at
        if m["role"] == "user" or not out:
            out.append({"msg": n, "at": at, "said": clean_said(text) if m["role"] == "user" else "",
                        "reply": "" if m["role"] == "user" else text})
        else:
            out[-1]["reply"] = (out[-1]["reply"] + "\n" + text).strip()
    return [r for r in out if r["said"] or r["reply"]]


def _index_chat(conn: sqlite3.Connection, cid: str, title: str, messages: List[Dict[str, Any]],
                at: float, mtime: float) -> int:
    conn.execute("DELETE FROM rounds WHERE chat = ?", (cid,))
    rows = rounds_of(messages, at)
    for r in rows:
        conn.execute("INSERT INTO rounds(chat, title, msg, at, said, reply, names) VALUES (?,?,?,?,?,?,?)",
                     (cid, title, r["msg"], r["at"], r["said"][:8000], r["reply"][:8000],
                      "|".join(names(r["said"] + "\n" + r["reply"][:3000]))))
    conn.execute("INSERT OR REPLACE INTO chats(chat, mtime, rounds) VALUES (?,?,?)", (cid, mtime, len(rows)))
    return len(rows)


def _sources() -> Dict[str, str]:
    """chat id -> file, for every chat and the old terminal history."""
    out: Dict[str, str] = {}
    try:
        names_ = os.listdir(CONVOS_DIR)
    except OSError:
        names_ = []
    for fn in names_:
        # A staged write carries .json and an id that was never indexed.
        if fn.endswith(".json") and not is_write_temp(fn) and not fn.startswith("."):
            out[fn[:-5]] = os.path.join(CONVOS_DIR, fn)
    logs = os.path.join(os.path.dirname(CONVOS_DIR), "logs")
    for cid, fn in (("cli-history", "jarvis_history.json"), ("cli-transcript", "cli_transcript.jsonl")):
        p = os.path.join(logs, fn)
        if os.path.isfile(p):
            out[cid] = p
    return out


def _read_source(cid: str, path: str) -> Tuple[str, str, List[Dict[str, Any]], float]:
    """(id, title, messages, when) of one source."""
    mt = _mtime(path)
    if cid == "cli-transcript":
        msgs = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        msgs.append(rec)
        except OSError:
            pass
        return cid, "CLI transcript (full)", msgs, mt
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if cid == "cli-history":
        return cid, "CLI session history", data if isinstance(data, list) else [], mt
    if not isinstance(data, dict):
        return cid, "Untitled", [], mt
    upd = data.get("updated") or mt
    try:
        upd = float(upd) / (1000 if float(upd) > 1e12 else 1)
    except (TypeError, ValueError):
        upd = mt
    return str(data.get("id") or cid), data.get("title") or "Untitled", data.get("messages") or [], upd


def _needs_reindex() -> bool:
    """Whether a chat changed, appeared or went since it was indexed."""
    if not os.path.isfile(INDEX_PATH):
        return True
    try:
        conn = _connect()
        indexed = dict(conn.execute("SELECT chat, mtime FROM chats").fetchall())
        conn.close()
    except sqlite3.Error:
        return True
    src = _sources()
    if set(src) != set(indexed):
        return True
    return any(_mtime(p) > (indexed.get(c) or 0) + 0.5 for c, p in src.items())


def sync() -> int:
    """Index the chats that changed since the last time; drop the ones gone.
    Returns how many chats were indexed again."""
    with _LOCK:
        conn = _connect()
        try:
            indexed = dict(conn.execute("SELECT chat, mtime FROM chats").fetchall())
            src = _sources()
            done = 0
            for gone in set(indexed) - set(src):
                conn.execute("DELETE FROM rounds WHERE chat = ?", (gone,))
                conn.execute("DELETE FROM chats WHERE chat = ?", (gone,))
            for cid, path in src.items():
                mt = _mtime(path)
                if cid in indexed and mt <= (indexed[cid] or 0) + 0.5:
                    continue
                try:
                    real, title, msgs, at = _read_source(cid, path)
                except (OSError, ValueError):
                    continue
                if real != cid:
                    conn.execute("DELETE FROM rounds WHERE chat = ?", (real,))
                _index_chat(conn, cid, title, msgs, at, mt)
                done += 1
            conn.commit()
            return done
        finally:
            conn.close()


def rebuild_index() -> int:
    """Index every chat again. Returns the exchanges indexed. Cards and what the
    memory pass has done are kept: they are not derived from the chats."""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM rounds")
            conn.execute("DELETE FROM chats")
            conn.commit()
        finally:
            conn.close()
    sync()
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
    finally:
        conn.close()


def chat_rounds(cid: str) -> int:
    """How many exchanges a chat has, as indexed."""
    conn = _connect()
    try:
        row = conn.execute("SELECT rounds FROM chats WHERE chat = ?", (cid,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------- facts
def _fact_rows() -> List[Dict[str, Any]]:
    from . import memory
    out = []
    for root, archived in ((memory.MEM_DIR, 0), (memory._ARCHIVE_DIR, 1)):
        try:
            files = os.listdir(root)
        except OSError:
            continue
        for fn in files:
            if not fn.endswith(".md") or fn.lower() == "memory.md" or fn.startswith("."):
                continue
            try:
                fm, body = memory._read_body(os.path.join(root, fn))
            except (OSError, UnicodeDecodeError):
                continue
            text = (fm.get("description") or "") + "\n" + body
            out.append({"slug": fn[:-3], "name": fm.get("name") or fn[:-3], "text": text,
                        "names": "|".join(names((fm.get("name") or "") + "\n" + text)),
                        "archived": archived, "valid_to": fm.get("valid_to") or ("archived" if archived else ""),
                        "importance": fm.get("importance") or "2", "updated": fm.get("updated") or "",
                        "source_chat": fm.get("source_chat") or "", "description": fm.get("description") or ""})
    return out


def _facts_stamp() -> str:
    from . import memory
    parts = []
    for root in (memory.MEM_DIR, memory._ARCHIVE_DIR):
        try:
            for e in os.scandir(root):
                if e.name.endswith(".md"):
                    parts.append(f"{root[-8:]}/{e.name}:{e.stat().st_mtime_ns}:{e.stat().st_size}")
        except OSError:
            pass
    import hashlib
    return hashlib.sha1("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def sync_facts() -> None:
    """Index the fact files again when any of them changed."""
    stamp = _facts_stamp()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT v FROM kv WHERE k='facts_stamp'").fetchone()
            if row and row[0] == stamp:
                return
            conn.execute("DELETE FROM facts")
            for r in _fact_rows():
                conn.execute("INSERT INTO facts(slug, name, text, names, archived, valid_to, importance, updated,"
                             " source_chat) VALUES (?,?,?,?,?,?,?,?,?)",
                             (r["slug"], r["name"], r["text"][:6000], r["names"], r["archived"], r["valid_to"],
                              r["importance"], r["updated"], r["source_chat"]))
            conn.execute("INSERT OR REPLACE INTO kv(k, v) VALUES ('facts_stamp', ?)", (stamp,))
            conn.commit()
        finally:
            conn.close()


def fact_candidates(text: str, limit: int = 10) -> List[Dict[str, str]]:
    """Up to `limit` current facts a conversation may touch: by its words and
    the names in it. What the memory pass diffs its proposals against."""
    sync_facts()
    toks = _query_tokens(text)
    if not toks:
        return []
    # The rarer words carry the topic; the most frequent forty are plenty.
    counts: Dict[str, int] = {}
    for t in toks:
        counts[t] = counts.get(t, 0) + 1
    words = sorted(counts, key=lambda w: (-len(w) if counts[w] == 1 else -counts[w]))[:40]
    q = " OR ".join(f'"{w}"' for w in words)
    conn = _connect()
    try:
        rows = conn.execute("SELECT slug, name, text, names, bm25(facts) FROM facts WHERE facts MATCH ? "
                            "AND archived = 0 ORDER BY bm25(facts) LIMIT 40", (q,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    qset = set(toks)
    scored = []
    best = max([-r[4] for r in rows] or [1.0]) or 1.0
    for slug, name, body, nm, b in rows:
        scored.append(((-b) / best + 0.8 * min(1.0, _name_hits(nm, qset) / 2), slug, name, body))
    scored.sort(key=lambda x: -x[0])
    return [{"slug": s, "title": n, "description": " ".join(t.split("\n", 1)[0].split())[:160]}
            for _sc, s, n, t in scored[:limit]]


# ---------------------------------------------------------------- search
_STOP = frozenset("""
a an and are as at be but by can could do does did for from get give had has have how i in into is it its
like make me my need of on or please show so some tell that the their them then there these this those to
use using want was what when where which who why will with would you your about we were us our
remember recall said say talk talked talking chat chats discussed discuss again earlier before
""".split())


def _query_tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"\w+", (text or "").lower()) if len(t) >= 2 and t not in _STOP]


_TEMPORAL_RE = re.compile(
    r"\b(yesterday|today|tonight|this morning|last night|earlier|recently|lately|just now|last time|previously|"
    r"the other day|(a\s+)?few\s+(days?|hours?)\s+ago|\d+\s+(days?|hours?|weeks?)\s+ago|this week|last week|"
    r"what were we|what did we|what was i|what have we|continue where|pick up where|where we left)\b", re.I)


def _is_temporal_query(q: str) -> bool:
    return bool(_TEMPORAL_RE.search(q or ""))


def _ranked_query(q: str) -> str:
    toks = _query_tokens(q) or [t for t in re.findall(r"\w+", (q or "").lower()) if t]
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(toks))


def _open_chats(conn: sqlite3.Connection) -> set:
    try:
        return {r[0] for r in conn.execute("SELECT chat FROM cards WHERE status = 'open'").fetchall()}
    except sqlite3.Error:
        return set()


def score(bm25n: float, name_overlap: float, age_days: float, importance: float, is_open: bool,
          half_life: float = 14.0) -> float:
    """One number for how well an item answers a query; higher is better."""
    return (bm25n * 1.0 + name_overlap * 0.8 + math.exp(-max(0.0, age_days) / half_life) * 0.5
            + (importance / 3.0) * 0.3 + (0.5 if is_open else 0.0))


def search(query: str, limit: int = 8, *, after: Any = None, before: Any = None,
           match_all: bool = False, now: Optional[float] = None) -> List[Match]:
    """The exchanges, and the cards, that best answer a query, best first.

    Time words in the query, and `after` / `before`, limit it to a span. With
    no words left once those are taken out, it is a look by time: the
    exchanges in that span, newest first. `match_all` keeps every word
    required, the way the sidebar's search box reads what is typed."""
    query = (query or "").strip()
    now = now if now is not None else time.time()
    start, end, rest = time_range(query, now) if not match_all else (None, None, query)
    a = _when_arg(after, "start", now)
    b = _when_arg(before, "end", now)
    start = a if a is not None else start
    end = b if b is not None else end
    if not rest.strip() and start is None and end is None and not query:
        return []
    if _needs_reindex():
        sync()
    if not os.path.isfile(INDEX_PATH):
        return []
    lo = start if start is not None else 0.0
    hi = end if end is not None else 1e13
    conn = _connect()
    try:
        open_ = _open_chats(conn)
        keywords = _query_tokens(rest) if not match_all else re.findall(r"\w+", rest)
        if not keywords:
            if start is None and end is None:
                return _recent(limit)
            rows = conn.execute(
                "SELECT chat, title, msg, at, said, reply, substr(said, 1, 220) FROM rounds "
                "WHERE CAST(at AS REAL) >= ? AND CAST(at AS REAL) < ? ORDER BY CAST(at AS REAL) DESC LIMIT ?",
                (lo, hi, limit)).fetchall()
            return [Match(r[0], r[1], int(r[2]), "exchange", r[4], r[6] or (r[5] or "")[:220], 0.0, float(r[3]),
                          reply=r[5]) for r in rows]
        tries = ([_safe_fts_query(rest), _quoted_fts_query(rest)] if match_all
                 else ([rest] if _FTS_OPERATOR.search(rest) else []) + [_ranked_query(rest)])
        rows = []
        for q in dict.fromkeys(tries):
            try:
                rows = conn.execute(
                    "SELECT chat, title, msg, at, said, reply, snippet(rounds, -1, '', '', '…', 24), "
                    "bm25(rounds, 1.0, 0.7, 1.5), names FROM rounds WHERE rounds MATCH ? "
                    "AND CAST(at AS REAL) >= ? AND CAST(at AS REAL) < ? ORDER BY bm25(rounds, 1.0, 0.7, 1.5) LIMIT 60",
                    (q, lo, hi)).fetchall()
                break
            except sqlite3.OperationalError:
                continue
        qset = set(_query_tokens(rest))
        best = max([-r[7] for r in rows] or [1.0]) or 1.0
        temporal = _is_temporal_query(query)
        out: List[Match] = []
        for r in rows:
            age = (now - float(r[3] or 0)) / 86400.0
            s = score((-r[7]) / best, min(1.0, _name_hits(r[8], qset) / 2), age, 1.0, r[0] in open_,
                      half_life=3.0 if temporal else 14.0)
            out.append(Match(r[0], r[1], int(r[2]), "exchange", r[4], r[6], round(s, 3), float(r[3] or 0), reply=r[5]))
        if not match_all:
            out.extend(_card_matches(qset, now, lo, hi))
        out.sort(key=lambda m: (-m.score, -m.updated))
        # One exchange per chat first, so one long chat does not fill the list.
        seen, first, later = set(), [], []
        for m in out:
            (later if (m.conversation_id, m.kind) in seen else first).append(m)
            seen.add((m.conversation_id, m.kind))
        out = (first + later)[:limit]
    finally:
        conn.close()
    if out or match_all or start is not None or end is not None:
        return out
    # Nothing matched a vague question: the latest exchanges, marked as such.
    return _recent(limit)


def _card_matches(qset: set, now: float, lo: float, hi: float) -> List[Match]:
    from . import memory_cards
    out = []
    for c in memory_cards.everything(now=now):
        upd = float(c.get("updated") or 0)
        if not (lo <= upd < hi) or not qset:
            continue
        text = memory_cards.card_text(c)
        toks = set(_query_tokens(text))
        hit = len(qset & toks)
        if not hit:
            continue
        s = score(hit / max(1, len(qset)), min(1.0, _name_hits("|".join(c.get("names") or []), qset) / 2),
                  (now - upd) / 86400.0, 2.0, c.get("status") == "open")
        out.append(Match(c["chat"], c.get("title") or c.get("goal") or "", 0, "card", text, text[:240],
                         round(s, 3), upd, kind="card"))
    return out


def _recent(limit: int = 8) -> List[Match]:
    """The newest exchanges anywhere: what a vague question gets when no word
    of it matched, marked so the model does not take them for an answer."""
    if not os.path.isfile(INDEX_PATH):
        return []
    conn = _connect()
    try:
        rows = conn.execute("SELECT chat, title, msg, at, said, reply FROM rounds "
                            "ORDER BY CAST(at AS REAL) DESC, CAST(msg AS INTEGER) DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return [Match(r[0], r[1], int(r[2]), "exchange", r[4], (r[4] or r[5] or "")[:200], RECENCY_SCORE,
                  float(r[3] or 0), reply=r[5]) for r in rows]


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


_RESULT_CHARS = 6000          # about 1.5k tokens, what a small model reads well


def format_matches(matches: List[Match]) -> str:
    """Matches as the model reads them: each a small item with when, which chat,
    what was said and the reply, within about 1.5k tokens."""
    if not matches:
        try:
            n_files = len([c for c in _sources() if not c.startswith("cli-")])
        except Exception:
            n_files = -1
        if n_files == 0:
            return "There are no earlier chats; this is the first one."
        return ("No past conversation matched. Try other words, fewer words, or a time "
                "(after/before) to list the chats from then.")
    _fallback = all(getattr(m, "score", 0) == RECENCY_SCORE for m in matches)
    if _fallback:
        out: List[str] = [
            "No message actually matched that query. These are only the most RECENT exchanges, "
            "which may be unrelated. Do not present them as the answer; if they don't cover it, say "
            "the search found nothing and ask for a specific term, file or topic."]
    else:
        out = [f"{len(matches)} found:"]
    used = len(out[0])
    for i, m in enumerate(matches, 1):
        when = _when(m.updated)
        if m.kind == "card":
            item = f"\n[{i}] Going on, in chat \"{m.title}\" ({when}): {m.content}\n    convo_id={m.conversation_id}"
        else:
            said = " ".join((m.content or "").split())
            reply = " ".join((m.reply or "").split())
            item = (f"\n[{i}] {m.title}  ({when}, message #{m.message_index})\n"
                    + (f"    They said: {said[:360]}{'…' if len(said) > 360 else ''}\n" if said else "")
                    + (f"    Reply: {reply[:360]}{'…' if len(reply) > 360 else ''}\n" if reply else "")
                    + f"    convo_id={m.conversation_id}")
        if used + len(item) > _RESULT_CHARS:
            out.append(f"\n({len(matches) - i + 1} more left out)")
            break
        out.append(item)
        used += len(item)
    out.append("\nTo carry on from one, read_chat with its convo_id reads it in order.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Reading one chat whole
# ---------------------------------------------------------------------------

_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "AppData", "$Recycle.Bin"}
_CHATS: dict = {"stamp": None, "list": []}


def _chat_titles() -> List[dict]:
    """Every chat's title, read again only when a chat file has changed."""
    from . import chat_sync
    try:
        stamp = tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(CONVOS_DIR)
                             if e.name.endswith(".json")))
    except OSError:
        stamp = ()
    if stamp != _CHATS["stamp"]:
        _CHATS["list"] = [c for c in chat_sync.listing(limit=100000) if c.get("count")]
        _CHATS["stamp"] = stamp
    return _CHATS["list"]


def mention(query: str, limit: int = 8, budget_s: float = 0.4) -> List[dict]:
    """What "@words" in the message box can point at: chats by title, and files
    and folders by name in the usual places, quickly and never exhaustively."""
    import time as _time
    from . import chat_sync
    from .tools import WORKSPACE
    words = [w for w in re.findall(r"\w+", (query or "").lower())]
    out: List[dict] = []
    for c in _chat_titles():
        t = (c.get("title") or "").lower()
        if all(w in t for w in words):
            out.append({"kind": "chat", "name": c.get("title") or "Untitled", "id": c["id"],
                        "sub": _when(c.get("updated")) + f" · {c.get('count', 0)} messages"})
            if len([o for o in out if o["kind"] == "chat"]) >= (5 if words else 4):
                break
    home = os.path.expanduser("~")
    roots = [WORKSPACE] + [os.path.join(home, d) for d in ("Desktop", "Documents", "Downloads")]
    if not words:
        for r in roots:
            if os.path.isdir(r):
                out.append({"kind": "folder", "name": os.path.basename(r) or r, "path": r, "sub": r})
        return out
    end = _time.time() + budget_s
    found: List[dict] = []
    seen = 0

    def walk(d: str, depth: int) -> None:
        nonlocal seen
        if depth > 3 or _time.time() > end or len(found) >= limit * 3:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            seen += 1
            if e.name.startswith(".") or e.name in _SKIP_DIRS:
                continue
            low = e.name.lower()
            is_dir = e.is_dir(follow_symlinks=False)
            if all(w in low for w in words):
                found.append({"kind": "folder" if is_dir else "file", "name": e.name, "path": e.path,
                              "sub": os.path.dirname(e.path)})
            if is_dir:
                walk(e.path, depth + 1)
            if _time.time() > end:
                return

    for r in roots:
        if os.path.isdir(r):
            walk(r, 0)
    # Closest names first: a name that starts with what was typed, then shorter.
    lead = words[0] if words else ""
    found.sort(key=lambda f: (not f["name"].lower().startswith(lead), len(f["name"])))
    return out + found[:limit]

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
    try:
        from . import memory_cards
        card = memory_cards.get(found["id"])
        if card:
            head += "\nWhere it stood: " + memory_cards.card_text(card)
    except Exception:
        pass
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
