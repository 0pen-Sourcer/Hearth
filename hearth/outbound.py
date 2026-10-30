"""What a turn has taken in from outside, and which requests could carry data
out because of it.

A web page, a downloaded file, an email or another program's result can tell
the brain to read something private and send it somewhere, and a plain GET
with the secret in its address is enough to send it. So once a chat has taken
in content from outside, a request to an address nobody gave asks first, in
every approval mode. An address is given when it appears whole in what the
person said or in what came in (a search result, a link on a page read): a
copy carries nothing new. One built with something appended, or to a host
nobody mentioned, is not.

The record lives on the turn (auto_review's turn state, which reaches tool
threads and the agent brains' permission hooks) and is rebuilt from the chat's
history at the start of each turn, so it holds for the whole chat.
"""
from __future__ import annotations

import html
import os
import re
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional

# Tools whose results are content from outside: the web, mail, the screen.
_OUTSIDE = {
    "web_search": "search results",
    "web_fetch": "a web page",
    "browse": "a web page", "browse_click": "a web page", "browse_scroll": "a web page",
    "browse_type": "a web page", "browse_key": "a web page",
    "read_inbox": "an email",
    "screenshot": "the screen", "capture_active_window": "the screen",
    "desktop_snapshot": "the screen", "computer_screen": "the screen",
}
# Readers of a file: outside content when the file came from the internet.
_FILE_READERS = {"read_file", "read_pdf_large", "summarize_file", "list_archive",
                 "extract_archive_file", "view_image"}
# Native tools that make a request to the address in these arguments.
URL_ARGS: Dict[str, tuple] = {
    "web_fetch": ("url",), "browse": ("url",), "open_url": ("url",),
    "open_in_browser": ("url",), "generate_video": ("image_url",),
}
# Reading the web: free in every mode unless the request would carry data out.
READS_WEB = {"web_fetch", "browse"}
_SHELL = {"run_command", "start_job"}
MCP_PREFIX = "mcp_"

_URL = re.compile(r"""https?://[^\s'"`<>()\[\]{}]+""", re.I)
_THIS_PC = re.compile(r"^(https?://)?(localhost|127\.\d+\.\d+\.\d+|\[::1\])(:\d+)?(/|$)", re.I)


def tool_name(name: str) -> str:
    """An agent brain's name for one of Inglen's tools, as Inglen names it."""
    n = str(name or "")
    for pre in ("mcp__hearth__", "hearth__", "hearth."):
        if n.startswith(pre):
            return n[len(pre):]
    return n


class Seen:
    """What one chat has taken in from outside, and the text addresses may be
    copied from (the person's words and what came in)."""

    def __init__(self) -> None:
        self.outside: List[str] = []
        self.given: List[str] = []

    def gave(self, text: Any) -> None:
        t = _flat(text)
        if t:
            self.given.append(t[-20000:])

    def took_in(self, what: str, text: Any = "") -> None:
        if what and what not in self.outside:
            self.outside.append(what)
        self.gave(text)

    def is_given(self, url: str) -> bool:
        n = _norm(url)
        return not n or any(n in _norm_text(t) for t in self.given)

    def source(self) -> str:
        if not self.outside:
            return ""
        if len(self.outside) == 1:
            return self.outside[0]
        return ", ".join(self.outside[:-1]) + " and " + self.outside[-1]


def _flat(text: Any) -> str:
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        return "\n".join(_flat(x.get("text") if isinstance(x, dict) else x) for x in text)
    return "" if text is None else str(text)


def _norm(url: str) -> str:
    u = html.unescape(str(url or "")).strip().strip(".,;:!?'\")]}>")
    u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    return u.split("#", 1)[0].rstrip("/").lower()


def _norm_text(text: str) -> str:
    t = re.sub(r"\b[a-z][a-z0-9+.-]*://(www\.)?", "", html.unescape(text), flags=re.I)
    return re.sub(r"(^|[\s'\"(<\[])www\.", r"\1", t, flags=re.I).lower()


