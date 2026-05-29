"""Long-term memory for Jarvis.

Pattern (cribbed from Claude Code's auto-memory):
  ~/Jarvis/memory/MEMORY.md       — always-loaded index. One line per fact.
  ~/Jarvis/memory/<slug>.md       — per-fact file. YAML frontmatter + body.

Why split across files? The index is cheap to inject into the system prompt
every turn (gives the model awareness of what it knows). Per-fact bodies are
loaded only when the model calls memory_recall — so we don't burn tokens on
facts irrelevant to the current question.

Four types (same as Claude Code, since the taxonomy is genuinely useful):
  user       — who the user is, role, expertise, preferences
  feedback   — corrections / confirmations about how to behave
  project    — ongoing context: what they're building, deadlines, decisions
  reference  — pointers to external places: links, paths, tool URLs

Hard caps:
  index      — 200 lines / 25KB (matches Claude Code's MEMDIR caps)
  per-entry  — model-driven; aim for tight bodies (a paragraph or two)
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .tools import WORKSPACE

MEM_DIR = os.path.join(WORKSPACE, "memory")
INDEX_PATH = os.path.join(MEM_DIR, "MEMORY.md")
RULES_PATH = os.path.join(WORKSPACE, "rules.md")

os.makedirs(MEM_DIR, exist_ok=True)

VALID_TYPES = ("user", "feedback", "project", "reference")
INDEX_LINE_CAP = 200
INDEX_BYTE_CAP = 25_000

# Map common LLM-generated type variants to the canonical four.
# Small models love to invent friendlier names like "User Preference" or
# "task" — instead of erroring back at them, normalize silently.
_TYPE_ALIASES = {
    # user
    "user": "user",
    "user_preference": "user",
    "userpreference": "user",
    "preference": "user",
    "preferences": "user",
    "pref": "user",
    "prefs": "user",
    "profile": "user",
    "identity": "user",
    "fact": "user",
    "facts": "user",
    "personal": "user",
    "info": "user",
    # feedback
    "feedback": "feedback",
    "rule": "feedback",
    "rules": "feedback",
    "correction": "feedback",
    "instruction": "feedback",
    "behavior": "feedback",
    "behaviour": "feedback",
    "guideline": "feedback",
    # project
    "project": "project",
    "projects": "project",
    "task": "project",
    "tasks": "project",
    "todo": "project",
    "todos": "project",
    "work": "project",
    "code": "project",
    "build": "project",
    "goal": "project",
    "plan": "project",
    # reference
    "reference": "reference",
    "ref": "reference",
    "link": "reference",
    "links": "reference",
    "url": "reference",
    "resource": "reference",
    "bookmark": "reference",
    "credential": "reference",
    "credentials": "reference",
    "creds": "reference",
    "password": "reference",
    "config": "reference",
    "setting": "reference",
    "settings": "reference",
}


def _normalize_type(raw: str) -> str:
    """Lenient mapping → canonical type. Falls back to 'user' for unknowns
    so memory_save never errors on a type the model guessed."""
    key = (raw or "user").strip().lower().replace("-", "_").replace(" ", "_")
    return _TYPE_ALIASES.get(key, "user")

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(title: str) -> str:
    s = _SLUG.sub("_", title.lower()).strip("_")
    return s or "memory"


def _path_for(title: str) -> str:
    return os.path.join(MEM_DIR, _slug(title) + ".md")


def _read_index_lines() -> List[str]:
    if not os.path.exists(INDEX_PATH):
        return []
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f.readlines()]


def _write_index_lines(lines: List[str]) -> str:
    """Write lines back, enforcing caps. Returns warning string or ''."""
    text = "\n".join(lines).rstrip() + "\n"
    warnings: List[str] = []
    if len(lines) > INDEX_LINE_CAP:
        lines = lines[:INDEX_LINE_CAP]
        text = "\n".join(lines).rstrip() + "\n"
        warnings.append(f"index truncated to {INDEX_LINE_CAP} lines")
    if len(text.encode("utf-8")) > INDEX_BYTE_CAP:
        # truncate to last newline before cap
        encoded = text.encode("utf-8")[:INDEX_BYTE_CAP]
        cut = encoded.rfind(b"\n")
        text = encoded[:cut].decode("utf-8", errors="ignore") + "\n"
        warnings.append(f"index truncated to {INDEX_BYTE_CAP} bytes")
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    return "; ".join(warnings)


def _index_entry(slug: str, title: str, mtype: str, hook: str) -> str:
    hook = hook.strip().replace("\n", " ")[:140]
    return f"- [{title}]({slug}.md) — `{mtype}` {hook}"


def _index_entry_for_slug(lines: List[str], slug: str) -> Optional[int]:
    pat = re.compile(rf"\]\({re.escape(slug)}\.md\)")
    for i, ln in enumerate(lines):
        if pat.search(ln):
            return i
    return None


def save(title: str, mtype: str, description: str, body: str = "",
         tags: Optional[List[str]] = None) -> str:
    """Write a memory file + add/update the index entry."""
    # Lenient: map any reasonable synonym to a canonical type instead
    # of erroring. Small models love to invent "User Preference" etc.
    canonical = _normalize_type(mtype)
    title = (title or "").strip()
    if not title:
        return "Error: title required"
    mtype = canonical
    slug = _slug(title)
    path = _path_for(title)
    tags = tags or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    front = ["---", f"name: {title}", f"type: {mtype}",
             f"description: {description.strip()}"]
    if tags:
        front.append(f"tags: [{', '.join(tags)}]")
    front.append(f"updated: {datetime.now().isoformat(timespec='seconds')}")
    front.append("---")
    content = "\n".join(front) + "\n\n" + body.strip() + "\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    lines = _read_index_lines()
    new_entry = _index_entry(slug, title, mtype, description)
    existing = _index_entry_for_slug(lines, slug)
    if existing is not None:
        lines[existing] = new_entry
        verb = "updated"
    else:
        lines.append(new_entry)
        verb = "saved"

    warn = _write_index_lines(lines)
    msg = f"{verb} memory: {slug} ({mtype})"
    if warn:
        msg += f"  [{warn}]"
    return msg


def forget(title: str) -> str:
    slug = _slug(title)
    path = _path_for(title)
    removed = False
    if os.path.exists(path):
        os.remove(path)
        removed = True
    lines = _read_index_lines()
    idx = _index_entry_for_slug(lines, slug)
    if idx is not None:
        lines.pop(idx)
        _write_index_lines(lines)
        removed = True
    if not removed:
        return f"no memory found matching '{title}'"
    return f"forgot: {slug}"


def list_index() -> str:
    lines = _read_index_lines()
    if not lines:
        return "(no memories yet)"
    return "\n".join(lines)


def index_for_prompt() -> str:
    """Compact form to inject into the system prompt every turn.

    The model should be able to see WHAT IT KNOWS without us loading every
    body. If a fact looks relevant to the current turn, it can call
    memory_recall to load the body."""
    lines = _read_index_lines()
    if not lines:
        return ""
    return "[ Memories on file (call memory_recall to load any) ]\n" + "\n".join(lines)


def _read_body(path: str) -> Tuple[Dict[str, str], str]:
    """Return (frontmatter_dict, body_text)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    fm: Dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            block = text[4:end]
            for ln in block.splitlines():
                if ":" in ln:
                    k, _, v = ln.partition(":")
                    fm[k.strip()] = v.strip()
            body = text[end + 4:].lstrip("\n")
    return fm, body


