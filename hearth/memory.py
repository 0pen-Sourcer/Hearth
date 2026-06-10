"""Long-term memory for Jarvis.

Storage layout:
  ~/Jarvis/memory/MEMORY.md       — always-loaded index. One line per fact.
  ~/Jarvis/memory/<slug>.md       — per-fact file. YAML frontmatter + body.

Why split across files? The index is cheap to inject into the system prompt
every turn (gives the model awareness of what it knows). Per-fact bodies are
loaded only when the model calls memory_recall — so we don't burn tokens on
facts irrelevant to the current question.

Four types:
  user       — who the user is, role, expertise, preferences
  feedback   — corrections / confirmations about how to behave
  project    — ongoing context: what they're building, deadlines, decisions
  reference  — pointers to external places: links, paths, tool URLs

Hard caps:
  index      — 200 lines / 25KB
  per-entry  — model-driven; aim for tight bodies (a paragraph or two)
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .tools import WORKSPACE

MEM_DIR = os.path.join(WORKSPACE, "memory")
# Cold-storage for evicted facts. Files keep their frontmatter and can be
# read or warmed back into MEM_DIR by recall_count crossing a threshold.
# Drop the underscore prefix if you want the GUI graph to walk it.
_ARCHIVE_DIR = os.path.join(MEM_DIR, "_archive")
# Per-(type, sub_category) char budget before eviction triggers. Picked so
# typical clusters (~25 facts of 200-300 chars each) don't churn; only the
# bloated buckets get pruned. Tuneable via JARVIS_MEM_SUBCAT_CAP env var.
_SUBCAT_CHAR_CAP = int(os.environ.get("JARVIS_MEM_SUBCAT_CAP", "6000"))
# When an archived fact gets recalled this many times via sibling pull, it
# promotes back into the hot store. Three hits = "user keeps coming back
# to this topic, stop hiding it".
_PROMOTE_THRESHOLD = 3
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
         tags: Optional[List[str]] = None,
         sub_category: Optional[str] = None) -> str:
    """Write a memory file + add/update the index entry.

    The optional sub_category groups facts within a type for the v0.6 memory
    graph view. If omitted, a regex classifier picks one based on the
    description+body text (Hermes Holographic pattern, zero LLM cost). The
    sub_category goes BOTH in the frontmatter (for the graph viz) AND as a
    `cat:<name>` tag (so recall_for_prompt's existing keyword scorer can
    surface it without schema changes)."""
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

    # Auto-classify the sub-category if not given. The classifier scans
    # description + body for known patterns (hardware/media/routine/etc).
    # Falls back to a type-specific default so every memory belongs to
    # SOMETHING — the tree view never has orphans.
    if not sub_category:
        try:
            from .memory_classify import classify_or_default
            sub_category = classify_or_default(
                mtype,
                (description or "") + " " + (body or ""),
                tags=tags,
            )
        except Exception:
            sub_category = None
    if sub_category:
        # Mirror into tags as `cat:<name>` so memory_recall's keyword
        # scorer can find it without us changing its signature.
        cat_tag = f"cat:{sub_category}"
        if cat_tag not in tags:
            tags = list(tags) + [cat_tag]

    front = ["---", f"name: {title}", f"type: {mtype}",
             f"description: {description.strip()}"]
    if sub_category:
        front.append(f"sub_category: {sub_category}")
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
    # Eviction pass: if the (type, sub_category) bucket got too big,
    # archive the coldest facts. Coldness combines recall_count with
    # last_recalled_at. Archived facts stay readable + warmable via
    # sibling pull, but stop bloating the hot list / system prompt.
    if sub_category:
        try:
            archived = _evict_if_over_cap(mtype, sub_category, protect_slug=slug)
            if archived:
                msg += f"  [archived {len(archived)} cold: {', '.join(archived[:3])}{'...' if len(archived) > 3 else ''}]"
        except Exception:
            pass
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


def index_for_prompt(max_lines: int = 20, max_line_chars: int = 120) -> str:
    """Compact form to inject into the system prompt every turn.

    The model should see WHAT IT KNOWS without us loading every body. If a
    fact looks relevant, it can call memory_recall to load the body.

    Capped at max_lines so the index doesn't grow without bound as the user
    accumulates facts (the auto-extraction hook adds facts every turn). When
    over the cap, show recent items + 'and N more — call memory_recall to
    find others'. memory_recall keyword-scores against ALL facts regardless
    of what's shown here, so capping the prompt-injection is safe."""
    lines = _read_index_lines()
    if not lines:
        return ""
    # Trim each line so a verbose memory entry doesn't eat 300 tokens alone
    trimmed = [(l[:max_line_chars] + "…") if len(l) > max_line_chars else l
               for l in lines]
    if len(trimmed) <= max_lines:
        body = "\n".join(trimmed)
    else:
        # Show the most recent N (index lines are appended chronologically;
        # most recent is at the END — show those + summary).
        kept = trimmed[-max_lines:]
        body = (
            "\n".join(kept)
            + f"\n…and {len(trimmed) - max_lines} older memories — "
              f"call memory_recall('<topic>') to find anything not shown here."
        )
    return "[ Memories on file (call memory_recall to load any) ]\n" + body


