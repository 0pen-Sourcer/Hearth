"""Opt-in update check against GitHub releases.

Never auto-installs and never nags persistently — the CLI surfaces it via
`/update` (and a one-line note at boot if a newer release exists), the GUI via
a button in Settings. A git checkout updates with `git pull --ff-only`; a
packaged build is pointed at the release page to download.

Override the repo with HEARTH_UPDATE_REPO (default "0pen-sourcer/hearth").
Set HEARTH_NO_UPDATE_CHECK=1 to disable the boot check entirely.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from typing import Optional

REPO = os.environ.get("HEARTH_UPDATE_REPO", "0pen-sourcer/hearth")

# Single source of truth for the shipped version. Callers must NOT hardcode it -
# a stale copy makes the update prompt fire forever after the user updates.
HEARTH_VERSION = "0.7.3-preview"


def _norm(v: str) -> tuple:
    """'v0.7.1-preview' -> (0, 7, 1). Ignores any non-numeric suffix so a
    '-preview' tag compares cleanly against a plain semver."""
    nums = re.findall(r"\d+", (v or "").split("-")[0])
    nums = [int(n) for n in nums[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def is_git_checkout() -> bool:
    """True if Hearth is running from a git clone (so `git pull` can update it)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.isdir(os.path.join(root, ".git"))


# Name of the small code-only archive published alongside the installers.
PATCH_ASSET = "hearth-code-{version}.zip"


def code_dir() -> Optional[str]:
    """Directory holding Hearth's patchable .py files, or None if this build
    can't be patched.

    A packaged build ships hearth/*.py loose beside the exe (see
    _unfreeze_hearth in Hearth.spec) precisely so a release can replace ~4 MB of
    code instead of a ~1 GB installer. Builds from before that layout have the
    modules frozen inside the exe, so there is nothing on disk to replace and
    they need one full install to get onto the patchable layout."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None                      # running from source -> git pull
    if os.path.isfile(os.path.join(base, "hearth", "__init__.py")):
        return base
    return None


def can_patch() -> bool:
    return code_dir() is not None


def patch_asset(tag: str = "") -> dict:
    """The code-only patch asset for a release, if one was published."""
    import urllib.request
    api = (f"https://api.github.com/repos/{REPO}/releases/"
           + (f"tags/{tag}" if tag else "latest"))
    try:
        req = urllib.request.Request(api, headers={
            "User-Agent": "Hearth", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.load(r)
    except Exception as e:
        return {"error": f"couldn't reach GitHub releases: {e}"}
    for a in rel.get("assets") or []:
        n = (a.get("name") or "").lower()
        if n.startswith("hearth-code-") and n.endswith(".zip"):
            return {"name": a.get("name"), "url": a.get("browser_download_url"),
                    "size": int(a.get("size") or 0), "tag": rel.get("tag_name")}
    return {"error": "this release has no code patch; a full install is needed"}


def download_patch(on_progress=None, tag: str = "") -> dict:
    """Fetch the code-only patch. Same resumable downloader as everything else."""
    global _dl_cancel
    _dl_cancel = False
    if not can_patch():
        return {"ok": False, "error":
                "this build stores its code inside the executable, so it can't "
                "be patched; install the full release once to switch over"}
    asset = patch_asset(tag)
    if asset.get("error"):
        return {"ok": False, "error": asset["error"]}
    from pathlib import Path
    from .llmserver import _download_with_resume, DownloadCancelled
    dest_dir = Path(os.path.expanduser("~/.hearth/updates"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / asset["name"]
    tmp = dest.with_suffix(dest.suffix + ".part")
    if not (dest.exists() and dest.stat().st_size == asset["size"]):
        try:
            _download_with_resume(asset["url"], dest, tmp, on_progress,
                                  ua="Hearth-updater", cancel=lambda: _dl_cancel)
        except DownloadCancelled:
            return {"ok": False, "cancelled": True,
                    "error": "cancelled (progress kept, it will resume)"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": str(dest), "tag": asset.get("tag"),
            "size": asset["size"]}


def apply_patch(path: str) -> dict:
    """Replace the on-disk .py layer with the patch contents.

    Backs the current files up first and restores them if anything fails, so a
    bad or truncated patch leaves a working install rather than a half-updated
    one. Only paths inside the code directory are written, so a crafted archive
    can't escape and drop files elsewhere."""
    import zipfile
    import shutil
    base = code_dir()
    if not base:
        return {"ok": False, "error": "this build isn't patchable"}
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "patch file not found"}
    backup = os.path.join(base, "_patch_backup")
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            # Refuse absolute paths and traversal before writing anything.
            for n in names:
                p = os.path.normpath(os.path.join(base, n))
                if not p.startswith(os.path.normpath(base) + os.sep):
                    return {"ok": False, "error": f"patch tried to write outside the app: {n}"}
            shutil.rmtree(backup, ignore_errors=True)
            os.makedirs(backup, exist_ok=True)
            for n in names:                       # snapshot what we're replacing
                cur = os.path.join(base, n)
                if os.path.isfile(cur):
                    dst = os.path.join(backup, n)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(cur, dst)
            try:
                for n in names:
                    z.extract(n, base)
            except Exception as e:                # roll back to the snapshot
                for n in names:
                    b = os.path.join(backup, n)
                    if os.path.isfile(b):
                        shutil.copy2(b, os.path.join(base, n))
                return {"ok": False, "error": f"patch failed, rolled back: {e}"}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "patch archive is corrupt; download it again"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "files": len(names)}


