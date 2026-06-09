"""Migrate existing flat memories to add sub_category frontmatter.

Run once after upgrading to v0.6 with the memory graph. Idempotent — files
that already have sub_category are skipped. Files where the regex
classifier can't confidently pick a bucket get `casual` (or the type's
default), same as fresh saves.

Usage:
    python -m scripts.migrate_memories_to_subcategories          # dry run
    python -m scripts.migrate_memories_to_subcategories --apply  # write
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hearth.memory import MEM_DIR  # noqa: E402
from hearth.memory_classify import classify_or_default  # noqa: E402


_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def parse(text: str):
    """Return (frontmatter_dict_lines, body) or (None, None) if not a
    Hearth memory file."""
    m = _FRONTMATTER.match(text)
    if not m:
        return None, None
    fm_block, body = m.group(1), m.group(2)
    fm_lines = [ln.rstrip() for ln in fm_block.split("\n")]
    return fm_lines, body


def get_field(lines, key: str) -> str:
    prefix = f"{key}:"
    for ln in lines:
        if ln.startswith(prefix):
            return ln[len(prefix):].strip()
    return ""


def has_field(lines, key: str) -> bool:
    prefix = f"{key}:"
    return any(ln.startswith(prefix) for ln in lines)


def insert_subcat(lines, sub: str):
    """Insert `sub_category: <sub>` AFTER description, BEFORE tags/updated."""
    out = []
    inserted = False
    for ln in lines:
        out.append(ln)
        if ln.startswith("description:") and not inserted:
            out.append(f"sub_category: {sub}")
            inserted = True
    if not inserted:
        # No description line — drop it before the final `updated:`/end.
        for i, ln in enumerate(out):
            if ln.startswith("updated:") or ln.startswith("tags:"):
                out.insert(i, f"sub_category: {sub}")
                inserted = True
                break
    if not inserted:
        out.append(f"sub_category: {sub}")
    return out


def add_cat_tag(lines, sub: str):
    """Add `cat:<sub>` to the tags line (or create it). Skips if already there."""
    cat_tag = f"cat:{sub}"
    for i, ln in enumerate(lines):
        if ln.startswith("tags:"):
            if cat_tag in ln:
                return lines
            # parse [a, b, c] format
            inner = ln[len("tags:"):].strip()
            if inner.startswith("[") and inner.endswith("]"):
                inner_list = [t.strip() for t in inner[1:-1].split(",") if t.strip()]
                inner_list.append(cat_tag)
                lines[i] = f"tags: [{', '.join(inner_list)}]"
            else:
                # space- or comma-separated bare tags — preserve shape
                lines[i] = f"{ln}, {cat_tag}" if inner else f"tags: [{cat_tag}]"
            return lines
    # No tags line — add one before `updated:` if present, else at end.
    for i, ln in enumerate(lines):
        if ln.startswith("updated:"):
            lines.insert(i, f"tags: [{cat_tag}]")
            return lines
    lines.append(f"tags: [{cat_tag}]")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Write changes. Without this flag, dry-run only.")
    ap.add_argument("--memory-dir", default=str(MEM_DIR),
                    help=f"Memory dir (default: {MEM_DIR})")
    args = ap.parse_args()

    mem_dir = Path(args.memory_dir)
    if not mem_dir.is_dir():
        print(f"error: not a directory: {mem_dir}")
        return 1

    files = sorted(p for p in mem_dir.glob("*.md") if p.name != "MEMORY.md")
    if not files:
        print(f"no memory files in {mem_dir}")
        return 0

    print(f"scanning {len(files)} memory file{'s' if len(files) != 1 else ''} "
          f"in {mem_dir}\n")

    skipped = updated = errors = 0
    breakdown: dict = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  ! {path.name}: read failed — {e}")
            errors += 1
            continue
        lines, body = parse(text)
        if lines is None:
            print(f"  - {path.name}: no frontmatter, skip")
            skipped += 1
            continue
        if has_field(lines, "sub_category"):
            skipped += 1
            continue
        mtype = (get_field(lines, "type") or "user").strip()
        desc = get_field(lines, "description") or ""
        # Parse the tags line so the classifier can use tag overrides
        # (e.g. tags containing "book" → books, not links).
        tags_raw = get_field(lines, "tags") or ""
        tags = []
        if tags_raw.startswith("[") and tags_raw.endswith("]"):
            tags = [t.strip() for t in tags_raw[1:-1].split(",") if t.strip()]
        sub = classify_or_default(mtype, desc + " " + (body or ""), tags=tags)
        breakdown[sub] = breakdown.get(sub, 0) + 1
        if args.apply:
            new_lines = insert_subcat(lines, sub)
            new_lines = add_cat_tag(new_lines, sub)
            new_text = "---\n" + "\n".join(new_lines) + "\n---\n" + (body or "")
            try:
                path.write_text(new_text, encoding="utf-8")
                print(f"  ✓ {path.name}: type={mtype} → sub_category={sub}")
                updated += 1
            except OSError as e:
                print(f"  ! {path.name}: write failed — {e}")
                errors += 1
        else:
            print(f"  · {path.name}: type={mtype} → would set sub_category={sub}")

    print()
    print(f"== summary ==")
    print(f"  classified: {sum(breakdown.values())}")
    print(f"  skipped:    {skipped} (already had sub_category, or not a memory)")
    print(f"  errors:     {errors}")
    if breakdown:
        print(f"  buckets:")
        for sub, n in sorted(breakdown.items(), key=lambda kv: -kv[1]):
            print(f"    {sub:18} {n}")
    if not args.apply:
        print()
        print("dry-run only. re-run with --apply to write the changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
