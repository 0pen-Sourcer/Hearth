"""Skills loader — discoverable markdown workflow bundles, distinct from plugins.

A SKILL is a folder under ~/Jarvis/skills/<name>/ containing:
  - SKILL.md   (required) — YAML frontmatter + markdown body
  - scripts/   (optional) — executable helpers (.py / .ps1 / .sh)
  - references/ (optional) — supplemental docs the model loads on demand
  - assets/    (optional) — templates, fonts, icons used in skill output

Frontmatter:
    ---
    name: <slug>
    description: <one-line when-to-use, model decides on this>
    version: 1.0.0          (optional)
    compatibility:          (optional)
      prerequisites: [...]  (optional)
    ---
    # body in markdown, model loads when invoked

Distinct from plugins.py:
  - Plugins extend the OpenAI tool surface (model writes a Python tool with
    its own schema). Best for "I need a NEW callable RIGHT NOW".
  - Skills are prose+asset bundles the model invokes via existing tools
    (run_command, write_file, etc.). Best for "I know HOW to make a PDF
    if I follow these 8 steps + use this template + this helper script".

Skills are LISTED in the system prompt (one line each: `name - description`)
so the model can ask to load_skill(<name>) — full body only enters context
when invoked. Keeps the always-on overhead tiny while making 50+ skills
available without bloating tool schemas.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Two search roots, in priority order:
#   1. User skills  ~/Jarvis/skills/   (writable, where new ones land)
#   2. Bundled      hearth/skills/     (ship-with-Hearth defaults)
# Same-name user skill shadows the bundled one (user wins).
_USER_SKILLS_DIR = Path(os.environ.get("JARVIS_WORKSPACE")
                        or (Path.home() / "Jarvis")) / "skills"
_BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_skill_md(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a SKILL.md. Returns frontmatter dict + body, or None if the
    file is malformed (no frontmatter / unreadable)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm_block, body = m.group(1), m.group(2)
    fm: Dict[str, Any] = {}
    # Cheap YAML parse — single-level keys + flat lists. Real skill files
    # only need name/description/version/etc. so we don't pull pyyaml.
    for ln in fm_block.split("\n"):
        if ":" not in ln or ln.startswith(" "):
            continue
        k, _, v = ln.partition(":")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [t.strip().strip('"\'') for t in v[1:-1].split(",") if t.strip()]
        else:
            fm[k] = v
    fm["body"] = body.strip()
    return fm


def _scan_dir(root: Path) -> List[Tuple[str, Path, Dict[str, Any]]]:
    """Walk `root` for skill folders. Returns [(slug, dir_path, parsed)]."""
    out: List[Tuple[str, Path, Dict[str, Any]]] = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            # Allow one nested category level (Hermes uses skills/<cat>/<name>/)
            for sub in entry.iterdir() if entry.is_dir() else []:
                if not sub.is_dir():
                    continue
                sub_md = sub / "SKILL.md"
                if sub_md.is_file():
                    parsed = _parse_skill_md(sub_md)
                    if parsed:
                        slug = parsed.get("name") or sub.name
                        out.append((slug, sub, parsed))
            continue
        parsed = _parse_skill_md(skill_md)
        if not parsed:
            continue
        slug = parsed.get("name") or entry.name
        out.append((slug, entry, parsed))
    return out


def list_skills(include_body: bool = False) -> List[Dict[str, Any]]:
    """Return every available skill — bundled + user. User skills shadow
    bundled ones with the same slug. Default response is metadata only
    (name + description + version + folder); pass include_body=True for
    the full SKILL.md body."""
    seen: Dict[str, Dict[str, Any]] = {}
    for root in (_BUNDLED_SKILLS_DIR, _USER_SKILLS_DIR):
        for slug, dir_path, parsed in _scan_dir(root):
            entry: Dict[str, Any] = {
                "name": slug,
                "description": parsed.get("description", ""),
                "version": parsed.get("version", ""),
                "folder": str(dir_path),
                "source": "user" if root == _USER_SKILLS_DIR else "bundled",
            }
            if include_body:
                entry["body"] = parsed.get("body", "")
                # List assets/scripts/references so the model knows what
                # bundled resources exist without needing to glob.
                for sub_kind in ("scripts", "references", "assets"):
                    sub = dir_path / sub_kind
                    if sub.is_dir():
                        entry[sub_kind] = sorted(
                            p.name for p in sub.iterdir()
                            if p.is_file() and not p.name.startswith("."))
            seen[slug] = entry
    return list(seen.values())


def load_skill(name: str) -> Dict[str, Any]:
    """Load a single skill by slug. Returns full SKILL.md body + the
    resource manifest. Use when the model decides to actually USE a skill
    (after seeing the one-line summary in list_skills)."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "skill name required"}
    for s in list_skills(include_body=True):
        if s["name"] == name:
            return {"ok": True, **s}
    available = ", ".join(s["name"] for s in list_skills()) or "(none)"
    return {"ok": False,
            "error": f"no skill named '{name}'. Available: {available}"}