def restart_app() -> dict:
    """Relaunch Hearth and exit, so the freshly patched code is loaded."""
    import subprocess
    import threading
    import time as _t

    def _go():
        # os._exit(0) alone only ends the process running the web backend. The
        # GUI lives in a separate window process and the built-in server is
        # another child, so both survived and the user ended up staring at a
        # dead window next to the freshly launched one. Tear the tree down the
        # same way tray Quit does, THEN start the replacement.
        try:
            from . import llmserver
            llmserver.stop_builtin()
        except Exception:
            pass
        try:
            import psutil as _ps
            for _ch in _ps.Process(os.getpid()).children(recursive=True):
                try:
                    _ch.kill()
                except Exception:
                    pass
        except Exception:
            pass
        # Spawn AFTER the cull. Launched first, the replacement is our own child
        # and the loop above would kill the thing we just started.
        try:
            exe = sys.executable
            flags = 0
            if sys.platform == "win32":
                flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                         | 0x00000008)  # DETACHED_PROCESS
            subprocess.Popen([exe], creationflags=flags, close_fds=True)
        except Exception:
            return
        _t.sleep(0.6)
        os._exit(0)

    threading.Thread(target=_go, daemon=True).start()
    return {"ok": True, "restarting": True}


def _short_notes(body: str, limit: int = 900) -> str:
    """Opening paragraphs of a release body, always ending on a whole one.

    A flat [:600] slice cut mid-word, so the update dialog showed things like
    "Most of this r" and then jumped to the next paragraph. Break on blank
    lines instead: better to show two clean paragraphs than three and a stump.
    """
    text = (body or "").strip()
    if not text:
        return ""
    kept, total = [], 0
    for para in text.split("\n\n"):
        p = para.strip()
        if not p:
            continue
        if kept and total + len(p) > limit:
            break
        kept.append(p)
        total += len(p) + 2
    if kept:
        return "\n\n".join(kept)
    # Single paragraph longer than the limit: cut on a word, never mid-word.
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "..."


def check_for_update(current: str = "", timeout: float = 6.0) -> dict:
    """Query the latest GitHub release. Returns a dict:
       {ok, available, latest, current, url, notes}  or  {ok: False, error}.
    Network-failure-safe: never raises."""
    current = current or HEARTH_VERSION
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Hearth/0.7",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    tag = (data.get("tag_name") or "").strip()
    if not tag:
        return {"ok": True, "available": False, "current": current,
                "note": "no published releases yet"}
    return {
        "ok": True,
        "available": _norm(tag) > _norm(current),
        "latest": tag,
        "current": current,
        "url": data.get("html_url", f"https://github.com/{REPO}/releases"),
        "notes": _short_notes(data.get("body") or ""),
    }


_dl_cancel = False


def cancel_installer_download() -> dict:
    """Stop an in-flight installer download. The .part is kept so a later
    attempt resumes instead of restarting the (large) download."""
    global _dl_cancel
    _dl_cancel = True
    return {"ok": True}


