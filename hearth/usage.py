"""How much of each brain's allowance is used, for every brain that can say.

Each source reports in its own way: Claude Code sends a rate_limit_event with
every run, Codex writes its limits into its own session files, OpenRouter
answers for a key at /api/v1/key. A brain with no way to report it (a local
model has no allowance; most API keys don't expose theirs) says so plainly
instead of being left out, so no one provider is treated as the only one.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

from . import claude_brain as _cb

# What each usage window is, read the same way whatever the source.
#   {"id", "label", "kind": "plan"|"key"|"local", "windows": [{"name", "used", "resets_at"}],
#    "credits": {"used", "left", "limit"}, "at", "note"}


def _openrouter(key: str) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = (json.load(r) or {}).get("data") or {}
    except Exception:
        return None
    return {"credits": {"used": d.get("usage"), "left": d.get("limit_remaining"), "limit": d.get("limit"),
                        "today": d.get("usage_daily"), "resets": d.get("limit_reset")},
            "free_tier": d.get("is_free_tier")}


# Agents that report their plan windows to Hearth, one way or another.
_REPORTS = {"claude-code", "codex"}


def everything(provider_key) -> List[Dict[str, Any]]:
    """One entry per brain Hearth knows about on this PC."""
    out: List[Dict[str, Any]] = []
    for aid, label in _cb.BRAINS.items():
        got = _cb.plan_limits(aid)
        entry = {"id": aid, "label": label, "kind": "plan", "plan": _cb.PLANS.get(aid, "")}
        now = time.time()
        if got:
            wins = []
            for k, v in (got.get("windows") or {}).items():
                w = {"name": k, **v}
                # A window that has reset since the agent last said is unknown now, not still full.
                if v.get("resets_at") and v["resets_at"] < now:
                    w.update(used=None, reset_since=True)
                wins.append(w)
            entry.update(at=got.get("at"), windows=wins)
        elif aid in _REPORTS:
            entry["note"] = f"{label} hasn't been used from Hearth yet; its usage shows here after the first run."
        else:
            entry["note"] = f"{label} doesn't tell other apps how much of its plan is used."
        out.append(entry)
    from .headless import PROVIDERS
    for pid, (label, _url) in PROVIDERS.items():
        key = provider_key(pid)
        if not key:
            continue
        entry = {"id": pid, "label": label, "kind": "key", "at": time.time()}
        if pid == "openrouter":
            got = _openrouter(key)
            entry.update(got or {"note": "OpenRouter didn't answer for this key."})
        else:
            entry["note"] = f"{label} doesn't tell apps how much of an API key is used; its own console does."
        out.append(entry)
    return out
