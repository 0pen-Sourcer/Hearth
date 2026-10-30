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
HEARTH_VERSION = "0.7.6-preview"


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
            "User-Agent": "Inglen", "Accept": "application/vnd.github+json"})
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
                                  ua="Inglen-updater", cancel=lambda: _dl_cancel)
        except DownloadCancelled:
            return {"ok": False, "cancelled": True,
                    "error": "cancelled (progress kept, it will resume)"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": str(dest), "tag": asset.get("tag"),
            "size": asset["size"]}


# Components a repair can and cannot restore. Kept beside repair() so the two
# never disagree about what the patch actually carries.
# Checked file by file: a folder that exists can still be missing the one file
# that starts the app.
_REPAIRABLE = [
    ("Inglen code", ["hearth/__init__.py", "hearth/web.py", "hearth/tools.py",
                     "hearth/headless.py", "hearth/ui.html", "hearth/room.js", "hearth/call_audio.js",
                     "hearth_cli.py"],
     "the app itself"),
    ("Skills and helpers", ["hearth/skills", "hearth/subagents"],
     "file-making skills and the helper agents' instructions"),
    ("Icons and art", ["assets/icon.png", "assets/mark.svg", "assets/team/manifest.json"],
     "the logo, tray icon and Team room"),
    ("Speech output", ["kokoro_onnx", "espeakng_loader"], "voice replies"),
    ("Speech input", ["faster_whisper", "RealtimeSTT", "silero_vad"], "the mic"),
    ("Audio devices", ["_sounddevice_data", "pyaudio"], "playback and capture"),
    ("Numerics", ["torch", "scipy", "numpy.libs"], "shared by both"),
]
# Present in the installer but too large for a code patch, so a repair leaves
# them alone and says so instead of reporting success over a broken install.
_UNPATCHABLE = [
    ("Speech runtime", ["onnxruntime"], "reinstall to restore"),
    ("Transcribe runtime", ["ctranslate2"], "reinstall to restore"),
    ("Window", ["webview", "clr_loader"], "reinstall to restore"),
    ("Desktop control", ["uiautomation", "comtypes"], "reinstall to restore"),
    ("Web browsing", ["playwright"], "reinstall to restore"),
]


def _engine_ok() -> bool:
    """A usable built-in engine exists, shipped with the app or downloaded later."""
    try:
        from . import llmserver
        return bool(llmserver.find_native_llama_server())
    except Exception:
        return False


def _needs_engine() -> bool:
    """Full ships with an engine. Lite doesn't, but someone who added one
    later has it repaired too."""
    try:
        from . import edition
        if not edition.is_lite():
            return True
    except Exception:
        return True
    root = os.path.join(os.path.expanduser("~"), ".hearth", "llamacpp")
    try:
        return any(os.path.isdir(os.path.join(root, d)) and not d.startswith(".") for d in os.listdir(root))
    except OSError:
        return False


def repair_plan() -> dict:
    """What a repair would touch, and what it cannot.

    Reads the install rather than guessing, so the user is shown the parts that
    are actually missing before anything downloads."""
    base = code_dir()
    if not base:
        return {"ok": False, "can_patch": False,
                "error": "this build keeps its code inside the executable, so it "
                         "cannot self-repair; reinstall the release to restore it",
                "items": []}

    def _state(mods):
        missing = [m for m in mods
                   if not os.path.exists(os.path.join(base, m.replace(".", os.sep)))
                   and not os.path.exists(os.path.join(base, m))]
        return missing

    items = []
    for label, mods, note in _REPAIRABLE:
        missing = _state(mods)
        items.append({"label": label, "note": note, "repairable": True,
                      "missing": missing, "ok": not missing})
    # The engine is not in the patch, but the same official build can be
    # downloaded again, so it is repairable rather than a reinstall.
    if _needs_engine():
        ok = _engine_ok()
        items.append({"label": "Model engine", "repairable": True,
                      "note": "runs local models (a larger download, about 600 MB)",
                      "missing": [] if ok else ["llama-server"], "ok": ok})
    for label, mods, note in _UNPATCHABLE:
        missing = _state(mods)
        items.append({"label": label, "note": note, "repairable": False,
                      "missing": missing, "ok": not missing})
    broken = [i["label"] for i in items if not i["ok"]]
    unfixable = [i["label"] for i in items if not i["ok"] and not i["repairable"]]
    return {"ok": True, "can_patch": True, "items": items,
            "broken": broken, "needs_reinstall": unfixable}


