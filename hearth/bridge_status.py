"""Shared formatting for the live tool-call status the phone/chat bridges show.

Discord and Telegram post one status message and edit it in place as the agent
works, then FINALIZE it into a "Tools used" list that stays put — the actual
answer is sent as a separate message below it, so the channel reads
"tools used X" then the reply (never one mashed into the other). WhatsApp can't
reliably edit, so it gets a one-line "used: ..." footer instead.

Text only — NO emojis (the user explicitly didn't want emoji in chat). On
Discord (`rich=True`) tool names are wrapped in `code` and the header bolded so
they stay visually distinct; Telegram gets the same content in plain text.

`files_in` finds the local files a reply names, which every bridge sends back
as attachments. `follow_parent` ends a bridge when the Hearth that started it
is gone.
"""
from __future__ import annotations

import os
import re

# Argument keys most likely to say WHAT a call is acting on, best-first.
_ARG_KEYS = ("path", "file", "filename", "query", "url", "command", "name",
             "pattern", "app", "text", "prompt")


def arg_hint(args, limit: int = 44) -> str:
    """A short human hint of what a call targets (the path/query/url/etc.)."""
    if not isinstance(args, dict):
        return ""
    for k in _ARG_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            v = v.strip().replace("\n", " ")
            if k in ("path", "file", "filename") and ("\\" in v or "/" in v):
                v = v.replace("\\", "/").rstrip("/").split("/")[-1] or v
            return v[:limit] + ("..." if len(v) > limit else "")
    return ""


def format_status(events, working: bool = True, rich: bool = False,
                  max_lines: int = 10) -> str:
    """Render the tool view (no emojis).

    events: ordered list of (tool_name, args_dict).
    working: True while the run is live (header "Running tools..."), False once
             finished (header "Tools used (N)" — the message to leave behind).
    rich:    Discord — bold header + `code` tool names. Telegram/plain — off.
    """
    bold = (lambda s: f"**{s}**") if rich else (lambda s: s)
    code = (lambda s: f"`{s}`") if rich else (lambda s: s)
    if not events:
        return bold("Running tools...")
    shown = events[-max_lines:]
    lines = []
    for name, args in shown:
        hint = arg_hint(args)
        lines.append("  " + code(name) + (f"  {hint}" if hint else ""))
    extra = len(events) - len(shown)
    if working:
        head = bold("Running tools...")
        if extra > 0:
            head += f"  (+{extra} earlier)"
    else:
        head = bold(f"Tools used ({len(events)})")
    return head + "\n" + "\n".join(lines)


def format_done(events, rich: bool = False) -> str:
    """The message left behind after a run: a compact deduped summary so even a
    20-30 tool run collapses to one tidy line (the live view showed the detail).
    e.g. "Tools used (25): run_command x20, read_file x3, web_search x2"."""
    from collections import Counter
    if not events:
        return ""
    bold = (lambda s: f"**{s}**") if rich else (lambda s: s)
    code = (lambda s: f"`{s}`") if rich else (lambda s: s)
    counts = Counter(n for n, _ in events)
    parts = [code(n) + (f" x{c}" if c > 1 else "") for n, c in counts.items()]
    return bold(f"Tools used ({len(events)}):") + " " + ", ".join(parts)


def footer(events, max_tools: int = 8) -> str:
    """One-line summary for channels that can't edit a live message (WhatsApp)."""
    names = list(dict.fromkeys(n for n, _ in events))
    if not names:
        return ""
    shown = names[:max_tools]
    tail = f" +{len(names) - len(shown)} more" if len(names) > len(shown) else ""
    return "_used: " + ", ".join(shown) + tail + "_"


# A path is either quoted or backticked, where it may contain spaces, or bare,
# where it ends at the first space. Only names of real files survive, so a
# span that merely looks like a path is dropped.
# A drive path (C:\...), or on Linux and macOS one from the root or home
# (/home/..., ~/...); quoted it may hold spaces. A slash inside a web link
# (after a letter, dot, colon or slash) doesn't start a path.
_PATH_IN_TEXT = re.compile(
    r"[`\"'“‘]((?:[A-Za-z]:[\\/]|/|~/)[^`\"'”’<>|\r\n]+)"
    r"|((?:(?<!\w)[A-Za-z]:[\\/]|(?<![\w.:/])/|(?<![\w.:/])~/)[^\s`\"'<>|]+\.[A-Za-z0-9]{1,5})")


def files_in(text: str, max_bytes: int, limit: int = 4) -> list:
    """Existing local files named in `text`, in order, under `max_bytes` each."""
    seen, out = set(), []
    for m in _PATH_IN_TEXT.finditer(text or ""):
        p = os.path.expanduser((m.group(1) or m.group(2)).strip().rstrip(".,;:)"))
        key = os.path.normcase(os.path.abspath(p)) if p else ""
        if not p or key in seen:
            continue
        try:
            ok = os.path.isfile(p) and os.path.getsize(p) < max_bytes
        except OSError:
            ok = False
        if ok:
            seen.add(key)
            out.append(p)
    return out[:limit]


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259                            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def follow_parent(every: float = 5.0) -> None:
    """End this bridge when the Hearth that started it has gone. Otherwise it
    keeps answering on its own, and the next Hearth starts a second one on the
    same bot, so every message gets two replies."""
    try:
        pid = int(os.environ.get("HEARTH_PARENT_PID") or 0)
    except ValueError:
        pid = 0
    if not pid:
        return
    import threading
    import time

    def watch():
        while True:
            time.sleep(every)
            if not _alive(pid):
                os._exit(0)

    threading.Thread(target=watch, daemon=True, name="follow-parent").start()


def channel_primer(app: str) -> dict:
    """What JARVIS is told about a phone chat, the same for every bridge."""
    return {"role": "system", "content": (
        f"You're talking over {app}, not in the Inglen window: they're on their phone "
        f"or another device and can't see this PC's screen. To send them a file or an "
        f"image (a screenshot, a PDF, a chart you made), put its full local path in your "
        f"reply and it reaches them as an attachment; opening it on the PC shows them "
        f"nothing. Keep replies chat-length, with no window buttons in them. Destructive "
        f"commands can't run from here; they run from the Inglen window or terminal.")}
