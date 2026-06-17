"""Regenerate docs/TOOLS.md from the live tool registry.

Run:  .\.venv\Scripts\python.exe -X utf8 scripts/gen_tools_doc.py

Keeps the public tool reference honest — the count and the list are pulled
straight from hearth.TOOL_DEFINITIONS, so it can't drift from the code the way a
hand-maintained table does. Re-run it whenever tools are added or removed.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import hearth
import hearth.tools as t


def first_sentence(desc: str, cap: int = 160) -> str:
    """First sentence of a tool description, collapsed to one line and capped."""
    d = re.sub(r"\s+", " ", (desc or "").strip())
    # Split on the first sentence end followed by a space + capital / quote / etc.
    m = re.search(r"\.(?:\s|$)", d)
    s = d[: m.start()] if m else d
    if len(s) > cap:
        s = s[: cap - 1].rstrip() + "…"
    return s or "(no description)"


def main() -> int:
    defs = hearth.TOOL_DEFINITIONS
    by_name = {d["name"]: d for d in defs}
    cat_of = dict(t._TOOL_CATEGORY)
    order = list(t._CATEGORY_ORDER)
    deferred = set(getattr(t, "_DEFERRED_TOOLS", set()) or set())

    # Bucket every tool by category; anything without an explicit category lands
    # in "Other" so nothing silently drops off the doc.
    buckets: dict[str, list[str]] = {c: [] for c in order}
    for name in by_name:
        c = cat_of.get(name, "Other")
        buckets.setdefault(c, []).append(name)
    if "Other" in buckets and not buckets["Other"]:
        del buckets["Other"]

    total = len(defs)
    n_deferred = sum(1 for n in by_name if n in deferred)
    n_default = total - n_deferred

    out = []
    out.append("# Tools\n")
    out.append(
        f"Hearth gives the model **{total} tools** to operate your machine. "
        f"Everything runs locally; the only outbound calls are web searches the "
        f"model itself makes. Risky tools (shell, file writes, app launch, "
        f"browser control) prompt for `[y/n/a/N]` permission in the CLI before "
        f"they run.\n"
    )
    out.append(
        f"To keep the prompt small, **{n_default}** core tools load by default "
        f"and **{n_deferred}** niche ones (marked †) stay behind a "
        f"`load_tools` meta-tool the model calls on demand. Set "
        f"`HEARTH_ALL_TOOLS=1` to load everything up front.\n"
    )
    out.append("_This file is generated from the live tool definitions "
               "(`scripts/gen_tools_doc.py`) — don't edit by hand._\n")

    ordered_cats = [c for c in order if buckets.get(c)] + [
        c for c in buckets if c not in order and buckets.get(c)
    ]
    for cat in ordered_cats:
        names = sorted(buckets[cat])
        out.append(f"\n## {cat}\n")
        out.append("| Tool | What it does |")
        out.append("|---|---|")
        for name in names:
            mark = " †" if name in deferred else ""
            desc = first_sentence(by_name[name].get("description", ""))
            desc = desc.replace("|", "\\|")
            out.append(f"| `{name}`{mark} | {desc} |")

    dest = os.path.join(ROOT, "docs", "TOOLS.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"wrote {dest}: {total} tools across {len(ordered_cats)} categories "
          f"({n_default} default, {n_deferred} on-demand)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
