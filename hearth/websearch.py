"""Web search backends, asked together, with the answers merged.

A single vendor was never enough: DuckDuckGo serves a bot check when it likes,
the keyless MCP endpoints rate-limit or (on some networks) cut the reply off
mid-stream and hang. So every backend is asked at once with a short deadline,
the first answer is taken and anything else that arrives a moment later is
merged in, duplicates dropped. A backend that failed is left out for a while,
so a dead one costs nothing on the next search. Wikipedia is the last resort.

Nothing here logs a query anywhere but the backends being asked.
"""
from __future__ import annotations

import html as _html
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Callable, Dict, List, Optional, Tuple

from . import env as _env

_UA = "Inglen/0.7.6"
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 10            # one backend's own limit
DEADLINE = 12.0          # the whole search, all backends together
GRACE = 1.5              # after the first answer, how long the others may still add theirs
REST_S = 600             # a backend that failed sits out this long

Row = Dict[str, str]     # {"title", "url", "snippet"}


def _settings() -> dict:
    try:
        base = _env.get("WORKSPACE") or __import__("hearth").default_workspace()
        with open(os.path.join(base, "settings.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _read(r) -> bytes:
    try:
        return r.read()
    except http.client.IncompleteRead as e:     # a stream cut off part way: keep what came
        return e.partial


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ---------------------------------------------------------------- backends

def _mcp_call(url: str, tool: str, args: dict) -> Tuple[str, bool]:
    """One MCP tools/call over plain HTTP. Returns (text, rate_limited).
    These endpoints answer as JSON or as one server-sent event."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        raw = _read(r).decode("utf-8", "replace")
    if not raw.strip():
        raise _Refused("the reply was cut off")      # some networks drop these streams
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {}
    result = data.get("result") or {}
    limited = bool((result.get("_meta") or {}).get("ai.exa/rateLimited"))
    text = "".join(c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text")
    if result.get("isError"):
        return "", limited
    return text.strip(), limited


def _p_parallel(query: str, limit: int) -> List[Row]:
    text, limited = _mcp_call("https://search.parallel.ai/mcp", "web_search",
                              {"objective": query, "search_queries": [query]})
    if limited or not text:
        return []
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    return [{"title": _clean(row.get("title") or ""), "url": (row.get("url") or "").strip(),
             "snippet": _clean(" ".join(str(x) for x in (row.get("excerpts") or [])[:2]))}
            for row in (doc.get("results") or [])[:limit] if row.get("url")]


def parse_exa(text: str) -> List[Row]:
    """Exa answers in blocks: Title:, URL:, then highlights or text until the next Title:."""
    rows = []
    for block in re.split(r"(?m)^(?=Title: )", text or ""):
        url = re.search(r"(?m)^URL:\s*(\S+)", block)
        if not url:
            continue
        title = re.search(r"(?m)^Title:\s*(.*)$", block)
        body = re.split(r"(?m)^(?:Highlights|Text|Summary):\s*$", block, maxsplit=1)
        body = body[1] if len(body) > 1 else re.sub(r"(?m)^(Title|URL|Published|Author):.*$", "", block)
        rows.append({"title": (title.group(1).strip() if title else ""), "url": url.group(1).strip(),
                     "snippet": re.sub(r"#+\s*", "", _clean(body))})
    return rows


def _p_exa(query: str, limit: int) -> List[Row]:
    text, limited = _mcp_call("https://mcp.exa.ai/mcp", "web_search_exa", {"query": query, "numResults": limit})
    return [] if limited else parse_exa(text)[:limit]


def parse_ddg(page: str) -> List[Row]:
    """DuckDuckGo's HTML page: each result a result__a link and a result__snippet."""
    rows = []
    for m in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
                         r'(?:.*?<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>)?', page, re.S):
        rows.append({"title": _clean(m.group(2)), "url": _real_url(m.group(1)), "snippet": _clean(m.group(3) or "")})
    return rows


def parse_ddg_lite(page: str) -> List[Row]:
    """DuckDuckGo's lite page: result-link anchors, each followed by a result-snippet cell."""
    links = list(re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*class=[\"']result-link[\"'][^>]*>(.*?)</a>", page, re.S))
    snips = re.findall(r"class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", page, re.S)
    return [{"title": _clean(m.group(2)), "url": _real_url(m.group(1)), "snippet": _clean(snips[i] if i < len(snips) else "")}
            for i, m in enumerate(links)]


def _real_url(href: str) -> str:
    """DuckDuckGo wraps the address in /l/?uddg=; take the real one out."""
    href = _html.unescape(href)
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        if q:
            return q[0]
    except Exception:
        pass
    return "https:" + href if href.startswith("//") else href


def _blocked(page: str) -> bool:
    return "anomaly" in page.lower() or "bots use duckduckgo" in page.lower()


def _p_ddg(query: str, limit: int) -> List[Row]:
    req = urllib.request.Request("https://html.duckduckgo.com/html/",
                                 data=urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode(), headers={
        "User-Agent": _BROWSER_UA, "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html", "Referer": "https://duckduckgo.com/"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        page = _read(r).decode("utf-8", "replace")
    if _blocked(page):
        raise _Refused("DuckDuckGo showed its bot check")
    return parse_ddg(page)[:limit]


def _p_ddg_lite(query: str, limit: int) -> List[Row]:
    req = urllib.request.Request("https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query}),
                                 headers={"User-Agent": _BROWSER_UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        page = _read(r).decode("utf-8", "replace")
    if _blocked(page):
        raise _Refused("DuckDuckGo Lite showed its bot check")
    return parse_ddg_lite(page)[:limit]


def _p_wikipedia(query: str, limit: int) -> List[Row]:
    q = urllib.parse.urlencode({"action": "opensearch", "search": query, "limit": str(limit),
                                "namespace": "0", "format": "json"})
    req = urllib.request.Request("https://en.wikipedia.org/w/api.php?" + q,
                                 headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        data = json.loads(_read(r).decode("utf-8"))
    return [{"title": t, "url": u, "snippet": d} for t, d, u in zip(data[1], data[2], data[3])]


class _Refused(Exception):
    pass


# Name -> (how to ask it, label). Wikipedia only answers once the rest came back empty.
BACKENDS: Dict[str, Tuple[Callable[[str, int], List[Row]], str]] = {
    "exa": (_p_exa, "Exa"),
    "parallel": (_p_parallel, "Parallel"),
    "duckduckgo": (_p_ddg, "DuckDuckGo"),
    "duckduckgo_lite": (_p_ddg_lite, "DuckDuckGo Lite"),
}
LAST_RESORT = ("wikipedia", _p_wikipedia, "Wikipedia titles only")

_HEALTH: Dict[str, Dict[str, float]] = {}      # name -> {"until": t, "why": ...}
_HEALTH_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="websearch")


def _resting(name: str, now: float) -> bool:
    with _HEALTH_LOCK:
        return _HEALTH.get(name, {}).get("until", 0) > now


def _mark(name: str, ok: bool, why: str = "") -> None:
    with _HEALTH_LOCK:
        if ok:
            _HEALTH.pop(name, None)
        else:
            _HEALTH[name] = {"until": time.time() + REST_S, "why": why}


def _why(e: BaseException) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return {429: "rate limited", 403: "refused"}.get(e.code, f"HTTP {e.code}")
    if isinstance(e, (TimeoutError,)) or "timed out" in str(e):
        return "timed out"
    if isinstance(e, _Refused):
        return str(e)
    if isinstance(e, http.client.IncompleteRead):
        return "the reply was cut off"
    return f"{type(e).__name__}"


def merge(lists: List[List[Row]], limit: int) -> List[Row]:
    """Interleave the lists, best of each first, one row per address."""
    seen, out = set(), []
    for i in range(max((len(x) for x in lists), default=0)):
        for rows in lists:
            if i < len(rows):
                key = re.sub(r"^https?://(www\.)?|/$", "", rows[i]["url"].lower())
                if key and key not in seen:
                    seen.add(key)
                    out.append(rows[i])
    return out[:limit]


def render(rows: List[Row], sources: List[str]) -> str:
    lines = [f"{len(rows)} result{'s' if len(rows) != 1 else ''} (from {', '.join(sources)}):"]
    for r in rows:
        snip = r["snippet"][:360] + ("…" if len(r["snippet"]) > 360 else "")
        lines.append(f"- {r['title'] or r['url']}\n  {r['url']}" + (f"\n  {snip}" if snip else ""))
    return "\n\n".join(lines)


def search(query: str, limit: int = 6) -> Tuple[str, str]:
    """(text, how). text is the results, or '' when nothing answered; how names the
    backends that answered, or says why each one didn't."""
    query = (query or "").strip()
    if not query:
        return "", ""
    limit = max(1, min(int(limit or 6), 15))
    now = time.time()
    preferred = str(_settings().get("web_search_provider") or "").strip().lower()
    names = sorted(BACKENDS, key=lambda n: n != preferred)
    live = [n for n in names if not _resting(n, now)] or names       # all resting: try them anyway
    futures = {_POOL.submit(BACKENDS[n][0], query, limit): n for n in live}
    got: Dict[str, List[Row]] = {}
    failed: Dict[str, str] = {}
    end = now + DEADLINE
    pending = set(futures)
    while pending and time.time() < end:
        done, pending = wait(pending, timeout=max(0.0, end - time.time()), return_when=FIRST_COMPLETED)
        for f in done:
            n = futures[f]
            try:
                rows = [r for r in f.result() if r.get("url", "").startswith("http")]
            except Exception as e:
                failed[n] = _why(e)
                _mark(n, False, failed[n])
                continue
            if rows:
                got[n] = rows
                _mark(n, True)
            else:
                failed[n] = "no results"
        if got:
            end = min(end, time.time() + GRACE)
    for f in pending:
        failed.setdefault(futures[f], "too slow")
    if got:
        order = [n for n in names if n in got]
        return render(merge([got[n] for n in order], limit), [BACKENDS[n][1] for n in order]), ", ".join(order)
    name, fn, label = LAST_RESORT
    try:
        rows = fn(query, limit)
        if rows:
            return render(rows, [label]), name
    except Exception as e:
        failed[name] = _why(e)
    why = "; ".join(f"{BACKENDS[n][1] if n in BACKENDS else n}: {w}" for n, w in failed.items())
    return "", why


def provider_status() -> List[dict]:
    """Each backend's state now: answering, or resting after a failure and why."""
    now = time.time()
    with _HEALTH_LOCK:
        h = dict(_HEALTH)
    return [{"name": n, "label": BACKENDS[n][1], "ok": h.get(n, {}).get("until", 0) <= now,
             "note": h.get(n, {}).get("why", "") if h.get(n, {}).get("until", 0) > now else ""} for n in BACKENDS]
