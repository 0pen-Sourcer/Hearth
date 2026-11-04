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
# What the web shows anyone. An address made of words from these and the person's own
# carries nothing of theirs.
_PUBLIC = {"web_search", "web_fetch", "browse", "browse_click", "browse_scroll", "browse_type", "browse_key"}
_WORD = re.compile(r"[^\W_]+")
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
# A path on another machine: \\host\share\..., //host/share/... or file://host/...
# Opening one connects to that host (SMB, or WebDAV over https with host@SSL), and
# the path itself, and the Windows sign-in hash, go with it.
_UNC = re.compile(r"""(?:(?<![\w:/\\])(?:\\\\|//)|file://(?://?)?(?=[^/]))([A-Za-z0-9][\w.-]*)(?:@SSL)?(?:@\d+)?"""
                  r"""[\\/][^\s'"`<>|]*""", re.I)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", ".", "?", "wsl$", "wsl.localhost"}
# Native tools that type text where an address bar may have the focus.
_TYPES_TEXT = {"desktop_type": "text", "computer_type": "text", "browse_type": "text"}
# open_app takes a name, a path or an address.
_NAME_ARGS = {"open_app": "name"}
# A host named without a scheme. curl and PowerShell's web cmdlets take one as http, a
# browser or the address bar does the same, and a name that is only looked up (nslookup,
# ping) carries its labels to whoever answers for that domain.
_BARE_HOST = re.compile(r"""(?<![\w@./\\:%$-])((?:[a-z0-9][a-z0-9-]{0,62}\.)+[a-z][a-z0-9-]{1,62}\.?"""
                        r"""(?::\d{1,5})?(?:[/?#][^\s'"`<>()\[\]{}]*)?)""", re.I)
_NET_WORDS = re.compile(r"""(?<![\w.-])(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|start-bitstransfer|"""
                        r"""bitsadmin|certutil|nslookup|ping|resolve-dnsname|test-netconnection|tnc|tracert|"""
                        r"""pathping|dig|ftp|tftp|telnet|ssh|scp|sftp|ncat|nc|start|start-process|msedge|chrome|"""
                        r"""firefox|iexplore|brave|opera|explorer)(?:\.exe)?(?![\w-])""", re.I)


def _bare_hosts(text: str, every: bool) -> List[str]:
    """Hosts named without a scheme in text, as http addresses. With every unset, only those
    that clearly are addresses (a path, a query or a port after the host, or www.)."""
    out = []
    for m in _BARE_HOST.finditer(text or ""):
        tok = m.group(1)
        if every or re.match(r"(?i)www\.", tok) or re.search(r"[/?#]|:\d", tok):
            out.append("http://" + tok)
    return out


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
        self.said: List[str] = []        # the person's own words only
        self.public: List[str] = []      # the person's words and what the web showed
        self.held: List[str] = []        # everything else the chat holds: files, the screen, mail, outputs
        self._words: Optional[tuple] = None

    def person_said(self, text: Any) -> None:
        t = _flat(text)
        if t:
            self.said.append(t[-20000:])
            self.public.append(t[-20000:])
        self.gave(t)

    def gave(self, text: Any) -> None:
        t = _flat(text)
        if t:
            self.given.append(t[-20000:])

    def took_in(self, what: str, text: Any = "", public: bool = False) -> None:
        if what and what not in self.outside:
            self.outside.append(what)
        self.gave(text)
        t = _flat(text)
        if t:
            (self.public if public else self.held).append(t[-20000:])

    def kept(self, text: Any) -> None:
        """A tool's result from this PC (a file, a command's output): not outside, but the chat holds it."""
        t = _flat(text)
        if t:
            self.held.append(t[-20000:])

    def public_words(self) -> set:
        if self._words is None or self._words[0] != len(self.public):
            self._words = (len(self.public), set(_WORD.findall(_norm_text("\n".join(self.public)))))
        return self._words[1]

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
        seen.took_in(_OUTSIDE[name] + where, result, public=name in _PUBLIC)
    elif name.startswith(MCP_PREFIX):
        seen.took_in(f"a result from {name}", result)
    elif name in _FILE_READERS and from_internet(str(a.get("path") or a.get("file") or a.get("archive") or "")):
        path = str(a.get("path") or a.get("file") or a.get("archive") or "")
        seen.took_in(f"a downloaded file ({os.path.basename(path)})", result)
    elif name in _SHELL and [u for u in _URL.findall(_flat(a.get("command") or a.get("cmd") or ""))
                             if not _THIS_PC.match(u)]:
        far = [u for u in _URL.findall(_flat(a.get("command") or a.get("cmd") or "")) if not _THIS_PC.match(u)]
        seen.took_in(f"a download ({_host(far[0])})", result)
    else:
        seen.kept(result)


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
                seen.person_said(text)
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
    seen.person_said(prompt)
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
        cmd = _flat(a.get("command") or a.get("cmd") or "")
        bare = _bare_hosts(_URL.sub(" ", cmd), True) if _NET_WORDS.search(cmd) else []
        return _URL.findall(cmd) + bare
    if name in _NAME_ARGS:
        # open_app hands an address to the default browser.
        text = str(a.get(_NAME_ARGS[name]) or "")
        return _URL.findall(text) + _bare_hosts(_URL.sub(" ", text), bool(_NET_WORDS.search(text)))
    if name in _TYPES_TEXT:
        # Typed into an address bar, an address is a request like any other.
        text = str(a.get(_TYPES_TEXT[name]) or "")
        return _URL.findall(text) + _bare_hosts(_URL.sub(" ", text), False)
    return []


