"""Regex-first memory categorizer.

Why regex over LLM:
  - Zero added latency on every chat turn (we call this on EVERY save).
  - Deterministic — same phrase always picks the same category, no drift.
  - Free — no extra tokens spent on a classifier call.

When regex misses:
  - LLM tiebreaker can be called explicitly by the caller (async, optional).
  - Memory_extract already runs an LLM pass; this just refines the type.

Categories below are SUB-CATEGORIES for the memory graph view — they
group facts within a TYPE for the "tree" rendering. The TYPE itself
stays the 4 canonical values (user/feedback/project/reference);
categories are added to tags so the graph viz + recall can group on
them without changing the schema.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


# Sub-category palette per type. Keep these stable — the GUI graph maps
# each to a color. Don't reorder without updating ui.html.
SUB_CATEGORIES: Dict[str, List[str]] = {
    "user": [
        "hardware",       # GPU/RAM/OS/drives/peripherals
        "preferences",    # food, music, style, dark-mode, etc.
        "media",          # currently-playing game / book / show
        "relationships",  # people they mentioned by role
        "routine",        # workout, sleep, fasting, daily habits
        "identity",       # name, role, location, age, language
        "casual",         # uncategorized but durable user facts
    ],
    "project": [
        "ongoing",        # actively-being-built things
        "deadline",       # date-bound work
        "decision",       # "we decided X" / "the project uses Y"
        "side",           # side projects, hobby builds
    ],
    "reference": [
        "links",          # URLs / accounts / external tools
        "credentials",    # API keys / passwords (only when explicit)
        "books",          # book notes from read_book recipe
        "documentation",  # internal-team docs, runbooks
    ],
    "feedback": [
        "correction",     # "don't do X" / "stop Y"
        "preference",     # "I like when you Z"
        "workflow",       # "always run linter before commit"
    ],
}


# Hardware: GPU/RAM/OS/drive layout/peripherals
_HW = [
    re.compile(r"\b(rtx|gtx|rx|arc)\s*\d+", re.I),
    re.compile(r"\b\d+\s*gb\s+(ram|vram|memory|ssd|hdd|disk)\b", re.I),
    re.compile(r"\b(my|new)\s+(gpu|cpu|ram|monitor|keyboard|mouse|earbud|headphone|headset|laptop|pc|rig|drive)\b", re.I),
    re.compile(r"\b(intel|amd|nvidia|apple)\s+(arc|ryzen|core|silicon|m\d)\b", re.I),
    re.compile(r"\bwindows\s+\d+|\bmacos\b|\blinux\b|\bubuntu\b", re.I),
]
# Media: games, books, shows, music
_MEDIA = [
    re.compile(r"\b(playing|watching|reading|listening to)\s+\w+", re.I),
    re.compile(r"\b(my favorite|currently into)\s+(game|book|show|movie|album|band|artist|series)\b", re.I),
    re.compile(r"\b(hyped|excited|looking forward) (for|to)\s+\w+", re.I),
    re.compile(r"\b(cyberpunk|sekiro|elden|hollow knight|helldivers|battlefield|fortnite|valorant)\b", re.I),
]
# Routine: workout, sleep, daily habits
_ROUTINE = [
    re.compile(r"\b(workout|work out|gym|run|jog)\s+(at|every|in the)\b", re.I),
    re.compile(r"\b(sleep|wake up|nap)\s+(at|by|around)\b", re.I),
    re.compile(r"\b(intermittent fasting|cut(ting)? sugar|diet|cardio)\b", re.I),
    re.compile(r"\bevery (morning|evening|night|day|week)\b", re.I),
]
# Relationships: people by role
_REL = [
    re.compile(r"\b(my)\s+(brother|sister|mom|mother|dad|father|wife|husband|partner|coworker|colleague|boss|manager|friend|dog|cat|pet)\b", re.I),
    re.compile(r"\b(reports to me|reports to|works with|teammate|TA|professor)\b", re.I),
]
# Identity: name, role, location, age
_IDENTITY = [
    re.compile(r"\b(call me|my name is|i('| a)m called)\s+\w+", re.I),
    re.compile(r"\b(i('| a)m a|i work as a|i('| a)m studying)\s+\w+", re.I),
    re.compile(r"\bi live in\s+\w+", re.I),
    re.compile(r"\b(i('| a)m|i am)\s+\d{1,2}\s+(years old|y\.?o\.?)\b", re.I),
]
# Preferences: stated likes/dislikes
_PREF = [
    re.compile(r"\bi (prefer|like|love|hate|dislike|always use|never use)\s+\w+", re.I),
    re.compile(r"\bmy (favorite|preferred|default)\s+\w+\s+is\b", re.I),
]

# Project: decisions + deadlines
_DEADLINE = [
    re.compile(r"\b(ship(s|ping)?|launch(es|ing)?|due|deadline|release(s|d)?)\s+(on|by|next|in)\s+\w+", re.I),
    re.compile(r"\b(next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month)\b", re.I),
]
_DECISION = [
    re.compile(r"\bwe (decided|agreed|chose|picked)\s+(to\s+)?\w+", re.I),
    re.compile(r"\bthe project (uses|needs|requires|wants)\s+\w+", re.I),
]
_ONGOING = [
    re.compile(r"\b(building|working on|prototyping|developing|writing)\s+\w+", re.I),
]

# Reference: links, credentials, books, docs
_LINK = [
    re.compile(r"https?://\S+", re.I),
    re.compile(r"\b(notion|github|gitlab|slack|jira|linear|figma|airtable)\.(com|io)\b", re.I),
]
# Credentials get checked BEFORE links because "OPENAI_API_KEY" doesn't
# contain a URL but mentioning a key (regardless of value) means it's a
# credential context — and we don't want users' secret-pasted notes
# misclassified as "links".
_CRED = [
    re.compile(r"\b(api[_\- ]?key|access[_\- ]?token|secret|password|credential)\b", re.I),
    re.compile(r"\b[A-Z]{2,}_[A-Z_]+_(?:KEY|TOKEN|SECRET|PASS|PWD)\b"),  # OPENAI_API_KEY style
]
_BOOK = [
    re.compile(r"^book[ :-]", re.I),
    re.compile(r"\b(chapter|ch\.?)\s*\d", re.I),
    re.compile(r"\[p\d", re.I),
]
_DOC = [
    re.compile(r"\b(runbook|playbook|sop|docs?|wiki)\b", re.I),
]

# Feedback patterns
_CORRECTION = [
    re.compile(r"\b(don't|do not|never|stop|avoid)\s+\w+", re.I),
    re.compile(r"\bstop (doing|saying|asking)\b", re.I),
]
_WORKFLOW = [
    re.compile(r"\balways\s+\w+", re.I),
    re.compile(r"\bbefore (you|each|every)\s+\w+", re.I),
]


# Map sub-category → ordered pattern list. Order matters: first match wins.
_RULES: Dict[str, List[Tuple[str, List[re.Pattern]]]] = {
    "user": [
        ("identity",      _IDENTITY),
        ("hardware",      _HW),
        ("relationships", _REL),
        ("routine",       _ROUTINE),
        ("media",         _MEDIA),
        ("preferences",   _PREF),
    ],
    "project": [
        ("deadline", _DEADLINE),
        ("decision", _DECISION),
        ("ongoing",  _ONGOING),
    ],
    "reference": [
        ("credentials",   _CRED),
        ("books",         _BOOK),
        ("documentation", _DOC),
        ("links",         _LINK),
    ],
    "feedback": [
        ("correction", _CORRECTION),
        ("workflow",   _WORKFLOW),
    ],
}


# Strong tag→sub-category overrides. When a memory already has these tags
# we trust them — much stronger signal than regex on the body. Common
# case: read_book recipe tags chunks with "book:<slug>" so we know it's a
# book note without scanning for [p47] in the body.
_TAG_OVERRIDES = {
    "book": "books",
    "auto-extracted": None,  # don't override on this alone — extractor handles type
    "credential": "credentials",
}


def classify_sub_category(mtype: str, text: str,
                          tags: Optional[List[str]] = None,
                          min_signal_chars: int = 4) -> Optional[str]:
    """Pick a sub-category for a memory given its type, body text, and tags.

    Order:
      1. Tag override — if the memory has `book` / `credential` / etc tags
         that map cleanly, trust them. Cheap, deterministic, beats regex.
      2. Regex pattern match with confidence floor — substring must be at
         least `min_signal_chars` long to count (catches joke-leak false
         positives like matching "my" in "my pet rock joke").
      3. None — caller defaults to "casual" / type-default.

    Returns the sub-category slug if a STRONG signal matches, else None.
    Cost: pure regex scan over ~10 patterns. Sub-millisecond per call.
    """
    # 1) Tag overrides win
    if tags:
        for t in tags:
            t_low = (t or "").lower().split(":")[0].strip()
            override = _TAG_OVERRIDES.get(t_low)
            if override:
                return override
    # 2) Regex match on body
    if not text or not isinstance(text, str):
        return None
    rules = _RULES.get(mtype, [])
    for sub, patterns in rules:
        for p in patterns:
            m = p.search(text)
            if m and len(m.group(0)) >= min_signal_chars:
                return sub
    return None


def default_sub_for_type(mtype: str) -> str:
    """Fallback sub-category when no regex matches. Keeps every memory
    in SOME bucket so the tree view never has orphans."""
    if mtype == "user":
        return "casual"
    if mtype == "project":
        return "ongoing"
    if mtype == "reference":
        return "links"
    if mtype == "feedback":
        return "correction"
    return "other"


def classify_or_default(mtype: str, text: str,
                        tags: Optional[List[str]] = None) -> str:
    """Convenience: classify_sub_category() with the default fallback."""
    return classify_sub_category(mtype, text, tags=tags) or default_sub_for_type(mtype)
