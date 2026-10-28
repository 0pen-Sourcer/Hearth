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


def _logged_refusal(agent: str, within_s: float = 86400) -> "tuple":
    """(what the agent logged, when) for the newest out-of-usage refusal in its own
    log within a day, or ('', 0). Only for agents whose log Hearth knows."""
    import os
    import re
    from datetime import datetime
    spec = _cb.ACP.get(agent) or {}
    path = os.path.expanduser(str(spec.get("log") or ""))
    if not path or not os.path.isfile(path):
        return "", 0
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 400_000))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return "", 0
    for line in reversed(tail.splitlines()):
        if "level=ERROR" not in line or not _cb._OUT_OF_USAGE.search(line):
            continue
        m = re.search(r"timestamp=(\S+)", line)
        e = re.search(r'error\.error="([^"]+)', line)
        try:
            when = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp() if m else 0
        except ValueError:
            when = 0
        if not when or time.time() - when > within_s or not e:
            return "", 0
        said = e.group(1)
        for pre in ("AI_RetryError: Failed after 3 attempts. Last error: ", "AI_APICallError: "):
            said = said.replace(pre, "")
        return said.strip()[:240], when
    return "", 0


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
            refused, when = _logged_refusal(aid)
            if refused:
                # What the agent itself logged, with when; not a guess at how much is left.
                entry.update(at=when, refused=refused,
                             note=f"{label} refused at {time.strftime('%H:%M', time.localtime(when))}: {refused}")
            else:
                entry["note"] = f"Can't tell: {label} doesn't report how much of its plan is used."
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