def repair(on_progress=None) -> dict:
    """Restore a fouled-up install by re-fetching THIS version's patch and
    re-applying it. The patch carries Hearth's code, skills, art and the loose
    voice stack, so a file deleted or corrupted in that layer comes back. A
    missing model engine is downloaded again from the official llama.cpp
    releases. onnxruntime, ctranslate2 and the window/desktop/browser libraries
    are too big to patch and need a full reinstall. apply_patch is atomic (it backs
    up and rolls back), so a failed repair leaves the install as it was."""
    if not can_patch():
        return {"ok": False, "error":
                "this build keeps its code inside the exe, so it can't self-repair; "
                "reinstall the release to restore it"}
    tag = HEARTH_VERSION if HEARTH_VERSION.startswith("v") else "v" + HEARTH_VERSION
    got = download_patch(on_progress=on_progress, tag=tag)
    if not got.get("ok"):
        # This version's release/patch may be gone — fall back to the latest.
        got = download_patch(on_progress=on_progress)
    if not got.get("ok"):
        return got
    r = apply_patch(got["path"])
    if not r.get("ok"):
        return r
    out = {"ok": True, "restored": r.get("files", 0),
           "note": "restored the app, art and voice files; restart Inglen to load them"}
    if _needs_engine() and not _engine_ok():
        try:
            from . import llmserver
            e = llmserver.download_llama_runtime(on_progress=on_progress, force=True)
        except Exception as ex:
            e = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
        if e.get("ok"):
            out["engine"] = e.get("tag")
            out["note"] = "restored the app and the model engine; restart Inglen to load them"
        else:
            out["engine_error"] = e.get("error") or "engine download failed"
            out["note"] = ("restored the app, but the model engine could not be "
                           f"downloaded: {out['engine_error']}")
    return out


# Written before a patch starts replacing files and removed once it has
# finished. Its presence at startup means the last attempt did not get to the
# end, which is what a power cut or a kill in the middle of an update leaves.
_INFLIGHT = "_patch_inflight.json"


def _backup_dir(base: str) -> str:
    return os.path.join(base, "_patch_backup")


def recover_interrupted_patch() -> dict:
    """Undo a patch that was cut off partway through, if one was.

    A rollback inside apply_patch only covers a failure it lives to see. Losing
    the process during the extract left files from two versions mixed together
    with nothing to notice or reverse it, and the install would not start. This
    runs at startup, restores everything the interrupted attempt had backed up,
    removes the files it had added, and clears the marker.
    """
    base = code_dir()
    if not base:
        return {"ok": True, "recovered": False}
    marker = os.path.join(base, _INFLIGHT)
    if not os.path.isfile(marker):
        return {"ok": True, "recovered": False}
    import shutil
    backup = _backup_dir(base)
    try:
        with open(marker, "r", encoding="utf-8") as f:
            rec = json.load(f) or {}
        restored, dropped = 0, 0
        for n in rec.get("replaced") or []:
            src = os.path.join(backup, n)
            if os.path.isfile(src):
                dst = os.path.join(base, n)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                restored += 1
        # Files the patch was adding have no earlier version to go back to, so
        # the only way back is to take them away again.
        for n in rec.get("added") or []:
            try:
                os.remove(os.path.join(base, n))
                dropped += 1
            except OSError:
                pass
        os.remove(marker)
        shutil.rmtree(backup, ignore_errors=True)
        print(f"[hearth.updater] recovered an interrupted update: "
              f"{restored} file(s) restored, {dropped} removed", flush=True)
        return {"ok": True, "recovered": True, "restored": restored, "removed": dropped}
    except Exception as e:
        return {"ok": False, "recovered": False, "error": f"{type(e).__name__}: {e}"}


