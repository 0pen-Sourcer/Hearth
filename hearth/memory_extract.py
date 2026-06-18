"""Passive fact extraction — durable memory without `memory_save` tool calls.

At safe boundaries (end of session, after compaction) run a single LLM pass
over recent turns, extract durable facts about the user, persist via
memory.save(). The agent does not need to fire memory_save by hand.

Facts are user-scoped, not chat-scoped: the same person across multiple GUI
chat threads writes to one shared ~/Jarvis/memory/ bank.

Safety guards on the extraction pass:
  - Hard filter on jokes, hypotheticals, sarcasm, quoted speech, illegal
    or violent content.
  - 1-5 confidence score per fact; only >=4 persists.
  - Strict category whitelist matching memory.VALID_TYPES
    (user / feedback / project / reference).
  - Dedup against existing slugs; skip_known defaults True.

Cost: one cheap LLM call per compaction or session end, not per turn.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import memory as _memory


# Categories the LLM is allowed to use. Anything else gets dropped silently.
# Matches memory.VALID_TYPES so save() doesn't have to normalize down the line.
ALLOWED_CATEGORIES = ("user", "project", "reference", "feedback")

# Confidence floor. The extractor rates each fact 1-5 on "would the user
# genuinely want me to remember this across future chats?". 1-2 = joke /
# unclear / hypothetical, 3 = maybe, 4 = clearly meant it, 5 = explicit
# "remember that ...". Only >=4 gets saved.
DEFAULT_CONFIDENCE_FLOOR = 4

# How many recent assistant+user turns to scan. Tool messages count too but
# add noise; the prompt tells the model to focus on USER intent.
DEFAULT_RECENT_TURNS = 8


EXTRACTION_PROMPT = """You are Hearth's memory extraction pass. Read the recent conversation below and extract DURABLE facts about the USER that would help a future assistant serve them better.

# What counts as a durable fact
Be GENEROUS here. The goal is "JARVIS feels like he knows me" — that comes
from remembering casual life details, not just formal preferences. If the
user mentions it offhand, it's probably worth saving:

- Their name, role, hardware they own ("just got new wireless earbuds"), OS,
  file/drive layout they referenced.
- Stuff they're hyped for or planning ("picking up that new game next
  Friday", "going on vacation next month").
- Stuff that just broke / they're annoyed about ("left earbud died",
  "WiFi's been flaky"). These age out but are useful for 1-2 weeks.
- Stated preferences ("I prefer dark mode", "always use uv not pip",
  "I hate cilantro").
- Long-running projects + their state ("Hearth v0.6 launch is next week").
- External tool URLs / accounts they mentioned ("my Notion is at X").
- Workflow corrections ("don't run tests before commits — they're slow").
- People + relationships they mentioned by role ("my coworker Sam reviews PRs",
  "my dog Bandit", "my brother just moved to Seattle").
- Media they love or hate ("currently playing Sekiro", "halfway through Dune part 2").
- Health / lifestyle they shared ("workout at 7am", "intermittent fasting",
  "trying to cut sugar"). Skip if it sounds like a one-time complaint.

Casual > formal. A real assistant remembers "your left earbud broke" because
it's the kind of thing a friend would ask about next time. Conf 4 is right
for these; reserve 5 for things they EXPLICITLY asked to be remembered.

# What does NOT count (BAN — filter these out)
- Jokes, sarcasm, role-play, hyperbole, anything said as a joke.
- Quoted speech from movies/shows/games/books ("I'm the one who knocks").
- Hypotheticals ("what if I told you...").
- Anything illegal, violent, NSFW, or about real harm — these aren't
  preferences a real user wants remembered. Even if literal, skip.
- One-off questions ("what's the capital of France").
- Things the assistant said, only things the USER said or revealed.
- Anything that's already obviously in memory (avoid duplicates).

# Output format — STRICT JSON ONLY, no preamble, no markdown fences
Return a JSON array. Each fact is an object with these exact keys:

  - "title": short kebab-case slug, max 5 words. e.g. "favorite-editor"
  - "category": one of: user | project | reference | feedback
  - "description": ONE concise sentence summarizing the fact.
  - "body": 1-3 sentences with the specifics + WHY it matters going forward.
  - "confidence": integer 1-5. Score it honestly. Anything <=3 will be DROPPED.

If you find NOTHING worth saving, return: []

# Examples
GOOD (will save):
  {"title": "primary-gpu", "category": "user", "description": "User runs a mid-range NVIDIA GPU with ~8 GB VRAM.", "body": "Local LLMs need to fit in 8 GB. Surfaced when sizing model picks.", "confidence": 5}

  {"title": "broken-earbud", "category": "user", "description": "One side of the user's wireless earbuds stopped working.", "body": "Minor annoyance — they may repair or replace. Worth bringing up the next time audio comes up.", "confidence": 4}

  {"title": "upcoming-game-release", "category": "user", "description": "User excited about a specific game launching this week.", "body": "Game launch they're looking forward to — ask how it went afterwards.", "confidence": 4}

  {"title": "side-project-deadline", "category": "project", "description": "Side project has a hard ship date this month.", "body": "Avoid blocking on related work. Star-farm or release is the win condition.", "confidence": 5}

