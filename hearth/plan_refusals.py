"""An agent saying the model picked isn't on the person's plan, in plain words.

Cursor, Codex and Claude Code each refuse a model their plan doesn't include
with a message of their own. Each is read here for what it names (the plan,
the models it does include), said in a line with the way on, and remembered,
so the model menu can mark the models this plan refused. Nothing about what a
plan includes is written here: it comes from the agent's own message.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

_FILE = os.path.join(os.path.expanduser("~"), ".hearth", "plan_refused.json")

# Cursor: "Model unavailable on Free\nFree includes Grok 4.7, Grok 4.6, and Auto with
# fixed settings. Switch to an included model to continue."
_CURSOR = re.compile(r"Model unavailable on (?P<plan>[A-Za-z][\w+-]*)", re.I)
# The list ends at the sentence's full stop, not at the dot in "4.7".
_CURSOR_LIST = re.compile(r"\bincludes (?P<list>.+?)(?:\s+with\b.*?)?\.(?=\s+[A-Z]|\s*$)", re.I)
# Codex: "The 'gpt-5.2-codex' model is not supported when using Codex with a ChatGPT account."
_CODEX = re.compile(r"The '(?P<model>[^']+)' model is not supported when using Codex with a (?P<plan>[\w ]+?) account",
                    re.I)
# Claude Code: "Claude Pro users are not currently able to use Opus in Claude Code."
_CLAUDE = re.compile(r"Claude (?P<plan>\w+) users are not (?:currently )?able to use (?P<model>[\w. -]+?) in Claude Code",
                     re.I)


def _names(text: str) -> List[str]:
    parts = re.split(r",\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _join(names: List[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def parse(text: str) -> Optional[Dict[str, Any]]:
    """{agent_kind, plan, model, included} for a plan refusal in an agent's words, else None."""
    t = " ".join(str(text or "").split())
    m = _CURSOR.search(t)
    if m:
        lst = _CURSOR_LIST.search(t[m.end():])
        return {"kind": "cursor", "plan": m.group("plan"), "model": "",
                "included": _names(lst.group("list")) if lst else []}
    m = _CODEX.search(t)
    if m:
        return {"kind": "codex", "plan": m.group("plan").strip(), "model": m.group("model"), "included": []}
    m = _CLAUDE.search(t)
    if m:
        return {"kind": "claude", "plan": m.group("plan"), "model": m.group("model").strip(), "included": []}
    return None


def say(name: str, got: Dict[str, Any]) -> str:
    """The line for the chat: what the plan doesn't include, and what to pick."""
    plan = got.get("plan") or ""
    plan_words = f"{plan} plan" if plan.lower() != "chatgpt" else "ChatGPT plan"
    what = got.get("model") or "that model"
    included = list(got.get("included") or [])
    # The one-tap choice first, then the rest in the agent's own order.
    auto = [n for n in included if n.lower() == "auto"]
    included = auto + [n for n in included if n.lower() != "auto"]
    head = f"{name}'s {plan_words.lower() if plan.lower() == 'free' else plan_words} doesn't include {what}."
    return head + (f" Pick {_join(included)}." if included else " Pick another model.")


def _read() -> Dict[str, Any]:
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(provider: str, model: str, plan: str) -> None:
    if not model:
        return
    d = _read()
    d.setdefault(provider, {})[model] = {"plan": plan, "at": time.time()}
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, _FILE)


def refused(provider: str) -> Dict[str, str]:
    """model id -> the plan that refused it, for this agent."""
    return {m: str(v.get("plan") or "") for m, v in (_read().get(provider) or {}).items()}


def forget(provider: str, model: str) -> None:
    """A model that answered after all (the plan changed) loses its mark."""
    d = _read()
    if model in (d.get(provider) or {}):
        d[provider].pop(model, None)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _FILE)