def _rewrite_frontmatter(path: str, fm: Dict[str, str], body: str) -> None:
    """Persist a memory file with updated frontmatter. Preserves field order
    when possible: standard keys first, then anything else. Used by the
    recall-count bumper; cheap to call per-pick because most chats touch
    only 1-2 memories per turn."""
    order = ["name", "type", "description", "sub_category", "tags",
             "recall_count", "last_recalled_at", "updated"]
    lines = ["---"]
    for k in order:
        if k in fm:
            lines.append(f"{k}: {fm[k]}")
    for k, v in fm.items():
        if k not in order:
            lines.append(f"{k}: {v}")
    lines.append("---")
    text = "\n".join(lines) + "\n\n" + body.lstrip("\n").rstrip() + "\n"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def _bump_recall(path: str) -> None:
    """Increment recall_count + stamp last_recalled_at on a memory file.
    Idempotent; quietly no-ops if the file is unreadable. Promotes the file
    back from archive if it crosses _PROMOTE_THRESHOLD hits there."""
    try:
        fm, body = _read_body(path)
    except OSError:
        return
    try:
        n = int(fm.get("recall_count", "0") or "0")
    except ValueError:
        n = 0
    fm["recall_count"] = str(n + 1)
    fm["last_recalled_at"] = datetime.now().isoformat(timespec="seconds")
    _rewrite_frontmatter(path, fm, body)
    # Promote out of archive once a topic gets pulled enough times that
    # hiding it is clearly wrong.
    parent = os.path.dirname(path)
    if (os.path.basename(parent) == "_archive"
            and (n + 1) >= _PROMOTE_THRESHOLD):
        _promote_from_archive(path)


def _promote_from_archive(archive_path: str) -> None:
    """Move a fact back from _archive/ to MEM_DIR. Also re-indexes it in
    MEMORY.md so the GUI list view + sibling scan can see it again."""
    fn = os.path.basename(archive_path)
    new_path = os.path.join(MEM_DIR, fn)
    try:
        if not os.path.exists(new_path):
            os.rename(archive_path, new_path)
        else:
            # collision (same slug exists in hot store): drop the archive
            # copy rather than overwriting newer state
            os.remove(archive_path)
            return
    except OSError:
        return
    try:
        fm, _body = _read_body(new_path)
    except OSError:
        return
    slug = fn[:-3] if fn.endswith(".md") else fn
    title = fm.get("name", slug)
    mtype = fm.get("type", "user")
    desc = fm.get("description", "")
    lines = _read_index_lines()
    if _index_entry_for_slug(lines, slug) is None:
        lines.append(_index_entry(slug, title, mtype, desc))
        _write_index_lines(lines)


