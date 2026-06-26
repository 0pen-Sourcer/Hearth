"""Shared formatting for the live tool-call status the phone/chat bridges show.

Discord and Telegram post one status message and edit it in place as the agent
works, so the owner watches which tools fire (read_file -> web_search -> ...)
instead of staring at a silent pause and then a wall of text. WhatsApp can't
reliably edit a sent message, so it gets a one-line "used: ..." footer instead.

Plain text only (no markdown/backticks) so the same string renders cleanly in
Discord, Telegram (no parse_mode), and a terminal log.
"""
from __future__ import annotations

# A glyph per common tool so the live view reads at a glance.
_ICON = {
    "read_file": "\U0001F4C4", "summarize_file": "\U0001F4C4",
    "write_file": "✍️", "edit_file": "✍️",
    "list_dir": "\U0001F4C2", "list_directory": "\U0001F4C2",
    "find_file": "\U0001F50E", "grep": "\U0001F50E", "search_files": "\U0001F50E",
    "search_chats": "\U0001F50E",
    "run_command": "⌨️",
    "web_search": "\U0001F310", "fetch_url": "\U0001F310", "browse": "\U0001F310",
    "open_url": "\U0001F310",
    "view_image": "\U0001F5BC️", "screenshot": "\U0001F5BC️",
    "take_screenshot": "\U0001F5BC️",
    "memory_recall": "\U0001F9E0", "memory_save": "\U0001F9E0",
    "set_reminder": "⏰", "list_reminders": "⏰",
    "desktop_click": "\U0001F5B1️", "desktop_type": "⌨️",
    "desktop_snapshot": "\U0001F5A5️",
    "open_app": "\U0001F680",
}

# Argument keys most likely to say WHAT a call is acting on, best-first.
_ARG_KEYS = ("path", "file", "filename", "query", "url", "command", "name",
             "pattern", "app", "text", "prompt")


def arg_hint(args, limit: int = 40) -> str:
    """A short human hint of what a call targets (the path/query/url/etc.)."""
    if not isinstance(args, dict):
        return ""
    for k in _ARG_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            v = v.strip().replace("\n", " ")
            if k in ("path", "file", "filename") and ("\\" in v or "/" in v):
                v = v.replace("\\", "/").rstrip("/").split("/")[-1] or v
            return v[:limit] + ("…" if len(v) > limit else "")
    return ""


def _line(name, args=None, done=False) -> str:
    icon = _ICON.get(name, "•")
    hint = arg_hint(args) if args else ""
    mark = "✓" if done else "…"  # check vs ellipsis
    tail = f"  {hint}" if hint else ""
    return f"{icon} {name}{tail} {mark}"


def format_status(events, working: bool = True, max_lines: int = 8) -> str:
    """Render the live tool view.

    events: ordered list of (tool_name, args_dict). While ``working`` the last
    line shows as in-progress (...); the rest are done (check). When the run is
    over, pass working=False so every line reads done.
    """
    if not events:
        return "\U0001F527 working…"
    shown = events[-max_lines:]
    lines = []
    for i, (name, args) in enumerate(shown):
        in_progress = working and i == len(shown) - 1
        lines.append(_line(name, args, done=not in_progress))
    header = "\U0001F527 working…" if working else "✅ done"
    extra = len(events) - len(shown)
    if extra > 0:
        header += f" (+{extra} earlier)"
    return header + "\n" + "\n".join(lines)


def footer(events, max_tools: int = 8) -> str:
    """One-line summary for channels that can't edit a live message (WhatsApp)."""
    names = list(dict.fromkeys(n for n, _ in events))
    if not names:
        return ""
    shown = names[:max_tools]
    tail = f" +{len(names) - len(shown)} more" if len(names) > len(shown) else ""
    return "_used: " + ", ".join(shown) + tail + "_"