def _strings(v: Any, depth: int = 0) -> Iterable[str]:
    if isinstance(v, str):
        yield v
    elif depth < 4 and isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x, depth + 1)
    elif depth < 4 and isinstance(v, dict):
        for x in v.values():
            yield from _strings(x, depth + 1)


# Arguments that name a place to open or a command to run; what a file is to hold is not one.
_PLACE_ARGS = ("path", "file", "file_path", "filePath", "source", "archive", "paths", "files", "folder",
               "directory", "dir", "target", "image", "attachment", "attachments", "destination", "dest",
               "cwd", "workdir", "url", "name", "command", "cmd", "text", "image_path", "output")


def _unc_paths(args: Any) -> List[tuple]:
    """(host, path) for every path on another machine in a call's arguments."""
    a = args if isinstance(args, dict) else {}
    out = []
    for s in _strings([a.get(k) for k in _PLACE_ARGS if k in a]):
        for m in _UNC.finditer(s):
            host = m.group(1).lower()
            if host not in _LOCAL_HOSTS and not host.startswith("127."):
                out.append((host, m.group(0)))
    return out


def _unc_given(path: str, seen: "Seen") -> bool:
    def flat(t: str) -> str:
        t = re.sub(r"^file:/*", "", html.unescape(t).strip(), flags=re.I)
        return t.replace("\\", "/").lstrip("/").rstrip("/").lower()
    p = flat(path)
    return bool(p) and any(p in html.unescape(t).replace("\\", "/").lower() for t in seen.given)


def _address_words(url: str) -> List[str]:
    """The words an address takes with it: the host's labels, the path and the query."""
    u = url if "://" in url else "https://" + url
    try:
        p = urllib.parse.urlsplit(html.unescape(u).strip())
        text = " ".join([re.sub(r"^www\.", "", p.hostname or "", flags=re.I),
                         urllib.parse.unquote_plus(p.path), urllib.parse.unquote_plus(p.query)])
    except ValueError:
        text = url
    return _WORD.findall(text.lower())


_ABOUT: Dict[str, Any] = {"at": 0.0, "text": ""}


def _about_you() -> str:
    """What Inglen keeps about the person (profile, rules, memory, the persona an agent
    brain is given): in the brain's context though no tool result shows it."""
    import time
    if time.time() - _ABOUT["at"] < 30:
        return _ABOUT["text"]
    parts: List[str] = []
    try:
        from . import tools
        ws = tools.WORKSPACE
        places = [os.path.join(ws, n) for n in ("profile.md", "rules.md", "soul.md")]
        places.append(os.path.join(os.path.expanduser("~"), ".hearth", "claude_persona.md"))
        mem = os.path.join(ws, "memory")
        for root, _dirs, files in os.walk(mem):
            places += [os.path.join(root, f) for f in files if f.endswith(".md")]
            if len(places) > 600:
                break
        size = 0
        for p in places[:600]:
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    t = f.read(200000)
            except OSError:
                continue
            parts.append(t)
            size += len(t)
            if size > 4000000:
                break
    except Exception:
        pass
    parts += [os.environ.get(k, "") for k in ("USERNAME", "USER", "COMPUTERNAME", "USERDOMAIN")]
    _ABOUT.update(at=time.time(), text="\n".join(parts).lower())
    return _ABOUT["text"]


def _new_words(url: str, seen: Seen) -> List[str]:
    known = seen.public_words()
    out: List[str] = []
    for w in _address_words(url):
        if w not in known and w not in out:
            out.append(w)
    return out