def _bucket_files(mtype: str, sub_category: str,
                  base: str = MEM_DIR) -> List[Tuple[str, Dict[str, str], str, int]]:
    """List all files in a (type, sub_category) bucket with their parsed
    frontmatter + body + current size in chars. Used by the eviction pass
    to know what's in scope + how big the bucket is."""
    out = []
    if not os.path.isdir(base):
        return out
    for fn in os.listdir(base):
        if not fn.endswith(".md") or fn == "MEMORY.md":
            continue
        full = os.path.join(base, fn)
        if not os.path.isfile(full):
            continue
        try:
            fm, body = _read_body(full)
        except OSError:
            continue
        if fm.get("type") != mtype:
            continue
        if (fm.get("sub_category") or "") != sub_category:
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = len(body) + sum(len(k) + len(v) for k, v in fm.items())
        out.append((fn, fm, body, size))
    return out


def _coldness(fm: Dict[str, str]) -> float:
    """Higher = better eviction candidate. Combines recency-of-recall and
    how often it's been recalled — a fact recalled 10 times last week is
    warmer than one created yesterday and never touched."""
    try:
        n = int(fm.get("recall_count", "0") or "0")
    except ValueError:
        n = 0
    when = fm.get("last_recalled_at") or fm.get("updated") or ""
    try:
        last = datetime.fromisoformat(when)
    except (ValueError, TypeError):
        return 1e6  # malformed → maximally cold so it sheds first
    days = (datetime.now() - last).total_seconds() / 86400.0
    return days / (n + 1)


def _evict_if_over_cap(mtype: str, sub_category: str,
                       protect_slug: str = "") -> List[str]:
    """If the bucket exceeds _SUBCAT_CHAR_CAP, archive the coldest facts
    until under cap. The just-saved fact (protect_slug) is never evicted
    even if the bucket can't fit otherwise — saving a fact bigger than
    the cap shouldn't make it instantly vanish. Returns the slug list
    that got archived."""
    if not sub_category:
        return []
    bucket = _bucket_files(mtype, sub_category)
    total = sum(sz for _fn, _fm, _b, sz in bucket)
    if total <= _SUBCAT_CHAR_CAP or len(bucket) <= 1:
        return []
    os.makedirs(_ARCHIVE_DIR, exist_ok=True)
    # Coldest first; protect_slug is forcibly last so it's never touched.
    protect_fn = (protect_slug + ".md") if protect_slug else ""
    bucket.sort(key=lambda r: (r[0] == protect_fn, -_coldness(r[1])))
    archived: List[str] = []
    for fn, _fm, _body, sz in bucket:
        if total <= _SUBCAT_CHAR_CAP:
            break
        if fn == protect_fn:
            break  # would be the last item; bail to avoid evicting it
        src = os.path.join(MEM_DIR, fn)
        dst = os.path.join(_ARCHIVE_DIR, fn)
        try:
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(src, dst)
            archived.append(fn[:-3] if fn.endswith(".md") else fn)
            total -= sz
        except OSError:
            continue
    # Drop the index entries for archived files so the public list view
    # stays clean; readers can still walk _archive/ manually.
    if archived:
        lines = _read_index_lines()
        keep = []
        archive_set = set(archived)
        for ln in lines:
            m = re.match(r"^- \[[^\]]+\]\(([^)]+)\.md\)", ln)
            slug = m.group(1) if m else None
            if slug and slug in archive_set:
                continue
            keep.append(ln)
        _write_index_lines(keep)
    return archived


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