def _host(url: str) -> str:
    u = url if "://" in url else "https://" + url
    try:
        return urllib.parse.urlsplit(u).hostname or url
    except ValueError:
        return url


def _short(url: str, n: int = 160) -> str:
    return url if len(url) <= n else url[:n] + "..."


def from_internet(path: str) -> bool:
    """Whether a file carries the mark Windows puts on downloads (Zone.Identifier)."""
    if not path or os.name != "nt":
        return False
    try:
        with open(os.path.expanduser(str(path)) + ":Zone.Identifier", "rb"):
            return True
    except OSError:
        return False


def note_on(seen: Optional[Seen], name: str, args: Any, result: Any) -> None:
    """Record what a tool that just ran brought in."""
    if seen is None:
        return
    name = tool_name(name)
    a = args if isinstance(args, dict) else {}
    if name in _OUTSIDE:
        where = ""
        url = str(a.get("url") or "")
        if url:
            where = f" ({_host(url)})"
        seen.took_in(_OUTSIDE[name] + where, result)
    elif name.startswith(MCP_PREFIX):
        seen.took_in(f"a result from {name}", result)
    elif name in _FILE_READERS:
        path = str(a.get("path") or a.get("file") or a.get("archive") or "")
        if from_internet(path):
            seen.took_in(f"a downloaded file ({os.path.basename(path)})", result)
    elif name in _SHELL:
        cmd = _flat(a.get("command") or a.get("cmd") or "")
        far = [u for u in _URL.findall(cmd) if not _THIS_PC.match(u)]
        if far:
            seen.took_in(f"a download ({_host(far[0])})", result)


def from_history(history: Optional[Iterable[Dict[str, Any]]], prompt: str = "") -> Seen:
    """What the chat so far has taken in, from its messages: tool results as
    the window and the bridges keep them, and the person's own words."""
    seen = Seen()
    calls: Dict[str, Any] = {}
    for m in history or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        for ev in m.get("events") or []:
            if isinstance(ev, dict) and ev.get("kind") == "tool":
                note_on(seen, str(ev.get("name") or ""), ev.get("args"), ev.get("result"))
        if role == "user":
            text = _flat(m.get("content"))
            if "<task-notification" in text:
                seen.took_in("a helper's report", text)   # written by a model, not the person
            else:
                seen.gave(text)
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = (tc or {}).get("function") or {}
                calls[str(tc.get("id") or "")] = fn.get("arguments")
        elif role == "tool":
            raw = calls.get(str(m.get("tool_call_id") or ""))
            try:
                import json
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except ValueError:
                args = {}
            note_on(seen, str(m.get("name") or ""), args, m.get("content"))
    seen.gave(prompt)
    return seen


def current() -> Optional[Seen]:
    try:
        from .auto_review import current as _turn
        t = _turn()
    except Exception:
        return None
    return getattr(t, "seen", None) if t is not None else None


def note(name: str, args: Any, result: Any) -> None:
    note_on(current(), name, args, result)


def _urls(name: str, args: Any) -> List[str]:
    a = args if isinstance(args, dict) else {}
    if name in URL_ARGS:
        return [str(a.get(k) or "").strip() for k in URL_ARGS[name] if str(a.get(k) or "").strip()]
    if name in _SHELL:
        return _URL.findall(_flat(a.get("command") or a.get("cmd") or ""))
    return []


def reason(name: str, args: Any = None) -> str:
    """Why this call asks first even in Auto: it would send something out after
    the chat took in content from outside. '' when it doesn't."""
    seen = current()
    if seen is None or not seen.outside:
        return ""
    name = tool_name(name)
    src = seen.source()
    if name.startswith(MCP_PREFIX):
        return (f"This chat has read {src}. {name} hands what it is given to another "
                f"program, which can send it anywhere.")
    for url in _urls(name, args):
        if _THIS_PC.match(url) or seen.is_given(url):
            continue
        return (f"This would send a request to {_host(url)}: {_short(url)}. Nobody gave that address, "
                f"and this chat has read {src}, which could have asked for it.")
    return ""