BAD (will NOT save — extractor must filter these):
  {"title": "likes-breaking-bad", "category": "user", "description": "User likes kids who cook Heisenberg's Blue.", ...}  # joke / quote — DROP
  {"title": "wants-to-destroy-world", "category": "user", "description": "User wants to take over the world.", ...}  # joke / hyperbole — DROP
  {"title": "asked-about-france", "category": "user", "description": "User asked what the capital of France is.", ...}  # one-off question — DROP

# Recent conversation
"""


def _safe_strip_json(s: str) -> str:
    """Some small models wrap JSON in ```json fences or add a one-line
    intro. Pull the first balanced [...] or {...} out of the string."""
    s = (s or "").strip()
    # Strip markdown fences
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    # Find first [ to last ] — JSON array case
    if "[" in s and "]" in s:
        start = s.index("[")
        end = s.rindex("]")
        if end > start:
            return s[start : end + 1]
    return s


def _format_turns_for_prompt(messages: List[Dict[str, Any]], recent_turns: int) -> str:
    """Take the last N user+assistant turns and render them as a transcript.
    Skip tool/system messages — they're noise for fact extraction."""
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    convo = convo[-(recent_turns * 2):]  # rough turn = user + assistant pair
    lines: List[str] = []
    for m in convo:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            # Multimodal content blocks — just concat the text parts
            content = " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b))
                for b in content
            )
        content = (content or "").strip()
        if not content:
            continue
        # Cap each turn to keep prompt bounded
        if len(content) > 1200:
            content = content[:1200] + " […]"
        lines.append(f"{role.upper()}: {content}")
    return "\n\n".join(lines)


def _fact_already_known(title: str) -> bool:
    """Check if a fact with this slug is already on disk. Cheap stat."""
    slug = _memory._slug(title)
    path = os.path.join(_memory.MEM_DIR, f"{slug}.md")
    return os.path.isfile(path)


def extract_and_save(
    messages: List[Dict[str, Any]],
    llm_call: Callable[[str, str], str],
    *,
    recent_turns: int = DEFAULT_RECENT_TURNS,
    confidence_floor: int = DEFAULT_CONFIDENCE_FLOOR,
    skip_known: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Run the extraction pass and persist surviving facts.

    Args:
      messages: full chat history (includes tool/system — we filter inside).
      llm_call: callable(system_prompt, user_prompt) -> assistant text. Pass
                a function that wraps whatever client the caller uses
                (cli/headless both have an OpenAI client handy).
      recent_turns: how many user+assistant pairs to scan.
      confidence_floor: drop facts below this confidence. 4 is the safe
                       default; raise to 5 for "only save if model is sure".
      skip_known: don't overwrite an existing memory of the same slug.
                  Set False to allow refresh.

    Returns:
      (saved_facts, warnings): list of facts that were actually persisted,
      and any human-readable warnings (e.g. "model returned non-JSON").
    """
    saved: List[Dict[str, Any]] = []
    warnings: List[str] = []

    transcript = _format_turns_for_prompt(messages, recent_turns)
    if not transcript.strip():
        return saved, ["no user/assistant content to scan"]

    user_prompt = EXTRACTION_PROMPT + "\n" + transcript + "\n\nJSON:"
    try:
        raw = llm_call(
            "You are a precise JSON-emitting fact extractor. Output ONLY a JSON array.",
            user_prompt,
        )
    except Exception as e:
        warnings.append(f"extractor LLM call failed: {type(e).__name__}: {e}")
        return saved, warnings

    cleaned = _safe_strip_json(raw)
    try:
        facts = json.loads(cleaned)
    except json.JSONDecodeError as e:
        warnings.append(f"extractor returned non-JSON: {e} — raw head: {raw[:120]!r}")
        return saved, warnings

    if not isinstance(facts, list):
        warnings.append(f"extractor returned non-list: {type(facts).__name__}")
        return saved, warnings

    for f in facts:
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "").strip()
        category = str(f.get("category") or "").strip().lower()
        desc = str(f.get("description") or "").strip()
        body = str(f.get("body") or "").strip()
        try:
            conf = int(f.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0

        if not title or not desc:
            continue
        if category not in ALLOWED_CATEGORIES:
            warnings.append(f"dropped '{title}': bad category {category!r}")
            continue
        if conf < confidence_floor:
            # Quietly drop — this is the joke-fact gate
            continue
        if skip_known and _fact_already_known(title):
            continue

        try:
            _memory.save(
                title=title,
                mtype=category,
                description=desc,
                body=body or desc,
                tags=["auto-extracted"],
            )
            saved.append(
                {"title": title, "category": category, "description": desc,
                 "confidence": conf}
            )
        except Exception as e:
            warnings.append(f"save failed for '{title}': {type(e).__name__}: {e}")

    # Auto-curate: whenever new facts landed (the only time fresh dups can
    # appear), silently merge same-topic duplicates so memory self-maintains —
    # no manual /curate needed. Conservative + non-destructive (archives the
    # older copies, recoverable). Best-effort; never breaks the extraction.
    if saved:
        try:
            from . import memory as _memory
            _memory.curate(apply=True)
        except Exception:
            pass

    return saved, warnings


def make_openai_llm_call(client, model: str, *, temperature: float = 0.0,
                        max_tokens: int = 800) -> Callable[[str, str], str]:
    """Convenience: wrap an OpenAI-compatible client into the llm_call shape
    extract_and_save expects. Caller passes their own client so we don't
    have to know which endpoint/key combo is in use."""
    def _call(system: str, user: str) -> str:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (r.choices[0].message.content or "").strip()
    return _call