def skills_for_prompt(max_per_skill: int = 120) -> str:
    """One-line-per-skill block for the system prompt. Cheap (~50 chars
    each). Lets the model see the available skill catalog without paying
    the JSON-schema cost of registering each as a tool."""
    skills = list_skills(include_body=False)
    if not skills:
        return ""
    # Cap the catalog so it can't grow the prompt unbounded as a user installs /
    # authors more skills — the model loads the rest via list_skills on demand
    # (same idea as the memory-index + tool-diet caps).
    _MAX_IN_PROMPT = 30
    extra = max(0, len(skills) - _MAX_IN_PROMPT)
    lines = ["# Available skills (call load_skill(<name>) for the full instructions)"]
    for s in skills[:_MAX_IN_PROMPT]:
        desc = (s.get("description") or "").strip()
        if len(desc) > max_per_skill:
            desc = desc[:max_per_skill].rstrip() + "..."
        lines.append(f"  - {s['name']} - {desc}")
    if extra:
        lines.append(f"  …and {extra} more — call list_skills for the full catalog.")
    return "\n".join(lines)


def create_skill(name: str, description: str, body: str,
                  scripts: dict | None = None) -> dict:
    """Author a new skill at ~/Jarvis/skills/<name>/. The model can call
    this to crystallize a workflow it just discovered (e.g. "make-resume",
    "summarize-youtube", "send-discord-message").

    Args:
      name        slug, lower-kebab-case (validated)
      description one-line when-to-use (shows in the catalog)
      body        markdown body (the workflow instructions)
      scripts     optional {filename: source_text} dict written to scripts/
    """
    import re as _re
    n = (name or "").strip()
    if not n or not _re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,40}", n):
        return {"ok": False, "error":
                "name must be lower-kebab-case slug, 2-40 chars"}
    if not (description or "").strip():
        return {"ok": False, "error": "description is required"}
    if not (body or "").strip():
        return {"ok": False, "error": "body markdown is required"}
    dest = _USER_SKILLS_DIR / n
    if dest.exists():
        return {"ok": False, "error":
                f"skill '{n}' already exists at {dest}"}
    try:
        dest.mkdir(parents=True, exist_ok=True)
        md = (
            f"---\n"
            f"name: {n}\n"
            f"description: {description.strip()}\n"
            f"version: 1.0.0\n"
            f"---\n\n"
            f"{body.strip()}\n"
        )
        (dest / "SKILL.md").write_text(md, encoding="utf-8")
        if scripts:
            (dest / "scripts").mkdir(exist_ok=True)
            for fn, src in scripts.items():
                if not _re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", fn or ""):
                    continue
                (dest / "scripts" / fn).write_text(src or "", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "name": n, "folder": str(dest),
            "next": f"call load_skill('{n}') to use it immediately"}
