"""What helpers change on disk, kept apart and undoable.

Hearth's own helpers write through Hearth's file tools. Each file a working
helper writes is claimed for it, so a second helper can't write over it
mid-task, and the file as it was before is kept so the helper's changes can
be undone.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# Which arguments of a file tool name the paths it writes.
WRITE_TOOLS = {"write_file": ("path",), "edit_file": ("path",), "delete_path": ("path",),
               "move_path": ("source", "destination"), "create_directory": ("path",)}

_LOCK = threading.Lock()
_CLAIMS: Dict[str, str] = {}          # path key -> helper id


def _dir() -> Path:
    from .subagents import _TRANSCRIPT_DIR
    _TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    return _TRANSCRIPT_DIR


def _key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def targets(name: str, args: Dict[str, Any]) -> List[str]:
    """The absolute paths a file tool call would write, as the tools resolve them."""
    from .tools import _write_path
    return [_write_path(p) for p in (str((args or {}).get(f) or "").strip() for f in WRITE_TOOLS.get(name, ())) if p]


def _name(helper_id: str) -> str:
    from . import subagents as _sa
    try:
        return (_sa.agent_story(helper_id) or {}).get("name") or "another helper"
    except Exception:
        return "another helper"


def claim(helper_id: str, name: str, args: Dict[str, Any]) -> Optional[str]:
    """Claim what this call writes for `helper_id`. Returns why it may not, or
    None when the call can go ahead. A file stays claimed until its helper
    ends (release)."""
    paths = targets(name, args)
    with _LOCK:
        for p in paths:
            held = _CLAIMS.get(_key(p))
            if held and held != helper_id:
                return (f"(not run: {_name(held)} is working on {os.path.basename(p) or p} right now. "
                        f"Leave that file to them, or ask them about it with message_helper.)")
        for p in paths:
            _CLAIMS[_key(p)] = helper_id
    return None


def release(helper_id: str) -> None:
    with _LOCK:
        for k in [k for k, v in _CLAIMS.items() if v == helper_id]:
            _CLAIMS.pop(k, None)


def _manifest_path(helper_id: str) -> Path:
    return _dir() / f"{helper_id}.changes.json"


def _load(helper_id: str) -> Dict[str, Any]:
    try:
        return json.loads(_manifest_path(helper_id).read_text(encoding="utf-8"))
    except Exception:
        return {"files": [], "undone": False}


def _save(helper_id: str, data: Dict[str, Any]) -> None:
    _manifest_path(helper_id).write_text(json.dumps(data, indent=1), encoding="utf-8")


def keep_before(helper_id: str, name: str, args: Dict[str, Any]) -> None:
    """Before a helper first writes a path, keep what was there (or that
    nothing was), so undo can put it back."""
    paths = targets(name, args)
    if not paths:
        return
    with _LOCK:
        data = _load(helper_id)
        seen = {_key(f["path"]) for f in data["files"]}
        for p in paths:
            if _key(p) in seen:
                continue
            entry: Dict[str, Any] = {"path": p, "kept": None}
            if os.path.exists(p):
                store = _dir() / f"{helper_id}.files" / str(len(data["files"]))
                store.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if os.path.isdir(p):
                        shutil.copytree(p, store)
                    else:
                        shutil.copy2(p, store)
                    entry["kept"] = str(store)
                except OSError:
                    entry["kept"] = ""        # couldn't be kept; undo leaves it alone
            data["files"].append(entry)
            seen.add(_key(p))
        _save(helper_id, data)


def changed(helper_id: str) -> List[str]:
    """The paths a helper wrote, moved, made or deleted."""
    return [f["path"] for f in _load(helper_id)["files"]]


def summary(helper_id: str) -> Dict[str, Any]:
    """What the panel shows: the files changed and whether they were put back."""
    data = _load(helper_id)
    return {"files": [f["path"] for f in data["files"]], "undone": bool(data.get("undone")),
            "can_undo": bool(data["files"]) and not data.get("undone")}


def undo(helper_id: str) -> Dict[str, Any]:
    """Put back every path a helper touched as it was before its first write."""
    with _LOCK:
        data = _load(helper_id)
        if data.get("undone"):
            return {"ok": False, "error": "already undone"}
        if not data["files"]:
            return {"ok": False, "error": "it didn't change any files"}
        put, skipped = [], []
        for f in reversed(data["files"]):
            p, kept = f["path"], f["kept"]
            try:
                if kept == "":
                    skipped.append(p)
                    continue
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                elif os.path.exists(p):
                    os.remove(p)
                if kept:
                    if os.path.isdir(kept):
                        shutil.copytree(kept, p)
                    else:
                        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
                        shutil.copy2(kept, p)
                put.append(p)
            except OSError as e:
                skipped.append(f"{p} ({e.strerror or e})")
        data["undone"] = True
        _save(helper_id, data)
    from . import subagents as _sa
    _sa._append_transcript(_sa._transcript_path(helper_id), {"kind": "undone", "files": put, "skipped": skipped})
    return {"ok": True, "restored": put, "skipped": skipped}