def _score_memories(query: str) -> List[Tuple[float, int, int, str, Dict[str, str], str, str]]:
    """Score every memory against the query. Returns
    [(score, matched_terms, total_terms, fn, fm, body, root_dir)] sorted best-first.
    Score = keyword hits (filename hits weighted 3x) scaled by how MANY distinct
    query terms a fact matches (breadth beats repetition); recency is the tiebreak.
    Stopwords are dropped so common words don't create spurious matches.
    Walks the archive too when nothing matches in the hot store; archived hits
    get a 0.6x score multiplier so a hot fact always wins a tie. root_dir is
    MEM_DIR or _ARCHIVE_DIR — callers that bump recall counts use it to find
    the file."""
    terms = [t for t in (w.lower() for w in re.findall(r"\w+", query))
             if len(t) >= 2 and t not in _STOPWORDS]
    if not terms:
        return []
    total = len(terms)

    def _scan(root: str, archived: bool):
        results = []
        try:
            files = os.listdir(root)
        except OSError:
            return results
        for fn in files:
            if not fn.endswith(".md") or fn == "MEMORY.md":
                continue
            full = os.path.join(root, fn)
            if not os.path.isfile(full):
                continue
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
            coverage = len(matched) / total
            # Archive facts score the same on relevance but get demoted via
            # a 0.6x multiplier so a hot fact always wins a tie. They only
            # surface as primary hits when nothing in the hot store matches.
            score = raw * (0.5 + coverage) * (0.6 if archived else 1.0)
            # Track the root so recall_for_prompt's _bump_recall knows
            # whether to look in MEM_DIR or _ARCHIVE_DIR for the file.
            results.append((score, fm.get("updated", ""), len(matched),
                            fn, fm, body, root))
        return results

    scored = _scan(MEM_DIR, archived=False)
    if not scored and os.path.isdir(_ARCHIVE_DIR):
        # Hot store has nothing — fall through to archive so cold facts
        # can still answer direct questions. The recall_count bump in
        # recall_for_prompt may end up promoting them back if the user
        # keeps asking about this topic.
        scored = _scan(_ARCHIVE_DIR, archived=True)
    scored.sort(key=lambda c: (c[0], c[1]), reverse=True)  # score, then recency
    # The 7th tuple element (root path) is kept on the result so callers
    # that bump recall counts use the correct file location. Callers that
    # don't care can ignore it via unpacking with a trailing _.
    return [(s, m, total, fn, fm, body, root)
            for (s, _u, m, fn, fm, body, root) in scored]


def _siblings_in_sub_category(sub_category: str, mtype: str,
                              exclude_filenames: set,
                              limit: int = 4,
                              include_archived: bool = True
                              ) -> List[Tuple[str, Dict[str, str], str, bool]]:
    """Find other memories in the same sub_category + type. Sibling-on-recall
    pattern — asking "what's my GPU?" also surfaces RAM + drive facts
    because they're in the same cluster.

    Walks both the hot store (MEM_DIR) and the archive (_archive/) when
    include_archived. Archived facts get marked so the renderer can flag
    them. When a sibling is pulled, the caller is expected to _bump_recall
    on it — if an archived fact crosses _PROMOTE_THRESHOLD, it moves back
    to hot automatically.

    Returns [(fn, fm, body, archived), ...] sorted hot-first then by recency.
    """
    if not sub_category:
        return []
    out: List[Tuple[str, Dict[str, str], str, bool]] = []
    roots: List[Tuple[str, bool]] = [(MEM_DIR, False)]
    if include_archived and os.path.isdir(_ARCHIVE_DIR):
        roots.append((_ARCHIVE_DIR, True))
    for root, archived in roots:
        try:
            files = os.listdir(root)
        except OSError:
            continue
        for fn in files:
            if not fn.endswith(".md") or fn == "MEMORY.md" or fn in exclude_filenames:
                continue
            full = os.path.join(root, fn)
            if not os.path.isfile(full):
                continue
            try:
                fm, body = _read_body(full)
            except OSError:
                continue
            if fm.get("sub_category") != sub_category:
                continue
            if mtype and fm.get("type") != mtype:
                continue
            out.append((fn, fm, body, archived))
    # Hot-first; within each tier, newest first.
    out.sort(key=lambda c: (not c[3], c[1].get("updated", "")), reverse=False)
    out.sort(key=lambda c: (c[3], -ord("9")), reverse=False)
    # The above keeps hot (False) before archived (True), with hot ordered
    # by recency desc when we re-walk after sort. Simpler: do it in two
    # passes.
    hot = [t for t in out if not t[3]]
    cold = [t for t in out if t[3]]
    hot.sort(key=lambda c: c[1].get("updated", ""), reverse=True)
    cold.sort(key=lambda c: c[1].get("updated", ""), reverse=True)
    return (hot + cold)[:limit]