def _carries_nothing(url: str, seen: Seen) -> bool:
    """Whether an address holds nothing of the person's: every word in it is from what
    they said or what the web showed, besides a few letters and short numbers (a page's
    id, a postcode) that appear nowhere in what this chat holds or what Inglen knows
    about them. A model's own knowledge of a site gives those; private data doesn't."""
    new = _new_words(url, seen)
    # Letters one or two at a time spell anything, known or not: only a few may go.
    short = [w for w in _address_words(url) if len(w) <= 2]
    rest = [w for w in new if len(w) > 2]
    if sum(len(w) for w in short) > 8 or sum(len(w) for w in rest) > 12:
        return False
    if any(not w.isdigit() or len(w) > 6 for w in rest):
        return False
    if not rest:
        return True
    held = "\n".join(seen.held).lower() + "\n" + _about_you()
    return not any(w in held for w in rest)


def _why_new(url: str, seen: Seen) -> str:
    new = _new_words(url, seen)
    held = "\n".join(seen.held).lower() + "\n" + _about_you()
    words = set(_WORD.findall(held))
    # A number may sit inside a longer one (a phone number sent in pieces); a word is matched whole.
    mine = [w for w in new if len(w) > 2 and (w in held if w.isdigit() else w in words)]
    if mine:
        return (f"The address holds {', '.join(mine[:3])}, which is in what this chat read on this PC or what "
                f"Inglen knows about you, so the page could be using it to carry your data out.")
    if new:
        return (f"Part of the address ({', '.join(new[:4])}) isn't in anything you said or anything the web "
                f"showed, so the page could be using it to carry your data out.")
    return "The page could be using it to carry your data out."


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
        if _THIS_PC.match(url) or seen.is_given(url) or _carries_nothing(url, seen):
            continue
        return (f"This would send a request to {_host(url)}: {_short(url)}. Nobody gave that address, "
                f"and this chat has read {src}, which could have asked for it. {_why_new(url, seen)} "
                f"Always doesn't cover a request like this: it asks each time.")
    for host, path in _unc_paths(args):
        if _unc_given(path, seen):
            continue
        return (f"This would connect to {host} for {_short(path)}, a path on another machine. Nobody gave "
                f"that path, and this chat has read {src}, which could have asked for it.")
    return ""


# ---------------------------------------------------------------- the local network

def _local_ip(addr: str) -> bool:
    import ipaddress
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return True                      # carrier-grade NAT, also where Tailscale puts its devices
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)


def _resolve(host: str) -> List[str]:
    import socket
    out = set()
    try:
        out.add(socket.inet_ntoa(socket.inet_aton(host)))   # 2130706433, 0x7f.1, 127.1 and the like
    except (OSError, UnicodeError, ValueError):
        pass
    try:
        out |= {i[4][0] for i in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        pass
    return sorted(out)


def _named_by_person(host: str, port: Optional[int], said: Iterable[str]) -> bool:
    """Whether the person wrote this host (with this port, when one is given) in their own words."""
    h = re.escape(host.lower().strip("[]"))
    if port:
        pat = rf"(?<![\w.-])\[?{h}\]?:{port}(?!\d)"
    else:
        pat = rf"(?<![\w.-])\[?{h}\]?(?![\w-]|\.\w)"
    return any(re.search(pat, html.unescape(t).lower()) for t in said)


def local_refusal(url: str, seen: Optional[Seen] = None) -> str:
    """Why a fetch or a page open may not go to this address: it is on this PC or the local
    network (after looking the name up) and the person didn't name it. '' when it may."""
    u = url if "://" in url else "https://" + url
    try:
        parts = urllib.parse.urlsplit(u)
        host, port = (parts.hostname or ""), parts.port
    except ValueError:
        return ""
    if not host:
        return ""
    addrs = [host] if _local_ip(host) else [a for a in _resolve(host) if _local_ip(a)]
    if not addrs:
        return ""
    if seen is None:
        seen = current()
    said = seen.said if seen is not None else []
    loop = host.lower() in ("localhost",) or all(_local_ip(a) and a.startswith(("127.", "::1")) for a in addrs)
    names = {host.lower()}
    if loop:
        names |= {"localhost", "127.0.0.1", "::1"}
    # On this PC the port matters (each one is a different program); on the network the host is enough.
    if any(_named_by_person(n, port if loop else None, said) for n in names):
        return ""
    where = "this PC" if loop else "the local network"
    at = f"{host}:{port}" if port else host
    also = f" ({addrs[0]})" if addrs[0] != host else ""
    return (f"Error: {at}{also} is on {where}, and only an address you were given in the person's own "
            f"words may be opened there. Ask them for it, or say what you wanted to check.")
