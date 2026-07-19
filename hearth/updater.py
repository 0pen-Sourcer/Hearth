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

REPO = os.environ.get("HEARTH_UPDATE_REPO", "0pen-sourcer/hearth")

# Single source of truth for the shipped version. Callers must NOT hardcode it -
# a stale copy makes the update prompt fire forever after the user updates.
HEARTH_VERSION = "0.7.0-preview"


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
        "notes": (data.get("body") or "").strip()[:600],
    }


def apply_update() -> str:
    """Update in place. Only meaningful for a git checkout; packaged builds are
    pointed at the release download. Returns a human-readable status line."""
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