def installer_asset(tag: str = "") -> dict:
    """The release asset matching THIS build's edition (Full vs Lite).
    Returns {name, url, size} or {error}. Downloading the wrong edition would
    silently swap the user's build, so the edition match is not optional."""
    import urllib.request
    from . import edition
    api = (f"https://api.github.com/repos/{REPO}/releases/"
           + (f"tags/{tag}" if tag else "latest"))
    try:
        req = urllib.request.Request(api, headers={
            "User-Agent": "Hearth", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.load(r)
    except Exception as e:
        return {"error": f"couldn't reach GitHub releases: {e}"}
    lite = edition.is_lite()
    for a in rel.get("assets") or []:
        n = (a.get("name") or "")
        if not n.lower().endswith(".exe"):
            continue
        is_lite_asset = "-lite-" in n.lower()
        if is_lite_asset == lite:
            return {"name": n, "url": a.get("browser_download_url"),
                    "size": int(a.get("size") or 0), "tag": rel.get("tag_name")}
    return {"error": f"no {edition.label()} installer in the latest release"}


def download_installer(on_progress=None, tag: str = "") -> dict:
    """Download the matching installer with resume + cancel, into
    ~/Downloads. Returns {ok, path} or {ok: False, error/cancelled}.

    Reuses llmserver's resumable downloader, so an interrupted pull continues
    instead of restarting, and a truncated file is never treated as complete —
    which matters a lot for a ~1 GB installer on a flaky link."""
    global _dl_cancel
    _dl_cancel = False
    asset = installer_asset(tag)
    if asset.get("error"):
        return {"ok": False, "error": asset["error"]}
    from pathlib import Path
    from .llmserver import _download_with_resume, DownloadCancelled
    dest_dir = Path(os.path.expanduser("~/Downloads"))
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        dest_dir = Path(os.path.expanduser("~"))
    dest = dest_dir / asset["name"]
    if dest.exists() and dest.stat().st_size == asset["size"]:
        return {"ok": True, "path": str(dest), "already": True}
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        _download_with_resume(asset["url"], dest, tmp, on_progress,
                              ua="Hearth-updater",
                              cancel=lambda: _dl_cancel)
    except DownloadCancelled:
        return {"ok": False, "cancelled": True,
                "error": "download cancelled (progress kept, it will resume)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": str(dest), "tag": asset.get("tag")}


def launch_installer(path: str, restart: bool = True) -> dict:
    """Hand off to the downloaded installer and step out of its way.

    Windows won't let the installer replace Hearth.exe or its DLLs while this
    process still holds them open, so simply spawning the installer from a
    running Hearth leaves it to fail or silently skip files. The handoff is:
    spawn DETACHED (so the installer outlives us), give it a moment to start,
    then exit. Inno relaunches Hearth itself when it finishes, so the user gets
    the app back on the new version without doing anything.
    """
    import subprocess
    import threading
    import time as _t
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "installer not found (download it first)"}

    def _handoff():
        try:
            flags = 0
            if sys.platform == "win32":
                flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                         | 0x00000008)  # DETACHED_PROCESS
            # /SILENT keeps it to a progress window instead of re-asking every
            # install question; Inno's CloseApplications + RestartApplications
            # then reopen Hearth once files are replaced.
            subprocess.Popen([path, "/SILENT", "/NORESTART"],
                             creationflags=flags, close_fds=True)
        except Exception:
            # Fall back to a plain launch — better a visible installer than none.
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except Exception:
                return
        if restart:
            # Release our file locks so the installer can overwrite them.
            _t.sleep(1.0)
            os._exit(0)

    threading.Thread(target=_handoff, daemon=True).start()
    return {"ok": True, "exiting": restart}


def apply_update() -> str:
    """Update in place. Only meaningful for a git checkout; packaged builds
    download the matching installer via download_installer(). Returns a
    human-readable status line."""
    if not is_git_checkout():
        return (f"This is a packaged build — download the latest release from "
                f"https://github.com/{REPO}/releases and run the installer.")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(
            ["git", "-C", root, "pull", "--ff-only"],
            capture_output=True, text=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0:
            tail = (out.stdout or "").strip()
            if "Already up to date" in tail:
                return "Already up to date."
            return "Updated. Restart Hearth to load the new version.\n" + tail
        return ("git pull failed (local changes? not a clean checkout?):\n"
                + (out.stderr or out.stdout or "").strip())
    except Exception as e:
        return f"update failed: {type(e).__name__}: {e}"