def apply_patch(path: str) -> dict:
    """Replace the on-disk .py layer with the patch contents.

    Backs the current files up first and restores them if anything fails, so a
    bad or truncated patch leaves a working install rather than a half-updated
    one. Only paths inside the code directory are written, so a crafted archive
    can't escape and drop files elsewhere. A marker written alongside the backup
    covers the case this function cannot: losing the process mid-extract, which
    recover_interrupted_patch undoes at the next start.
    """
    import zipfile
    import shutil
    base = code_dir()
    if not base:
        return {"ok": False, "error": "this build isn't patchable"}
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "patch file not found"}
    # Nothing is written unless the patch is signed by a release key and every file matches its signed hash.
    from .patch_sign import check as _check, MANIFEST, SIGNATURE
    signed = _check(path, HEARTH_VERSION)
    if not signed.get("ok"):
        # A refused download isn't kept: the next try fetches it again instead of reusing it.
        if os.path.dirname(os.path.abspath(path)) == os.path.abspath(os.path.expanduser("~/.hearth/updates")):
            try:
                os.remove(path)
            except OSError:
                pass
        return {"ok": False, "error": signed["error"]}
    backup = _backup_dir(base)
    marker = os.path.join(base, _INFLIGHT)
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/") and n not in (MANIFEST, SIGNATURE)]
            # Refuse absolute paths and traversal before writing anything.
            for n in names:
                p = os.path.normpath(os.path.join(base, n))
                if not p.startswith(os.path.normpath(base) + os.sep):
                    return {"ok": False, "error": f"patch tried to write outside the app: {n}"}
            shutil.rmtree(backup, ignore_errors=True)
            os.makedirs(backup, exist_ok=True)
            replaced, added = [], []
            for n in names:                       # snapshot what we're replacing
                cur = os.path.join(base, n)
                if os.path.isfile(cur):
                    dst = os.path.join(backup, n)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(cur, dst)
                    replaced.append(n)
                else:
                    added.append(n)
            with open(marker, "w", encoding="utf-8") as f:
                json.dump({"replaced": replaced, "added": added,
                           "version": HEARTH_VERSION}, f)
            try:
                for n in names:
                    z.extract(n, base)
            except Exception as e:                # roll back to the snapshot
                for n in replaced:
                    b = os.path.join(backup, n)
                    if os.path.isfile(b):
                        shutil.copy2(b, os.path.join(base, n))
                for n in added:
                    try:
                        os.remove(os.path.join(base, n))
                    except OSError:
                        pass
                try:
                    os.remove(marker)
                except OSError:
                    pass
                return {"ok": False, "error": f"patch failed, rolled back: {e}"}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "patch archive is corrupt; download it again"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        os.remove(marker)
    except OSError:
        pass
    # Nothing is left to roll back once the patch is in, and the backup is the
    # size of everything it replaced (torch included, hundreds of MB).
    shutil.rmtree(backup, ignore_errors=True)
    _sync_registry_version()
    return {"ok": True, "files": len(names)}


def _sync_registry_version() -> None:
    """Point Windows' uninstall entry at the patched version. Inno only writes
    DisplayVersion at install time, so a patched install otherwise shows the OLD
    installer's version in Control Panel (e.g. stuck at 0.7.2 after patching to
    0.7.5). Best-effort, Windows-only; the Hearth AppId matches Hearth.iss."""
    if os.name != "nt":
        return
    key = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
           r"\{B7E2B6A4-1C9F-4F3D-9A6E-7E5C2F0A1D34}_is1")
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                k = winreg.OpenKey(root, key, 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, HEARTH_VERSION)
                # Inno bakes the version into the display NAME too ("Hearth version
                # 0.7.2-preview"), so sync it or Control Panel still reads old.
                winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ,
                                  f"Inglen version {HEARTH_VERSION}")
                winreg.CloseKey(k)
                return
            except FileNotFoundError:
                continue
            except Exception:
                continue
    except Exception:
        pass


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
            "User-Agent": "Inglen/0.7.6",
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


# Commit-message marker the maintainer puts in a commit to flag it for source
# (git-clone) users. Only flagged commits ever nudge — normal commits never do.
SOURCE_UPDATE_MARKER = "[upgradeable]"


