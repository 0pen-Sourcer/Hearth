"""Undo for what the agent changes on disk, and a note of what it created.

Before a tool changes a file or a folder (write_file, edit_file, delete_path,
move_path, and a plain delete command Auto lets through), a copy of what was
there is kept under <workspace>/.undo and the change is written in its journal;
undo(id) puts it back. Kept a week, at most UNDO_MAX in all, oldest out first.

Everything the app's own tools create is noted too, so Auto can let the agent
delete or overwrite what it made itself without asking (made_by_app). The
journal is a file, not memory: an agent brain runs these tools in a separate
process (the MCP server), and both must see the same record.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

KEEP_S = 7 * 86400
COPY_MAX = 200 * 1024 * 1024        # what one change may copy aside; bigger isn't undoable
UNDO_MAX = 2 * 1024 * 1024 * 1024
_LOCK = threading.Lock()


def _root() -> str:
    from .tools import WORKSPACE
    return os.path.join(WORKSPACE, ".undo")


def _journal_path() -> str:
    return os.path.join(_root(), "journal.json")


def _read() -> Dict[str, Any]:
    try:
        with open(_journal_path(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("changes", [])
            d.setdefault("created", {})
            return d
    except (OSError, ValueError):
        pass
    return {"changes": [], "created": {}}


def _write(d: Dict[str, Any]) -> None:
    os.makedirs(_root(), exist_ok=True)
    tmp = _journal_path() + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, _journal_path())


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(p))))


def _size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dp, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
            if total > COPY_MAX:
                return total
    return total


# ---------------------------------------------------------------- what a call changes

def _resolve(p: str, cwd: str = "") -> str:
    from .tools import WORKSPACE
    p = os.path.expandvars(os.path.expanduser(str(p or "").strip().strip('"').strip("'")))
    return os.path.abspath(p if os.path.isabs(p) else os.path.join(cwd or WORKSPACE, p))


_DEL = re.compile(r"^\s*(?:cmd(?:\.exe)?\s+/[ck]\s+)?(rm|del|erase|rd|rmdir|Remove-Item|ri)\b(.*)$", re.I | re.S)


def delete_targets(cmd: str, cwd: str = "") -> Optional[List[str]]:
    """The paths a plain, single delete command removes (rm -rf X, del /s /q X, rd /s /q X,
    Remove-Item -Recurse X), or None for anything else: chained, piped, redirected,
    wildcarded or with a variable or a subshell, it isn't read as plain."""
    if not cmd or re.search(r"[;&|<>`\n]|\$\(|\*|\?", cmd):
        return None
    m = _DEL.match(cmd)
    if not m:
        return None
    try:
        words = shlex.split(m.group(2), posix=False)
    except ValueError:
        return None
    paths = []
    for w in words:
        lw = w.lower()
        if re.fullmatch(r"-[a-z]+|/[a-z]", lw) or lw in ("-recurse", "-force", "-path", "-literalpath", "--"):
            continue
        if lw.startswith("-"):
            return None                       # an option not understood: not plain
        paths.append(_resolve(w, cwd))
    return paths or None


def targets(name: str, args: Dict[str, Any]) -> List[Dict[str, str]]:
    """What a tool call will change: [{kind, path, dest?}]."""
    from .tools import _write_path
    a = args or {}
    try:
        if name in ("write_file", "edit_file"):
            return [{"kind": "change", "path": os.path.abspath(_write_path(a["path"]))}]
        if name == "delete_path":
            return [{"kind": "delete", "path": os.path.abspath(_write_path(a["path"]))}]
        if name == "move_path":
            return [{"kind": "move", "path": os.path.abspath(_write_path(a["source"])),
                     "dest": os.path.abspath(_write_path(a["destination"]))}]
        if name == "create_directory":
            return [{"kind": "mkdir", "path": os.path.abspath(_write_path(a["path"]))}]
        if name == "run_command":
            got = delete_targets(str(a.get("command") or ""), str(a.get("cwd") or ""))
            return [{"kind": "delete", "path": p} for p in got or []]
    except (KeyError, TypeError, ValueError):
        return []
    return []


# ---------------------------------------------------------------- around a call

