"""A coding-agent session started from the phone: the agents installed here, the
folders they have worked in, browsing the PC's folders, and the session itself,
which runs in the agent's own harness (agent_sessions.run_in) with its own model
and permissions, not through Inglen's assistant."""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List

# Sessions asked for and not yet started: their first message starts them.
DRAFTS: Dict[str, Dict[str, Any]] = {}


def catalog() -> Dict[str, Any]:
    """{agents: [{id, label}], recent: [{agent, path, at}]}: the agents installed on this PC,
    and the folders each has worked in, newest first."""
    from . import agent_sessions as S
    agents = [{"id": a, "label": S.label(a)} for a in S.AGENTS if S._exe(a)]
    have = {a["id"] for a in agents}
    recent: List[Dict[str, Any]] = []
    seen = set()
    for r in S.listing(400):
        cwd = r.get("cwd") or ""
        key = (r["agent"], os.path.normcase(cwd))
        if r["agent"] not in have or not cwd or key in seen or not os.path.isdir(cwd):
            continue
        seen.add(key)
        recent.append({"agent": r["agent"], "path": cwd, "name": r.get("project") or cwd, "at": r["at"]})
    return {"agents": agents, "recent": recent[:80]}


def _private(path: str) -> bool:
    keys = os.path.normcase(os.path.realpath(os.path.expanduser("~/.hearth")))
    p = os.path.normcase(path)
    return p == keys or p.startswith(keys + os.sep)


def folders(path: str = "") -> Dict[str, Any]:
    """The folders inside `path` (home when empty), for picking where a session works."""
    path = os.path.realpath(os.path.expanduser(path or "~"))
    if _private(path):
        return {"error": "That folder is private."}
    if not os.path.isdir(path):
        return {"error": "That folder isn't on this PC."}
    try:
        inside = sorted(({"name": e.name, "path": e.path} for e in os.scandir(path)
                         if not e.name.startswith((".", "$")) and e.is_dir(follow_symlinks=False)
                         and not _private(e.path)), key=lambda e: e["name"].lower())
    except OSError:
        return {"error": "That folder couldn't be opened."}
    up = os.path.dirname(path)
    roots = [os.path.expanduser("~")]
    if os.name == "nt":
        roots += [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{c}:\\")]
    return {"path": path, "name": os.path.basename(path.rstrip("/\\")) or path,
            "up": up if up != path else "", "folders": inside[:400], "roots": roots}


def reserve(agent: str, cwd: str, text: str) -> Dict[str, Any]:
    """A session to start with `text` in `cwd`: its row, with a chat id the phone opens at once.
    The agent's own session id replaces it when the agent gives one (the "session" event)."""
    from . import agent_sessions as S
    if agent not in S.AGENTS or not S._exe(agent):
        return {"error": "That coding agent isn't installed on this PC."}
    cwd = os.path.realpath(os.path.expanduser(cwd or ""))
    if not cwd or not os.path.isdir(cwd):
        return {"error": "That folder isn't on this PC."}
    if _private(cwd):
        return {"error": "That folder is private."}
    if not (text or "").strip():
        return {"error": "Type what it should do first."}
    cid = S.chat_id(agent, "new" + uuid.uuid4().hex[:12])
    row = {"chat": cid, "agent": agent, "id": "", "label": S.label(agent), "cwd": cwd,
           "project": os.path.basename(cwd.rstrip("/\\")) or cwd, "title": S._title(text),
           "at": int(time.time() * 1000), "live": "recent", "model": ""}
    DRAFTS[cid] = row
    return row


async def start(row: Dict[str, Any], text: str, emit, ask, *, drain=None) -> int:
    from . import agent_sessions as S
    exe = S._exe(row["agent"])
    if not exe:
        emit("error", message=f"{row['label']} isn't installed on this PC any more.")
        return 2
    return await S.run_in(row["agent"], exe, "", row["cwd"], text, emit, ask, drain=drain)
