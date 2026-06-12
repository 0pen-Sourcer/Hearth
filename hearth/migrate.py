"""Migrate memory from other local-AI agents into Hearth's store.

Sources implemented:
  - hermes   : ~/.hermes/memories/USER.md + MEMORY.md  (entries split by \\n§\\n)
  - openclaw : ~/.openclaw/workspace/MEMORY.md + memory/YYYY-MM-DD*.md
  - md       : any markdown file with one fact per bullet (universal fallback)

Each entry becomes one Hearth memory file under ~/Jarvis/memory/.
Hearth's regex sub-category classifier picks the bucket on save, so migrated
facts integrate with the graph view + sibling-recall immediately.

Optional flags:
  --include-config : pull the source agent's chosen model/provider into
                     ~/Jarvis/settings.json. API keys are NEVER copied —
                     migrator stays away from .env / secrets.json for safety.
  --include-skills : copy SKILL.md directories into ~/Jarvis/imported_skills/
                     for later review. Hearth has no skill loader yet so they
                     are parked, not registered.

Usage:
    python -m hearth.migrate --from hermes                          # dry-run
    python -m hearth.migrate --from hermes --apply                  # write
    python -m hearth.migrate --from openclaw --apply --include-skills
    python -m hearth.migrate --from md --path notes.md --apply

Idempotent: existing memory titles are updated rather than duplicated
(see hearth.memory.save slug collision handling).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hearth.memory import save  # noqa: E402


# ---------------------------------------------------------------------------
# Source-locating helpers
# ---------------------------------------------------------------------------

def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _hermes_active_memory_dir(home: Path) -> Path:
    """If a profile is active, memories live under profiles/<name>/memories."""
    profile_marker = home / "active_profile"
    if profile_marker.is_file():
        try:
            name = profile_marker.read_text(encoding="utf-8").strip()
            cand = home / "profiles" / name / "memories"
            if cand.is_dir():
                return cand
        except OSError:
            pass
    return home / "memories"


def _openclaw_state_dir() -> Path:
    return Path(os.environ.get("OPENCLAW_STATE_DIR") or (Path.home() / ".openclaw"))


def _openclaw_workspace_dir() -> Path:
    env = os.environ.get("OPENCLAW_WORKSPACE_DIR")
    if env:
        return Path(env)
    profile = os.environ.get("OPENCLAW_PROFILE", "").strip()
    base = _openclaw_state_dir() / (f"workspace-{profile}" if profile else "workspace")
    return base


# ---------------------------------------------------------------------------
# Hermes parser  (USER.md / MEMORY.md, entries split by \n§\n)
# ---------------------------------------------------------------------------

def _hermes_entries(text: str) -> List[str]:
    """Hermes uses literal '\\n§\\n' as the entry delimiter (tools/memory_tool.py:57).
    Returns each non-empty stripped entry."""
    if not text:
        return []
    parts = text.split("\n§\n")
    return [p.strip() for p in parts if p.strip()]


def _collect_hermes(home: Path) -> List[Tuple[str, str]]:
    """Return [(mtype, text), ...] for Hermes USER.md + MEMORY.md."""
    mem_dir = _hermes_active_memory_dir(home)
    out: List[Tuple[str, str]] = []
    user_file = mem_dir / "USER.md"
    if user_file.is_file():
        try:
            for entry in _hermes_entries(user_file.read_text(encoding="utf-8", errors="replace")):
                out.append(("user", entry))
        except OSError:
            pass
    mem_file = mem_dir / "MEMORY.md"
    if mem_file.is_file():
        try:
            for entry in _hermes_entries(mem_file.read_text(encoding="utf-8", errors="replace")):
                out.append(("project", entry))
        except OSError:
            pass
    return out


# ---------------------------------------------------------------------------
# OpenClaw parser  (MEMORY.md sections + memory/YYYY-MM-DD*.md bullets)
# ---------------------------------------------------------------------------

_OC_DAILY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(-.+)?\.md$")
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _oc_split_sections(text: str) -> List[Tuple[str, str]]:
    """Split MEMORY.md into (heading, body) chunks at H2 boundaries. Content
    before the first H2 is given the synthetic heading 'overview'."""
    text = _strip_frontmatter(text)
    matches = list(_H2_RE.finditer(text))
    out: List[Tuple[str, str]] = []
    if not matches:
        body = text.strip()
        if body:
            out.append(("overview", body))
        return out
    if matches[0].start() > 0:
        head = text[: matches[0].start()].strip()
        if head:
            out.append(("overview", head))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            out.append((heading, body))
    return out


def _oc_bullets(text: str) -> List[str]:
    """Pull bulleted lines out of a daily note. Multi-line bullets are
    NOT joined — each line is its own entry. Hearth's classifier groups
    them via sub_category anyway."""
    out: List[str] = []
    for ln in _strip_frontmatter(text).splitlines():
        m = _BULLET_RE.match(ln)
        if m:
            t = m.group(1).strip()
            if len(t) >= 8:
                out.append(t)
    return out


def _collect_openclaw(workspace: Path) -> List[Tuple[str, str, str]]:
    """Return [(mtype, title_hint, text), ...]. title_hint is the H2 heading
    or daily filename — _title_for refines it."""
    out: List[Tuple[str, str, str]] = []
    mem_file = workspace / "MEMORY.md"
    if mem_file.is_file():
        try:
            for heading, body in _oc_split_sections(
                    mem_file.read_text(encoding="utf-8", errors="replace")):
                # Each H2 section is one memory; if the section body is a list
                # of bullets, fan it out so the classifier can bucket each.
                bullets = _oc_bullets(body)
                if bullets:
                    for b in bullets:
                        out.append(("user", heading, b))
                else:
                    out.append(("user", heading, body))
        except OSError:
            pass
    daily_dir = workspace / "memory"
    if daily_dir.is_dir():
        for fn in sorted(os.listdir(daily_dir)):
            if not _OC_DAILY_RE.match(fn):
                continue
            full = daily_dir / fn
            try:
                for bullet in _oc_bullets(full.read_text(encoding="utf-8", errors="replace")):
                    out.append(("user", fn[:-3], bullet))  # date as title-hint
            except OSError:
                continue
    return out


# ---------------------------------------------------------------------------
# Universal markdown fallback
# ---------------------------------------------------------------------------

def _collect_md(path: Path) -> List[Tuple[str, str, str]]:
    """Pull bullets out of any markdown file, using the nearest H1/H2 as hint."""
    out: List[Tuple[str, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    current = ""
    for ln in _strip_frontmatter(text).splitlines():
        h = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", ln)
        if h:
            current = h.group(1).strip()
            continue
        b = _BULLET_RE.match(ln)
        if b:
            t = b.group(1).strip()
            if len(t) >= 8:
                out.append(("user", current, t))
    return out


# ---------------------------------------------------------------------------
# Title / classification
# ---------------------------------------------------------------------------

def _title_for(hint: str, text: str) -> str:
    """Short slug-friendly title. Bolded key wins; else hint + first words."""
    m = re.match(r"\*\*([^*]+)\*\*\s*[:\-]\s*(.+)", text)
    if m:
        return m.group(1).strip().lower()
    words = re.findall(r"[\w']+", text)[:6]
    base = " ".join(w.lower() for w in words) if words else "imported"
    if hint:
        return f"{hint.lower().strip()} - {base}"
    return base


# ---------------------------------------------------------------------------
# Skills + config helpers
# ---------------------------------------------------------------------------

def _copy_skills(roots: List[Path]) -> int:
    """Park skill dirs under ~/Jarvis/imported_skills/<source>/<name>/.
    Hearth has no skill loader yet; this preserves them for later review."""
    target_root = Path.home() / "Jarvis" / "imported_skills"
    count = 0
    for root in roots:
        if not root.is_dir():
            continue
        for skill_dir in root.iterdir():
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                # Hermes allows category subdirs (skills/<cat>/<name>/SKILL.md)
                for sub in skill_dir.iterdir() if skill_dir.is_dir() else []:
                    sub_md = sub / "SKILL.md"
                    if sub_md.is_file():
                        dst = target_root / skill_dir.name / sub.name
                        try:
                            if dst.exists():
                                shutil.rmtree(dst)
                            shutil.copytree(sub, dst)
                            count += 1
                        except OSError:
                            pass
                continue
            dst = target_root / skill_dir.name
            try:
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(skill_dir, dst)
                count += 1
            except OSError:
                pass
    return count


def _maybe_import_config(source: str) -> Optional[dict]:
    """Read the source agent's chosen model/provider so Hearth can preset
    settings. Returns a dict to merge into ~/Jarvis/settings.json, or None
    when nothing useful is found. API keys are skipped on purpose."""
    if source == "hermes":
        cfg_path = _hermes_home() / "config.yaml"
        if not cfg_path.is_file():
            return None
        try:
            import yaml  # type: ignore
        except ImportError:
            return {"_warning": "pyyaml not installed; install it to import hermes config.yaml"}
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return {"_error": f"yaml parse: {e}"}
        out: dict = {}
        model = (data.get("model") or {})
        if isinstance(model, dict):
            if model.get("name"):     out["llm_model"] = str(model["name"])
            if model.get("provider"): out["llm_provider"] = str(model["provider"])
            if model.get("base_url"): out["llm_url"]      = str(model["base_url"])
        return out or None
    if source == "openclaw":
        cfg_path = _openclaw_state_dir() / "openclaw.json"
        if not cfg_path.is_file():
            return None
        try:
            import json5  # type: ignore
            data = json5.loads(cfg_path.read_text(encoding="utf-8"))
        except ImportError:
            # Best-effort plain JSON (works if no comments/trailing commas)
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                return {"_warning": "openclaw.json is JSON5; install json5 to parse it"}
        except Exception as e:
            return {"_error": f"openclaw.json parse: {e}"}
        out = {}
        agents = (data.get("agents") or {})
        defaults = (agents.get("defaults") or {}) if isinstance(agents, dict) else {}
        prov = defaults.get("provider")
        model = defaults.get("model")
        if prov:  out["llm_provider"] = str(prov)
        if model: out["llm_model"]    = str(model)
        return out or None
    return None


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m hearth.migrate")
    ap.add_argument("--from", dest="source", required=True,
                    choices=["openclaw", "hermes", "md"])
    ap.add_argument("--path", help="Override the source path (required for md).")
    ap.add_argument("--apply", action="store_true",
                    help="Write to ~/Jarvis/memory. Without this, dry-run.")
    ap.add_argument("--include-skills", action="store_true",
                    help="Copy SKILL.md dirs into ~/Jarvis/imported_skills/ "
                         "for later review. Hearth has no skill loader yet.")
    ap.add_argument("--include-config", action="store_true",
                    help="Pull the source agent's chosen model/provider into "
                         "~/Jarvis/settings.json. API keys are NOT copied.")
    args = ap.parse_args()

    # ---- collect entries ----
    entries: List[Tuple[str, str, str]] = []  # (mtype, title_hint, text)
    skill_roots: List[Path] = []

    if args.source == "hermes":
        home = _hermes_home()
        if not home.is_dir():
            print(f"no hermes home at {home}; set HERMES_HOME or pass --path "
                  f"(point to a directory containing memories/USER.md)")
            return 1
        target_home = Path(args.path) if args.path else home
        for mtype, text in _collect_hermes(target_home):
            entries.append((mtype, "", text))
        skill_roots = [target_home / "skills"]
        print(f"reading hermes memory under {target_home}")

    elif args.source == "openclaw":
        ws = Path(args.path) if args.path else _openclaw_workspace_dir()
        if not ws.is_dir():
            print(f"no openclaw workspace at {ws}; set OPENCLAW_WORKSPACE_DIR "
                  f"or pass --path")
            return 1
        entries = _collect_openclaw(ws)
        skill_roots = [ws / "skills", _openclaw_state_dir() / "skills"]
        print(f"reading openclaw workspace under {ws}")

    else:  # md
        if not args.path:
            print("--path required for source=md")
            return 1
        p = Path(args.path)
        if not p.is_file():
            print(f"not a file: {p}")
            return 1
        entries = _collect_md(p)
        print(f"reading markdown file {p}")

    if not entries:
        print("no entries found, nothing to migrate.")
        return 0

    print(f"found {len(entries)} candidate memor{'y' if len(entries) == 1 else 'ies'}.")
    print()

    # ---- save / dry-run ----
    written, updated, errors = 0, 0, 0
    for mtype, hint, text in entries:
        title = _title_for(hint, text)
        description = text if len(text) <= 120 else text[:120].rstrip() + "..."
        body = text if len(text) > 120 else ""
        if args.apply:
            try:
                msg = save(title=title, mtype=mtype,
                           description=description, body=body,
                           tags=[f"src:{args.source}"])
                if "updated" in msg:
                    updated += 1
                else:
                    written += 1
            except Exception as e:
                print(f"  ! {title}: {type(e).__name__}: {e}")
                errors += 1
        else:
            print(f"  . would save: [{mtype}] {title}: {description}")

    print()

    # ---- skills (opt-in) ----
    skills_copied = 0
    if args.include_skills and skill_roots and args.apply:
        skills_copied = _copy_skills(skill_roots)

    # ---- config (opt-in) ----
    cfg_diff: Optional[dict] = None
    if args.include_config:
        cfg_diff = _maybe_import_config(args.source)
        if args.apply and cfg_diff and not any(k.startswith("_") for k in cfg_diff):
            settings_path = Path.home() / "Jarvis" / "settings.json"
            try:
                existing = {}
                if settings_path.is_file():
                    existing = json.loads(settings_path.read_text(encoding="utf-8"))
                existing.update(cfg_diff)
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(json.dumps(existing, indent=2),
                                         encoding="utf-8")
            except Exception as e:
                print(f"  ! settings.json: {e}")

    # ---- summary ----
    print("== summary ==")
    print(f"  source:        {args.source}")
    print(f"  entries seen:  {len(entries)}")
    if args.apply:
        print(f"  new memories:  {written}")
        print(f"  updated:       {updated}")
        print(f"  errors:        {errors}")
        if args.include_skills:
            print(f"  skills parked: {skills_copied}  (under ~/Jarvis/imported_skills/)")
        if cfg_diff:
            if any(k.startswith("_") for k in cfg_diff):
                print(f"  config import: {cfg_diff}")
            else:
                print(f"  config import: {cfg_diff} -> settings.json")
        print()
        print("open ~/Jarvis/memory or the GUI Memory tab to browse.")
    else:
        if args.include_skills:
            print(f"  would scan skills under: {[str(p) for p in skill_roots]}")
        if cfg_diff:
            print(f"  would import config: {cfg_diff}")
        print()
        print("dry-run only. re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