def before(name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Copy aside what the call will change. None when it changes nothing we follow."""
    items = []
    for t in targets(name, args):
        path = t["path"]
        item = dict(t, existed=os.path.lexists(path), backup="")
        if item["existed"] and t["kind"] in ("change", "delete") or (t["kind"] == "move" and os.path.lexists(t["dest"])):
            src = path if t["kind"] != "move" else t["dest"]
            if t["kind"] == "move":
                item["dest_existed"] = True
            if _size(src) <= COPY_MAX:
                bdir = os.path.join(_root(), "files", uuid.uuid4().hex[:12])
                os.makedirs(bdir, exist_ok=True)
                dst = os.path.join(bdir, os.path.basename(src.rstrip("\\/")) or "item")
                try:
                    if os.path.isdir(src) and not os.path.islink(src):
                        shutil.copytree(src, dst, symlinks=True)
                    else:
                        shutil.copy2(src, dst, follow_symlinks=False)
                    item["backup"] = dst
                except OSError:
                    item["backup"] = ""
            else:
                item["too_big"] = True
        items.append(item)
    if not items:
        return None
    return {"id": uuid.uuid4().hex[:10], "tool": name, "at": time.time(), "items": items}


def after(token: Optional[Dict[str, Any]], ok: bool, chat: str = "") -> None:
    """Keep the change in the journal (and note what was created), or drop its copies when the call failed."""
    if not token:
        return
    if not ok:
        for it in token["items"]:
            if it.get("backup"):
                shutil.rmtree(os.path.dirname(it["backup"]), ignore_errors=True)
        return
    with _LOCK:
        d = _read()
        now = time.time()
        for it in token["items"]:
            made = it["dest"] if it["kind"] == "move" else it["path"]
            if it["kind"] in ("change", "mkdir", "move") and not it.get("existed" if it["kind"] != "move" else "dest_existed") \
                    and os.path.lexists(made):
                d["created"][_norm(made)] = now
            if it["kind"] == "delete":
                d["created"].pop(_norm(it["path"]), None)
            if it["kind"] == "move" and _norm(it["path"]) in d["created"]:
                d["created"][_norm(it["dest"])] = d["created"].pop(_norm(it["path"]))
        if any(it["kind"] != "mkdir" or not it["existed"] for it in token["items"]):
            d["changes"].append(dict(token, chat=chat))
        _prune(d)
        _write(d)


def _ours(backup: str) -> bool:
    """A backup copy this module made: one folder down in .undo/files. A journal entry
    naming any other place is not followed, for removing or for putting back."""
    try:
        return bool(backup) and _norm(os.path.dirname(os.path.dirname(backup))) == _norm(os.path.join(_root(), "files"))
    except (OSError, ValueError):
        return False


def _prune(d: Dict[str, Any]) -> None:
    now = time.time()
    keep, total = [], 0
    for ch in reversed(d["changes"]):
        size = sum(_size(it["backup"]) for it in ch["items"] if it.get("backup") and os.path.exists(it["backup"]))
        if now - ch.get("at", 0) > KEEP_S or total + size > UNDO_MAX:
            for it in ch["items"]:
                if it.get("backup") and _ours(it["backup"]):
                    shutil.rmtree(os.path.dirname(it["backup"]), ignore_errors=True)
            continue
        total += size
        keep.append(ch)
    d["changes"] = list(reversed(keep))
    d["created"] = {k: v for k, v in d["created"].items() if now - v <= KEEP_S}


# ---------------------------------------------------------------- what was made

def made_by_app(path: str) -> bool:
    """True when the path is something the app's tools created this week, or inside it.
    The workspace itself and a drive or home folder never count."""
    n = _norm(path)
    from .tools import WORKSPACE
    if n in (_norm(WORKSPACE), _norm("~")) or os.path.dirname(n) == n:
        return False
    created = _read()["created"]
    p = n
    while True:
        if p in created:
            return True
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent


# ---------------------------------------------------------------- undo

def _words(ch: Dict[str, Any]) -> str:
    out = []
    for it in ch["items"]:
        name = os.path.basename(it["path"].rstrip("\\/"))
        out.append({"change": ("Changed " if it.get("existed") else "Made ") + name, "delete": "Deleted " + name,
                    "move": f"Moved {name} to {os.path.dirname(it.get('dest', ''))}", "mkdir": "Made folder " + name}[it["kind"]])
    return "; ".join(out)


def recent(limit: int = 20) -> List[Dict[str, Any]]:
    """The newest changes that can still be undone: id, when, what, folder."""
    out = []
    for ch in reversed(_read()["changes"]):
        if ch.get("undone"):
            continue
        can = all(it.get("backup") or not it.get("existed") or it["kind"] in ("move", "mkdir") for it in ch["items"])
        out.append({"id": ch["id"], "at": ch["at"], "what": _words(ch), "can": can, "chat": ch.get("chat", ""),
                    "where": os.path.dirname(ch["items"][0]["path"])})
        if len(out) >= limit:
            break
    return out


def undo(change_id: str) -> Dict[str, Any]:
    """Put back what one change did. {ok, said} or {ok: False, error}."""
    with _LOCK:
        d = _read()
        ch = next((c for c in d["changes"] if c["id"] == change_id), None)
        if not ch:
            return {"ok": False, "error": "that change isn't in the list any more"}
        if ch.get("undone"):
            return {"ok": False, "error": "that change was undone already"}
        if any(it.get("backup") and not _ours(it["backup"]) for it in ch["items"]):
            return {"ok": False, "error": "that change's saved copy isn't one Inglen kept, so it isn't put back"}
        try:
            for it in reversed(ch["items"]):
                path = it["path"]
                if it["kind"] == "move":
                    if os.path.lexists(it["dest"]) and not os.path.lexists(path):
                        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                        shutil.move(it["dest"], path)
                    if it.get("backup"):
                        _restore(it["backup"], it["dest"])
                elif it.get("existed"):
                    if not it.get("backup"):
                        return {"ok": False, "error": "it was too big to keep a copy of, so it can't be put back"}
                    _restore(it["backup"], path)
                elif os.path.lexists(path):          # it didn't exist before: making it is undone by removing it
                    if os.path.isdir(path) and not os.path.islink(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                    d["created"].pop(_norm(path), None)
        except OSError as e:
            return {"ok": False, "error": f"couldn't put it back: {e}"}
        ch["undone"] = time.time()
        _write(d)
        return {"ok": True, "said": "Undone: " + _words(ch)}


def _restore(backup: str, path: str) -> None:
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.isdir(backup):
        shutil.copytree(backup, path, symlinks=True)
    else:
        shutil.copy2(backup, path, follow_symlinks=False)
