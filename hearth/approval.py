"""A risky step put to the person, said the same way on every surface: the
window's strip, the terminal, the phone card, a chat app and the desktop panel.

    head    who wants to do what, in plain words: "Inglen wants to delete a file"
    detail  the specifics apart from the words: the command, the paths
    why     why it asks, when there is a reason beyond the approval mode

Never a tool's own name. Allow and Deny everywhere; a surface that already
offers more (the window's Always and Never) keeps it.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


def _cut(v: Any, n: int = 120) -> str:
    t = " ".join(str(v or "").split())
    return t if len(t) <= n else t[:n].rstrip() + "..."


def _paths(v: Any) -> List[str]:
    if isinstance(v, (list, tuple)):
        return [str(p) for p in v if str(p or "").strip()]
    return [str(v)] if str(v or "").strip() else []


SHOWN = 4      # paths listed in the box; the rest are counted


def listing(paths: List[str]) -> str:
    """Every path, one per line, up to SHOWN, then how many more."""
    out = list(paths[:SHOWN])
    if len(paths) > SHOWN:
        out.append(f"and {len(paths) - SHOWN} more")
    return "\n".join(out)


def _files(paths: List[str], one: str = "a file", many: str = "files") -> str:
    if len(paths) <= 1:
        return one
    words = {2: "two", 3: "three", 4: "four", 5: "five"}
    return f"{words.get(len(paths), str(len(paths)))} {many}"


def parts(name: str, args: Dict[str, Any]) -> Tuple[str, str]:
    """(what, detail): what in a few plain words, and the specifics apart."""
    a = args if isinstance(args, dict) else {}
    if name in ("run_command", "start_job"):
        return ("start a command" if name == "start_job" else "run a command",
                str(a.get("command") or a.get("cmd") or "").strip())
    if name in ("write_file", "append_file"):
        return "write a file", str(a.get("path") or "")
    if name == "edit_file":
        return "change a file", str(a.get("path") or "")
    if name == "create_directory":
        return "make a folder", str(a.get("path") or "")
    if name in ("delete_file", "delete_path"):
        ps = _paths(a.get("paths") or a.get("path"))
        folder = name == "delete_path" and len(ps) == 1 and not os.path.splitext(ps[0])[1]
        return "delete " + ("a folder" if folder else _files(ps)), listing(ps)
    if name in ("move_path", "move_file"):
        src = a.get("sources") or a.get("source") or a.get("src") or a.get("path")
        dst = a.get("destination") or a.get("dst") or a.get("dest")
        return "move " + _files(_paths(src)), listing(_paths(src)) + (f"\nto {dst}" if dst else "")
    if name == "send_email":
        return f"send an email to {a.get('to') or 'someone'}", _cut(a.get("subject"), 200)
    if name == "send_to_phone":
        what = a.get("path") or a.get("file")
        return (("send a file to your phone", str(what)) if what else
                ("send a message to your phone", _cut(a.get("message") or a.get("text"), 400)))
    if name in ("open_url", "open_in_browser"):
        return "open a web page", str(a.get("url") or "")
    if name == "open_app":
        return f"open {a.get('name') or a.get('app') or 'an app'}", ""
    if name == "install_skill":
        return "install a skill", str(a.get("name") or a.get("source") or "")
    if name in ("delegate", "launch_team"):
        who = a.get("agent") or a.get("runtime") or "another agent"
        return f"hand this to {who}", _cut(a.get("task") or a.get("prompt"), 400)
    if name in ("generate_image", "generate_video"):
        return f"make {'a video' if 'video' in name else 'an image'}", _cut(a.get("prompt"), 400)
    if name == "load_model":
        return "load a model", str(a.get("model") or a.get("name") or "")
    if name.startswith(("computer_", "desktop_", "browse_")) or name in ("execute_actions", "smart_click"):
        return "use the mouse and keyboard on your PC", ""
    if name == "read_inbox":
        return "read your email", ""
    if name == "__extend_workspace__":
        return "write outside its folder", str(a.get("parent") or a.get("path") or "")
    words = str(name or "a tool").replace("_", " ")
    shown = "\n".join(f"{k}: {_cut(v, 200)}" for k, v in list(a.items())[:4]
                      if isinstance(v, (str, int, float)) and str(v).strip())
    return f"use {words}", shown


def line(name: str, args: Dict[str, Any]) -> str:
    """The step in one line, for a question that can only be words: a command
    stays in backticks, so a card reading it can set it apart again."""
    what, detail = parts(name, args or {})
    if name in ("run_command", "start_job"):
        return f"{what.split()[0]} `{_cut(detail, 160)}`"
    return what + (f": {_cut(detail.replace(chr(10), ', '), 160)}" if detail else "")


def agent() -> str:
    try:
        from .call_me import agent_name
        return agent_name() or "Inglen"
    except Exception:
        return "Inglen"


def card(name: str, args: Dict[str, Any], who: str = "", why: str = "") -> Dict[str, str]:
    """{who, what, head, detail, why} for one step."""
    what, detail = parts(name, args or {})
    who = who or agent()
    if not why:
        try:
            from .tools import sends_out
            why = sends_out(name, args or {}) or ""
        except Exception:
            why = ""
    return {"who": who, "what": what, "head": f"{who} wants to {what}", "detail": detail.strip(),
            "why": " ".join(str(why or "").split())}


def question(c: Dict[str, str]) -> str:
    """The card as one plain question, for a chat that can only take text."""
    q = c["what"][:1].upper() + c["what"][1:] + "?"
    if c.get("why"):
        q += " " + c["why"]
    return q


def declined(c: Dict[str, str], aside: bool = False) -> str:
    """The quiet line left once it is declined."""
    return f"{'Not run' if aside else 'Declined'}: {c['what']}"


def terminal(c: Dict[str, str], dim: str = "", reset: str = "", bold: str = "") -> List[str]:
    """The card in the terminal: who and what, the specifics indented, why dimmed."""
    out = [f"  {bold}{c['head']}{reset}"]
    for line in (c.get("detail") or "").splitlines()[:8]:
        out.append(f"      {line}")
    if c.get("why"):
        out.append(f"  {dim}{c['why']}{reset}")
    return out
