"""What helpers change on disk, kept apart and undoable.

Hearth's own helpers write through Hearth's file tools. Each file a working
helper writes is claimed for it, so a second helper can't write over it
mid-task, and the file as it was before is kept so the helper's changes can
be undone. Agent programs working in a git repository get a worktree of
their own instead; their changes are brought in, discarded or undone whole.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

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


# ---------------------------------------------------------------- worktrees
# An agent program sent to work in a git repository works in its own copy (a
# worktree), started from the repository as it is now, uncommitted changes
# included. Nothing reaches the real folder until its changes are brought in.

_FOLDERS: Dict[str, Dict[str, str]] = {}     # working agent program -> its folder, name, task


def _git(*args: str, cwd: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=_NO_WINDOW, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else f"git {args[0]} failed")
    return r


def _worktree_root() -> Path:
    return Path.home() / ".hearth" / "worktrees"


def isolate(helper_id: str, folder: str, name: str, task: str) -> Tuple[str, str]:
    """Where an agent program should work, and a note for its brief. In a git
    repository that is a fresh worktree; elsewhere it is the folder itself,
    with word of anyone else working there."""
    with _LOCK:
        others = [f for hid, f in _FOLDERS.items() if _key(f["folder"]) == _key(folder)]
        _FOLDERS[helper_id] = {"folder": folder, "name": name, "task": task[:120]}
    top = ""
    if shutil.which("git"):
        r = _git("rev-parse", "--show-toplevel", cwd=folder)
        top = r.stdout.strip() if r.returncode == 0 else ""
    if top and _git("rev-parse", "--verify", "HEAD", cwd=top).returncode == 0:
        base = _git("stash", "create", cwd=top).stdout.strip() or _git("rev-parse", "HEAD", cwd=top).stdout.strip()
        short = helper_id.rsplit("_", 1)[-1]
        slug = re.sub(r"[^a-z0-9]+", "-", (name or "helper").lower()).strip("-") or "helper"
        branch = f"hearth/{slug}-{short}"
        path = _worktree_root() / f"{Path(top).name}-{short}"
        path.parent.mkdir(parents=True, exist_ok=True)
        r = _git("worktree", "add", "--quiet", "-b", branch, str(path), base, cwd=top)
        if r.returncode == 0:
            sub = os.path.relpath(os.path.abspath(folder), os.path.abspath(top))
            work = str(path / sub) if sub != "." else str(path)
            with _LOCK:
                data = _load(helper_id)
                data["worktree"] = {"repo": top, "path": str(path), "branch": branch, "base": base,
                                    "state": "working", "files": [], "stat": ""}
                _save(helper_id, data)
            return work, ("You are working in your own copy of this repository, made for this task. "
                          "Change files freely here; your changes are shown for review when you finish.")
    note = ""
    if others:
        who = "; ".join(f"{o['name']} ({o['task']})" for o in others)
        note = (f"Others are working in this folder at the same time: {who}. "
                "Don't change files they are working on.")
    return folder, note


def settle(helper_id: str) -> Optional[Dict[str, Any]]:
    """When an agent program ends: what it changed in its worktree, kept as a
    patch beside its transcript."""
    with _LOCK:
        _FOLDERS.pop(helper_id, None)
        data = _load(helper_id)
    wt = data.get("worktree")
    if not wt or wt.get("state") != "working":
        return None
    path = wt["path"]
    _git("add", "-A", cwd=path)
    # As bytes, end to end: text mode would turn the line endings into ones the files don't have.
    patch = subprocess.run(["git", "diff", "--cached", "--binary", wt["base"]], cwd=path, capture_output=True,
                           creationflags=_NO_WINDOW, timeout=120).stdout
    files = [l for l in _git("diff", "--cached", "--name-only", wt["base"], cwd=path).stdout.splitlines() if l.strip()]
    stat = _git("diff", "--cached", "--shortstat", wt["base"], cwd=path).stdout.strip()
    (_dir() / f"{helper_id}.patch").write_bytes(patch)
    wt.update(files=files, stat=stat, state="waiting" if files else "empty")
    with _LOCK:
        data = _load(helper_id)
        data["worktree"] = wt
        _save(helper_id, data)
    if not files:
        _remove_worktree(wt)
    from . import subagents as _sa
    _sa._append_transcript(_sa._transcript_path(helper_id), {"kind": "isolation", "files": files, "stat": stat,
                                                             "branch": wt["branch"]})
    return wt


def _remove_worktree(wt: Dict[str, Any]) -> None:
    _git("worktree", "remove", "--force", wt["path"], cwd=wt["repo"])
    _git("branch", "-D", wt["branch"], cwd=wt["repo"])


def _set_state(helper_id: str, state: str) -> None:
    with _LOCK:
        data = _load(helper_id)
        data["worktree"]["state"] = state
        _save(helper_id, data)


def bring_in(helper_id: str) -> Dict[str, Any]:
    """Apply a worktree helper's changes to the real folder."""
    wt = _load(helper_id).get("worktree")
    if not wt:
        return {"ok": False, "error": "this helper didn't work on a separate copy"}
    if wt["state"] != "waiting":
        return {"ok": False, "error": {"working": "it is still working", "brought_in": "already brought in",
                                       "discarded": "its changes were discarded", "empty": "it changed nothing",
                                       "undone": "its changes were undone"}.get(wt["state"], wt["state"])}
    patch = str(_dir() / f"{helper_id}.patch")
    # Working files only: the index stays as it was, so nothing looks staged that wasn't.
    r = _git("apply", "--whitespace=nowarn", patch, cwd=wt["repo"])
    if r.returncode != 0:
        said = (r.stderr or r.stdout)
        clash = sorted({m.group(1) for m in re.finditer(r"error: (?:patch failed: )?([^:\n]+?):", said)})
        files = " and ".join(clash) if clash else "some files"
        return {"ok": False, "clash": clash,
                "error": f"its changes to {files} clash with what changed in the folder since. Discard them, "
                         f"or look at the diff and make the change by hand."}
    _set_state(helper_id, "brought_in")
    _remove_worktree(wt)
    _note(helper_id, "brought_in", wt["files"])
    return {"ok": True, "files": wt["files"]}