# Common words carry no recall signal and cause false matches (e.g. "run" in a
# question hitting "run_command" in an unrelated memory). Stripped before scoring.
_STOPWORDS = frozenset("""
a an and are as at be but by can could do does for from get give had has have how
i in into is it its like make me my need of on or please run show so some tell that
the their them then there these this those to use using want was what when where which
who why will with would you your find open play set go got am pm
""".split())


def _score_memories(query: str) -> List[Tuple[float, int, int, str, Dict[str, str], str]]:
    """Score every memory against the query. Returns
    [(score, matched_terms, total_terms, fn, fm, body)] sorted best-first.
    Score = keyword hits (filename hits weighted 3x) scaled by how MANY distinct
    query terms a fact matches (breadth beats repetition); recency is the tiebreak.
    Stopwords are dropped so common words don't create spurious matches."""
    terms = [t for t in (w.lower() for w in re.findall(r"\w+", query))
             if len(t) >= 2 and t not in _STOPWORDS]
    if not terms:
        return []
    total = len(terms)
    scored: List[Tuple[float, str, int, str, Dict[str, str], str]] = []
    for fn in os.listdir(MEM_DIR):
        if not fn.endswith(".md") or fn == "MEMORY.md":
            continue
        full = os.path.join(MEM_DIR, fn)
        try:
            fm, body = _read_body(full)
        except OSError:
            continue
        text = (fn + " " + " ".join(fm.values()) + " " + body).lower()
        matched = set()
        raw = 0.0
        for t in terms:
            c = text.count(t)
            if c:
                matched.add(t)
                raw += c * (3.0 if t in fn.lower() else 1.0)
        if raw <= 0:
            continue
        coverage = len(matched) / total  # 0..1
        score = raw * (0.5 + coverage)   # breadth-of-match boost
        scored.append((score, fm.get("updated", ""), len(matched), fn, fm, body))
    scored.sort(key=lambda c: (c[0], c[1]), reverse=True)  # score, then recency
    return [(s, m, total, fn, fm, body) for (s, _u, m, fn, fm, body) in scored]


