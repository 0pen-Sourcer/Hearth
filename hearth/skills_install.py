"""Install shareable skills from a GitHub repo or a local path.

This is the distribution half of the skills system (authoring lives in
skills_loader.create_skill). A skill is just a folder with a SKILL.md, so
"installing" one is: fetch it, validate the SKILL.md, show the user what it can
do, get consent, drop it into ~/Jarvis/skills/<slug>/.

Sources accepted:
    owner/repo                      -> GitHub default branch
    owner/repo@branch               -> a specific branch/tag
    owner/repo/path/to/skill        -> a subfolder inside the repo
    https://github.com/owner/repo   -> same, URL form
    git:owner/repo                  -> same, git: prefix form
    C:\\path\\to\\skill              -> a local folder (copied in)

SAFETY MODEL — be honest about it. A skill runs with the SAME access as the
agent: its SKILL.md is prose the model follows, and any scripts/ it ships are
run via run_command. There is NO sandbox. So the manifest is *disclosure +
consent*, not enforcement: we surface the declared tools + shipped scripts and
make the user say yes before anything lands on disk. Treat an installed skill
like any other code you downloaded from the internet.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import skills_loader as _sl

# Tools that let a skill change the machine / reach the network. Used only to
# describe risk at install time — not to block anything at runtime.
_RISKY_TOOLS = {
    "run_command", "write_file", "edit_file", "delete_path", "move_path",
    "create_directory", "open_url", "open_app", "browse", "browse_click",
    "browse_type", "create_plugin", "install_skill", "set_reminder",
    "forge_generate", "send_email",
}

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,40}$")
# owner/repo, owner/repo@ref, owner/repo/sub/dir, optionally @ref on the end.
_GH_RE = re.compile(
    r"^(?:https?://github\.com/|git:)?"
    r"(?P<owner>[A-Za-z0-9][\w.-]*)/(?P<repo>[A-Za-z0-9][\w.-]*?)"
    r"(?:/(?P<sub>[^@]+?))?"
    r"(?:@(?P<ref>[\w.\-/]+))?/?$"
)


def _is_local(source: str) -> bool:
    s = source.strip().strip('"').strip("'")
    return os.path.sep in s and Path(os.path.expanduser(s)).exists() \
        or Path(os.path.expanduser(s)).exists()


def _download_github(owner: str, repo: str, ref: Optional[str]) -> Path:
    """Download + extract a GitHub repo zipball to a temp dir. Returns the
    extracted repo root. Tries the given ref, else main then master."""
    repo = repo[:-4] if repo.endswith(".git") else repo
    refs = [ref] if ref else ["main", "master"]
    last_err: Optional[Exception] = None
    for r in refs:
        url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{r}"
        # tags live under refs/tags — fall back to that if a branch 404s
        for u in (url, f"https://codeload.github.com/{owner}/{repo}/zip/refs/tags/{r}"):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Hearth-skill-install"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                zf = zipfile.ZipFile(io.BytesIO(data))
                tmp = Path(tempfile.mkdtemp(prefix="hearth-skill-"))
                zf.extractall(tmp)
                # GitHub wraps everything in <repo>-<ref>/
                roots = [p for p in tmp.iterdir() if p.is_dir()]
                return roots[0] if roots else tmp
            except Exception as e:  # noqa: BLE001 - try the next ref/url
                last_err = e
                continue
    raise RuntimeError(
        f"could not download {owner}/{repo} (tried {', '.join(refs)}): {last_err}")


def _resolve_source(source: str) -> Path:
    """Fetch `source` to a local directory and return the path that should
    contain the skill (repo root or the named subfolder)."""
    s = source.strip().strip('"').strip("'")
    if _is_local(s):
        return Path(os.path.expanduser(s)).resolve()
    m = _GH_RE.match(s)
    if not m:
        raise ValueError(
            "source must be a GitHub repo (owner/repo[/subdir][@ref]) or a "
            "local folder path")
    root = _download_github(m.group("owner"), m.group("repo"), m.group("ref"))
    sub = m.group("sub")
    return (root / sub) if sub else root


def _find_skill_root(start: Path) -> Optional[Path]:
    """Find the folder that holds SKILL.md: `start` itself, or a single
    obvious subfolder (handles repos that wrap the skill one level deep)."""
    if (start / "SKILL.md").is_file():
        return start
    if not start.is_dir():
        return None
    subdirs = [p for p in start.iterdir()
               if p.is_dir() and not p.name.startswith((".", "_"))]
    with_md = [d for d in subdirs if (d / "SKILL.md").is_file()]
    if len(with_md) == 1:
        return with_md[0]
    return None


def inspect_source(source: str) -> Dict[str, Any]:
    """Fetch + parse a skill WITHOUT installing. Returns a manifest the caller
    shows the user for consent: name, description, declared tools, the scripts
    it ships, and a risk flag. Leaves the fetched copy in a temp dir
    (`_staged`) so install_from_staged can move it without re-downloading."""
    try:
        fetched = _resolve_source(source)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    root = _find_skill_root(fetched)
    if not root:
        return {"ok": False, "error":
                "no SKILL.md found (skill folders need a SKILL.md at the root)"}
    parsed = _sl._parse_skill_md(root / "SKILL.md")
    if not parsed:
        return {"ok": False, "error": "SKILL.md is malformed (no YAML frontmatter)"}
    slug = (parsed.get("name") or root.name).strip().lower()
    if not _SLUG_RE.match(slug):
        return {"ok": False, "error": f"invalid skill name '{slug}'"}
    if not (parsed.get("description") or "").strip():
        return {"ok": False, "error": "SKILL.md has no description"}

    declared = parsed.get("tools") or parsed.get("permissions") or []
    if isinstance(declared, str):
        declared = [declared]
    scripts: List[str] = []
    sdir = root / "scripts"
    if sdir.is_dir():
        scripts = sorted(p.name for p in sdir.iterdir()
                         if p.is_file() and not p.name.startswith("."))
    risky = sorted(set(declared) & _RISKY_TOOLS)
    # Shipping executable scripts is itself a "runs code" signal even if no
    # risky tool is declared.
    ships_code = bool(scripts)
    existing = {s["name"]: s for s in _sl.list_skills()}
    return {
        "ok": True,
        "name": slug,
        "description": parsed.get("description", "").strip(),
        "version": parsed.get("version", "").strip(),
        "author": parsed.get("author", "").strip(),
        "declared_tools": declared,
        "risky_tools": risky,
        "scripts": scripts,
        "ships_code": ships_code,
        "risky": bool(risky or ships_code),
        "already_installed": slug in existing,
        "shadows_bundled": existing.get(slug, {}).get("source") == "bundled",
        "_staged": str(root),
    }


def install_from_staged(manifest: Dict[str, Any], *, force: bool = False) -> Dict[str, Any]:
    """Move an already-inspected skill (manifest['_staged']) into the user
    skills dir. Call only after the user has consented to the manifest."""
    slug = manifest.get("name")
    staged = manifest.get("_staged")
    if not slug or not staged or not Path(staged).is_dir():
        return {"ok": False, "error": "nothing staged to install"}
    dest = _sl._USER_SKILLS_DIR / slug
    if dest.exists():
        if not force:
            return {"ok": False, "error":
                    f"'{slug}' is already installed at {dest}. Pass force=True to overwrite."}
        shutil.rmtree(dest, ignore_errors=True)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged, dest)
    except OSError as e:
        return {"ok": False, "error": f"copy failed: {e}"}
    return {"ok": True, "name": slug, "folder": str(dest),
            "next": f"call load_skill('{slug}') to use it"}


def install_skill(source: str, *,
                  consent: Optional[Callable[[Dict[str, Any]], bool]] = None,
                  force: bool = False) -> Dict[str, Any]:
    """Full install flow: inspect -> consent -> install.

    consent: called with the manifest; return True to proceed. If None, the
    install proceeds for non-risky skills but is REFUSED for risky ones (so a
    headless/automated path can never silently install something that runs
    code without an explicit consent callback).
    """
    manifest = inspect_source(source)
    if not manifest.get("ok"):
        return manifest
    if consent is not None:
        if not consent(manifest):
            return {"ok": False, "error": "install declined", "name": manifest["name"]}
    elif manifest.get("risky"):
        return {"ok": False, "name": manifest["name"], "manifest": manifest,
                "error": "this skill runs code / uses risky tools and no consent "
                         "callback was provided — refusing to auto-install. "
                         "Review it and confirm explicitly."}
    return install_from_staged(manifest, force=force or manifest.get("already_installed", False) and force)


def uninstall_skill(name: str) -> Dict[str, Any]:
    """Remove a user-installed skill. Bundled skills can't be removed (they're
    part of the app); a same-named user skill shadowing one is removable.

    SOFT delete: the folder is MOVED to skills/_trash/<name>_<timestamp> rather
    than hard-deleted, so an accidental remove is recoverable (rmtree bypasses
    the Recycle Bin — a hard delete here is unrecoverable)."""
    import time as _time
    slug = (name or "").strip().lower()
    dest = _sl._USER_SKILLS_DIR / slug
    if not dest.is_dir():
        return {"ok": False, "error": f"no user-installed skill named '{slug}'"}
    trash = _sl._USER_SKILLS_DIR / "_trash"
    try:
        trash.mkdir(parents=True, exist_ok=True)
        grave = trash / f"{slug}_{int(_time.time())}"
        shutil.move(str(dest), str(grave))
    except Exception as e:
        return {"ok": False, "error": f"remove failed: {e}"}
    return {"ok": True, "name": slug, "trashed_to": str(grave),
            "note": "moved to skills/_trash — restore by moving it back"}