def recall(query: str, limit: int = 5, with_siblings: bool = True) -> str:
    """Keyword search across the memory store. Returns top N matches with bodies.

    with_siblings=True (default) ALSO pulls a few sibling facts from each
    top match's sub_category. Asking "what's my GPU?" surfaces the GPU
    fact AND the RAM + drive facts because they're in the same cluster.
    Set False for a pure keyword match (back-compat)."""
    query = (query or "").strip()
    if not query:
        return list_index()
    candidates = _score_memories(query)[:limit]
    if not candidates:
        return f"(no memories match '{query}')"
    out: List[str] = []
    seen_files = set()
    sibling_blocks: List[Tuple[str, str, list]] = []  # (sub, type, [(name, body)])
    for score, _matched, _total, fn, fm, body, root in candidates:
        seen_files.add(fn)
        title = fm.get("name", fn[:-3])
        mtype = fm.get("type", "?")
        updated = fm.get("updated", "")
        cold = (root == _ARCHIVE_DIR)
        cold_tag = " (cold)" if cold else ""
        out.append(f"## {title}{cold_tag}  ({mtype}, score={score:.0f}, {updated})")
        out.append(body.strip()[:1500])
        out.append("")
        # Sibling pull — adjacent facts in the same sub_category. Archived
        # facts are included with a (cold) marker so the model can see them
        # while still treating hot facts as primary.
        if with_siblings:
            sub = fm.get("sub_category", "")
            if sub:
                sibs = _siblings_in_sub_category(sub, mtype, seen_files, limit=3)
                for sfn, sfm, sbody, archived in sibs:
                    seen_files.add(sfn)
                    # Bump recall on the sibling too — surfacing it counts
                    # as usage, and archived siblings may promote back to
                    # hot after enough hits.
                    parent = _ARCHIVE_DIR if archived else MEM_DIR
                    _bump_recall(os.path.join(parent, sfn))
                    sibling_blocks.append((sub, mtype,
                        [(sfm.get("name", sfn[:-3]), sbody, archived)]))
    # Render sibling block AFTER the direct matches, fenced as "related facts"
    if sibling_blocks:
        out.append("---")
        out.append("**Related facts** (same cluster):")
        seen_sub: set = set()
        for sub, mtype, leaves in sibling_blocks:
            key = (sub, mtype)
            if key in seen_sub:
                continue
            seen_sub.add(key)
            out.append(f"\n### {sub} ({mtype})")
            for name, body, archived in leaves:
                first = body.strip().split("\n", 1)[0][:200]
                marker = " *(cold)*" if archived else ""
                out.append(f"- **{name}**{marker}: {first}")
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
    picked_paths: List[str] = []
    used = 0
    for score, matched, total, fn, fm, body, root in _score_memories(query):
        if not (matched >= 2 or matched == total) or score < 2.0:
            continue
        title = fm.get("name", fn[:-3])
        snippet = " ".join(body.split())
        chunk = f"- {title}: {snippet}"
        if len(chunk) > max_chars:
            chunk = chunk[:max_chars] + " ..."
        if picked and used + len(chunk) > max_chars:
            break
        picked.append(chunk)
        picked_paths.append(os.path.join(root, fn))
        used += len(chunk)
        if len(picked) >= limit:
            break
    # Bump recall counters AFTER selection so a fact pulled into the
    # system prompt counts toward warming. Pass the full path so the
    # archive fallback bumps the right file (and can promote on threshold).
    for p in picked_paths:
        _bump_recall(p)
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