def recall(query: str, limit: int = 5) -> str:
    """Keyword search across the memory store. Returns top N matches with bodies."""
    query = (query or "").strip()
    if not query:
        return list_index()
    candidates = _score_memories(query)[:limit]
    if not candidates:
        return f"(no memories match '{query}')"
    out: List[str] = []
    for score, _matched, _total, fn, fm, body in candidates:
        title = fm.get("name", fn[:-3])
        mtype = fm.get("type", "?")
        updated = fm.get("updated", "")
        out.append(f"## {title}  ({mtype}, score={score:.0f}, {updated})")
        out.append(body.strip()[:1500])
        out.append("")
    return "\n".join(out)


def recall_for_prompt(query: str, max_chars: int = 900, limit: int = 2) -> str:
    """Proactively surface the most relevant memory BODIES for the current user
    message, fenced and framed as authoritative — so the model actually USES what
    it knows instead of ignoring a passive index or re-asking/disk-scanning.

    PRECISION over recall here: this fires every turn, so a loose match would bloat
    context with irrelevant facts. A fact is only injected if it matches >= 2
    distinct query words, OR matches ALL the meaningful query words (so short asks
    like "my GPU" still hit), AND clears a small score floor. Returns '' otherwise
    — adding zero tokens on unrelated turns."""
    query = (query or "").strip()
    if not query:
        return ""
    picked: List[str] = []
    used = 0
    for score, matched, total, fn, fm, body in _score_memories(query):
        if not (matched >= 2 or matched == total) or score < 2.0:
            continue
        title = fm.get("name", fn[:-3])
        snippet = " ".join(body.split())
        chunk = f"- {title}: {snippet}"
        if len(chunk) > max_chars:
            chunk = chunk[:max_chars] + " …"
        if picked and used + len(chunk) > max_chars:
            break
        picked.append(chunk)
        used += len(chunk)
        if len(picked) >= limit:
            break
    if not picked:
        return ""
    return ("<memory>\n"
            "Facts you saved earlier about this user and their machine. Treat them "
            "as authoritative — use them instead of asking again or scanning the disk.\n"
            + "\n".join(picked)
            + "\n</memory>")


# ---------------------------------------------------------------------------
# rules.md — Void's pattern. Plain text, user-editable, prepended fresh each
# turn. Lets you tweak Jarvis behavior WITHOUT touching code.
# ---------------------------------------------------------------------------

DEFAULT_RULES = """\
# Jarvis house rules

Edit this file freely — Jarvis re-reads it every turn. Add lines like:

- always answer in English
- never run pip without asking first
- when I say "the project" I mean ~/Documents/MyApp
- prefer ripgrep over grep
"""


def ensure_rules_exist() -> None:
    if not os.path.exists(RULES_PATH):
        with open(RULES_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_RULES)


def read_rules() -> str:
    ensure_rules_exist()
    try:
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