def check_source_update(timeout: float = 5.0, marker: str = SOURCE_UPDATE_MARKER) -> dict:
    """For a git-clone (source) install: is there a newer commit on origin/main
    that the maintainer FLAGGED for source users (its message contains `marker`),
    and which this checkout doesn't have yet? Only flagged commits nudge — never
    every push. Returns {source, available, sha, subject}. Network/git-safe: a
    frozen or non-git build returns source=False.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if getattr(sys, "frozen", False) or not os.path.isdir(os.path.join(root, ".git")):
        return {"source": False}
    try:
        url = f"https://api.github.com/repos/{REPO}/commits?sha=main&per_page=30"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Inglen/0.7.6", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            commits = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"source": True, "available": False, "error": f"{type(e).__name__}: {e}"}
    flagged = next((c for c in commits
                    if marker.lower() in ((c.get("commit") or {}).get("message") or "").lower()),
                   None)
    if not flagged:
        return {"source": True, "available": False}
    sha = flagged.get("sha") or ""
    subject = (((flagged.get("commit") or {}).get("message") or "").splitlines() or [""])[0]
    subject = subject.replace(marker, "").strip(" -:") or "a flagged update"
    # Does this checkout already contain the flagged commit? (is-ancestor is 0 when
    # HEAD already has it; non-zero when it's missing or on another branch → behind.)
    try:
        import subprocess
        rc = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", sha, "HEAD"],
                            capture_output=True, timeout=timeout).returncode
        have_it = (rc == 0)
    except Exception:
        have_it = False
    return {"source": True, "available": not have_it, "sha": sha[:8], "subject": subject}


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
            "User-Agent": "Inglen", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.load(r)
    except Exception as e:
        return {"error": f"couldn't reach GitHub releases: {e}"}
    lite = edition.is_lite()
    assets = rel.get("assets") or []
    for a in assets:
        n = (a.get("name") or "")
        if not n.lower().endswith(".exe"):
            continue
        is_lite_asset = "-lite-" in n.lower()
        if is_lite_asset == lite:
            sig = next((s for s in assets if (s.get("name") or "") == n + ".sig"), {})
            return {"name": n, "url": a.get("browser_download_url"),
                    "size": int(a.get("size") or 0), "tag": rel.get("tag_name"),
                    # GitHub's own hash of the file it holds ("sha256:..."), when it gives one.
                    "digest": str(a.get("digest") or ""),
                    # The release key's signature over the installer (patch_sign.sign_installer).
                    "sig_url": str(sig.get("browser_download_url") or "")}
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
    sig = _installer_sig(asset)
    if not sig:
        return {"ok": False, "error": "this release's installer isn't signed by Inglen's release key, "
                                      "so it wasn't downloaded"}
    asset["sig"] = sig
    dest_dir = Path(os.path.expanduser("~/Downloads"))
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        dest_dir = Path(os.path.expanduser("~"))
    dest = dest_dir / asset["name"]
    if dest.exists() and dest.stat().st_size == asset["size"]:
        bad = _installer_refusal(str(dest), asset)
        if not bad:
            _VERIFIED.add(os.path.abspath(str(dest)))
            return {"ok": True, "path": str(dest), "already": True}
        try:
            dest.unlink()
        except OSError:
            pass
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        _download_with_resume(asset["url"], dest, tmp, on_progress,
                              ua="Inglen-updater",
                              cancel=lambda: _dl_cancel)
    except DownloadCancelled:
        return {"ok": False, "cancelled": True,
                "error": "download cancelled (progress kept, it will resume)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    bad = _installer_refusal(str(dest), asset)
    if bad:
        try:
            dest.unlink()
        except OSError:
            pass
        return {"ok": False, "error": bad}
    _VERIFIED.add(os.path.abspath(str(dest)))
    return {"ok": True, "path": str(dest), "tag": asset.get("tag")}


# Installers this run downloaded and checked; launch_installer runs no other file.
_VERIFIED: set = set()


def _installer_sig(asset: dict) -> str:
    """The release's <installer>.sig text, or '' when there is none."""
    url = asset.get("sig_url") or ""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Inglen-updater"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read(4096).decode("ascii", "replace").strip()
    except Exception:
        return ""


def _installer_refusal(path: str, asset: dict) -> str:
    """'' when the installer matches GitHub's hash and carries the release key's signature."""
    bad = _digest_mismatch(path, asset.get("digest", ""))
    if bad:
        return bad
    from .patch_sign import check_installer
    return check_installer(path, asset.get("name") or os.path.basename(path), asset.get("sig") or "")


def _digest_mismatch(path: str, digest: str) -> str:
    """'' when the file matches the hash GitHub gives for it (or none is given), else why not."""
    algo, _, want = (digest or "").partition(":")
    if algo.lower() != "sha256" or not want:
        return ""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest().lower() == want.strip().lower():
        return ""
    return "the downloaded installer doesn't match the release, so it wasn't kept; try again"


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
    if os.path.abspath(path) not in _VERIFIED:
        return {"ok": False, "error": "only an installer Inglen downloaded and checked runs from here"}

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
            return "Updated. Restart Inglen to load the new version.\n" + tail
        return ("git pull failed (local changes? not a clean checkout?):\n"
                + (out.stderr or out.stdout or "").strip())
    except Exception as e:
        return f"update failed: {type(e).__name__}: {e}"