def discard(helper_id: str) -> Dict[str, Any]:
    wt = _load(helper_id).get("worktree")
    if not wt or wt["state"] not in ("waiting", "empty"):
        return {"ok": False, "error": "nothing waiting to discard"}
    _remove_worktree(wt)
    _set_state(helper_id, "discarded")
    _note(helper_id, "discarded", wt["files"])
    return {"ok": True, "files": wt["files"]}


def _note(helper_id: str, kind: str, files: List[str]) -> None:
    from . import subagents as _sa
    _sa._append_transcript(_sa._transcript_path(helper_id), {"kind": kind, "files": files})


def diff(helper_id: str, limit: int = 200_000) -> str:
    try:
        return (_dir() / f"{helper_id}.patch").read_bytes()[:limit].decode("utf-8", "replace")
    except OSError:
        return ""


def summary(helper_id: str) -> Dict[str, Any]:
    """What the panel shows: what changed, where it stands, and what can be done about it."""
    data = _load(helper_id)
    wt = data.get("worktree")
    if wt:
        st = wt.get("state", "")
        return {"files": wt.get("files", []), "stat": wt.get("stat", ""), "state": st, "separate": True,
                "can_bring_in": st == "waiting", "can_discard": st == "waiting", "can_undo": st == "brought_in"}
    return {"files": [f["path"] for f in data["files"]], "undone": bool(data.get("undone")), "separate": False,
            "state": "undone" if data.get("undone") else ("changed" if data["files"] else ""),
            "can_undo": bool(data["files"]) and not data.get("undone")}


def undo(helper_id: str) -> Dict[str, Any]:
    """Take back what a helper changed: its copied files put back, or its
    brought-in changes reversed."""
    wt = _load(helper_id).get("worktree")
    if wt:
        if wt["state"] == "waiting":
            return discard(helper_id)
        if wt["state"] != "brought_in":
            return {"ok": False, "error": "nothing to undo"}
        r = _git("apply", "-R", "--whitespace=nowarn", str(_dir() / f"{helper_id}.patch"), cwd=wt["repo"])
        if r.returncode != 0:
            return {"ok": False, "error": "those files changed again since: " + (r.stderr or r.stdout).strip()[-400:]}
        _set_state(helper_id, "undone")
        _note(helper_id, "undone", wt["files"])
        return {"ok": True, "restored": wt["files"], "skipped": []}
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


def act(helper_id: str, action: str = "show") -> Dict[str, Any]:
    """The helper_changes tool: show, bring_in, discard or undo."""
    from .subagents import agent_story
    if not agent_story(helper_id):
        return {"ok": False, "error": f"no helper '{helper_id}'"}
    action = (action or "show").strip().lower()
    if action == "show":
        out = {"ok": True, **summary(helper_id)}
        d = diff(helper_id, 12_000)
        if d:
            out["diff"] = d
        return out
    if action == "bring_in":
        return bring_in(helper_id)
    if action == "discard":
        return discard(helper_id)
    if action == "undo":
        return undo(helper_id)
    return {"ok": False, "error": "action is show, bring_in, discard or undo"}
