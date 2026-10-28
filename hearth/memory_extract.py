"""The memory pass: one model call over a chat that went quiet.

It returns a fixed JSON shape: the chat's card (where it stands, or null for a
chat with no goal) and a short list of fact changes, each an add, an update or
an invalidate checked against up to ten saved facts the conversation may touch
(found by words and names, no vectors). Invalid JSON changes nothing: the old
card stays and no fact is written.

Facts are about the person, not one chat: every chat writes to one memory.
Safety: jokes, quotes, hypotheticals, role-play and harmful content are left
out; a fact needs confidence 4 of 5; categories are memory.VALID_TYPES.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import memory as _memory
from .chat_call import chat_create


ALLOWED_CATEGORIES = ("user", "project", "reference", "feedback")
DEFAULT_CONFIDENCE_FLOOR = 4
DEFAULT_RECENT_TURNS = 8
CANDIDATES = 10


PASS_PROMPT = """You keep the memory of a personal assistant. Read the conversation at the end and answer with ONE JSON object and nothing else, in exactly this shape:

{"card": null or {"goal": "...", "status": "open" | "done" | "dropped", "where": "...", "slots": {"name": "value"}, "left": ["..."], "names": ["..."]},
 "facts": [{"op": "add" | "update" | "invalidate", "title": "...", "target": "...", "category": "user" | "project" | "reference" | "feedback", "layer": "fact" | "preference" | "rule", "description": "...", "body": "...", "confidence": 1-5, "volatility": "durable" | "seasonal" | "transient", "importance": 1-3}]}

# card: where THIS chat stands, so the assistant can pick it up from their other chats
- goal: what they are trying to get done, in one line ("Buy a phone under 30k"). The card is null when the chat has no goal: small talk, a joke, a greeting, a one-off question. Never describe the chat itself ("banter", "casual talk").
- status: "open" while something is unfinished, "done" once it got done, "dropped" when they gave up on it.
- where: one short line on where it got to.
- slots: values settled so far, such as a budget, a shortlist, dates, sizes, places. {} when there are none.
- left: THEIR pending things (a purchase not made, a fee not paid, a reply they wait for), not work the assistant is doing. [] when nothing is left.
- names: products, people, places and projects named.
- "Card before" is this chat's card so far. Give the whole card again with what changed: it replaces the old one.

# facts: lasting things about the USER worth knowing in any later chat
Who they are, what they own, their tastes, the people in their life, projects and plans, what broke. Casual life details count. Leave out jokes, sarcasm, role-play, quotes from films or games, hypotheticals, one-off questions, anything illegal or violent, and things only the assistant said. Once they said not to save or remember things, answer {"card": null, "facts": []}.
- op "add": a new fact. op "update": "target" is a saved title below that this conversation changes ("moved to Linux" for a saved OS); give the new text. op "invalidate": "target" is a saved fact that is no longer true, with nothing replacing it. A saved fact repeated without a change is left out.
- title: a short kebab-case slug. description: one sentence. body: one to three sentences with the specifics.
- confidence: 5 when they asked to remember it, 4 when clearly meant; 3 or less is dropped.
- volatility: "durable" for identity, things owned, lasting tastes; "seasonal" for what holds now and changes within a year (grade, job, city, the game they are playing); "transient" for a week or two (something broke, a plan for Friday). Unsure between durable and seasonal: seasonal.
- importance: 3 central to them, 2 useful, 1 minor.
- layer: "fact" by default; "preference" for how they want replies ("short answers"); "rule" for a standing order ("always answer in English").

# Example: a chat about buying a phone
{"card": {"goal": "Buy a phone under 30k with a good camera", "status": "open", "where": "Down to two models", "slots": {"budget": "30k", "shortlist": "Pixel 9a, Nothing Phone 3"}, "left": ["Hasn't bought it yet"], "names": ["Pixel 9a", "Nothing Phone 3"]},
 "facts": [{"op": "add", "title": "phone-camera-priority", "target": "", "category": "user", "layer": "fact", "description": "Cares most about the camera when choosing a phone.", "body": "Came up while comparing phones.", "confidence": 4, "volatility": "seasonal", "importance": 2}]}

# Example: small talk
{"card": null, "facts": []}
"""


# What the background pass saved, newest last, for the window to hear about.
NOTICED: List[Dict[str, Any]] = []


def _safe_strip_json(s: str) -> str:
    """Some small models wrap JSON in fences or add a line before it. Pull the
    first balanced object or array out of the string."""
    s = (s or "").strip()
    s = re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.I).strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    if "{" in s and "}" in s and ("[" not in s or s.index("{") < s.index("[")):
        start, end = s.index("{"), s.rindex("}")
        if end > start:
            return s[start: end + 1]
    if "[" in s and "]" in s:
        start, end = s.index("["), s.rindex("]")
        if end > start:
            return s[start: end + 1]
    return s


def _format_turns_for_prompt(messages: List[Dict[str, Any]], recent_turns: int) -> str:
    """The last exchanges as a transcript: what was said and replied, without
    tool output or what Hearth put in front of a message."""
    from .session_search import clean_said
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    convo = convo[-(recent_turns * 2):]
    lines: List[str] = []
    for m in convo:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join((b.get("text", "") if isinstance(b, dict) else str(b)) for b in content)
        content = (content or "").strip()
        if m.get("role") == "user":
            content = clean_said(content)
        if not content or content == "__HEARTH_NOTIFY_FLUSH__":
            continue
        if len(content) > 1200:
            content = content[:1200] + " […]"
        lines.append(f"{m['role'].upper()}: {content}")
    return "\n\n".join(lines)


def _fact_already_known(title: str) -> bool:
    return os.path.isfile(os.path.join(_memory.MEM_DIR, f"{_memory._slug(title)}.md"))


def enabled() -> bool:
    """Whether memory is noted in the background at all (Settings: Remember
    things on its own). Being asked not to save is left to the pass."""
    try:
        from .tools import WORKSPACE
        with open(os.path.join(WORKSPACE, "settings.json"), encoding="utf-8") as f:
            return (json.load(f) or {}).get("auto_memory") is not False
    except Exception:
        return True


def candidates(transcript: str) -> List[Dict[str, str]]:
    """Up to ten saved facts the pass checks its proposals against: those the
    words and names of the conversation point at, then the newest."""
    out: List[Dict[str, str]] = []
    try:
        from . import session_search
        out = session_search.fact_candidates(transcript, CANDIDATES)
    except Exception:
        out = []
    have = {c["title"] for c in out}
    if len(out) < CANDIDATES:
        try:
            for t in _memory.fact_titles(CANDIDATES):
                if t not in have and len(out) < CANDIDATES:
                    out.append({"title": t, "description": ""})
                    have.add(t)
        except Exception:
            pass
    return out


def _candidates_block(cands: List[Dict[str, str]]) -> str:
    rows = "\n".join(f"- {c['title']}" + (f": {c['description']}" if c.get("description") else "") for c in cands)
    return "# Saved facts this conversation may touch\n" + (rows or "(nothing saved yet)") + "\n"


def _card_from_old(thread: Any) -> Any:
    """The shape an earlier prompt asked for ("thread"), read as a card."""
    if not isinstance(thread, dict):
        return thread
    if "goal" in thread:
        return thread
    return {"goal": thread.get("topic"), "where": thread.get("where"), "left": thread.get("open") or [],
            "status": "open" if thread.get("open") else "done"}


def extract_and_save(
    messages: List[Dict[str, Any]],
    llm_call: Callable[[str, str], str],
    *,
    recent_turns: int = DEFAULT_RECENT_TURNS,
    confidence_floor: int = DEFAULT_CONFIDENCE_FLOOR,
    skip_known: bool = True,
    chat_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Run the memory pass over `messages` and keep what it returns: the chat's
    card (when `chat_id` is given) and the facts. `llm_call(system, user)`
    answers with text. Returns (facts saved, warnings)."""
    saved: List[Dict[str, Any]] = []
    warnings: List[str] = []
    transcript = _format_turns_for_prompt(messages, recent_turns)
    if not transcript.strip():
        return saved, ["no user/assistant content to scan"]

    cards = None
    before = None
    if chat_id:
        try:
            from . import memory_cards as cards
            before = cards.get(chat_id)
        except Exception:
            cards = None
    shown = {k: v for k, v in (before or {}).items() if k in ("goal", "status", "where", "slots", "left", "names")}
    user_prompt = (PASS_PROMPT + "\n" + _candidates_block(candidates(transcript))
                   + "\n# Card before (this chat)\n" + (json.dumps(shown, ensure_ascii=False) if shown else "null")
                   + "\n\n# Conversation\n" + transcript + "\n\nJSON:")
    try:
        raw = llm_call("You are a precise JSON-emitting memory pass. Output ONLY JSON.", user_prompt)
    except Exception as e:
        warnings.append(f"extractor LLM call failed: {type(e).__name__}: {e}")
        return saved, warnings
    try:
        got = json.loads(_safe_strip_json(raw))
    except (json.JSONDecodeError, TypeError) as e:
        warnings.append(f"extractor returned non-JSON: {e} — raw head: {str(raw)[:120]!r}")
        return saved, warnings

    card = None
    if isinstance(got, dict):
        card = got.get("card") if "card" in got else _card_from_old(got.get("thread"))
        facts = got.get("facts") or []
    else:
        facts = got
    if not isinstance(facts, list):
        warnings.append(f"extractor returned non-list: {type(facts).__name__}")
        return saved, warnings
    if cards is not None and isinstance(card, dict):
        try:
            if isinstance(got, dict) and "thread" in got and isinstance(got.get("thread"), dict):
                # The earlier shape closed items one by one.
                done = {str(c).lower() for c in got["thread"].get("closed") or []}
                card["left"] = [x for x in (card.get("left") or []) if str(x).lower() not in done]
            cards.put(chat_id, card)
        except Exception as e:
            warnings.append(f"card not kept: {type(e).__name__}: {e}")

    for f in facts:
        if not isinstance(f, dict):
            continue
        out = _apply_fact(f, confidence_floor, skip_known, chat_id, warnings)
        if out:
            saved.append(out)

    try:
        _memory.auto_curate()
    except Exception:
        pass
    try:
        if cards is not None:
            cards.close_stale()
    except Exception:
        pass
    if saved:
        import time as _time
        NOTICED.append({"at": _time.time(), "titles": [f.get("title", "") for f in saved]})
        del NOTICED[:-20]
    return saved, warnings


def _apply_fact(f: Dict[str, Any], floor: int, skip_known: bool, chat_id: Optional[str],
                warnings: List[str]) -> Optional[Dict[str, Any]]:
    op = str(f.get("op") or "").strip().lower()
    title = str(f.get("title") or "").strip()
    target = str(f.get("target") or "").strip()
    if f.get("updates") is True:              # the earlier shape of an update
        op, target = "update", target or title
    category = str(f.get("category") or "").strip().lower()
    desc = str(f.get("description") or "").strip()
    body = str(f.get("body") or "").strip()
    try:
        conf = int(f.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0
    try:
        importance = min(3, max(1, int(f.get("importance") or 2)))
    except (TypeError, ValueError):
        importance = 2
    vol = str(f.get("volatility") or "seasonal").strip().lower()
    if vol not in ("durable", "seasonal", "transient"):
        vol = "seasonal"
    layer = str(f.get("layer") or "fact").strip().lower()

    if op == "invalidate":
        if conf and conf < floor:
            return None
        slug = _memory.invalidate(target or title) if (target or title) else None
        return {"title": target or title, "category": "invalidated", "description": desc, "confidence": conf} if slug else None
    if not desc or conf < floor:
        return None
    if layer == "preference":
        try:
            _memory.upsert_profile_line(desc)
            return {"title": title or "preference", "category": "profile", "description": desc, "confidence": conf}
        except Exception as e:
            warnings.append(f"profile upsert failed: {type(e).__name__}: {e}")
            return None
    if layer == "rule":
        try:
            _memory.append_rule(desc)
            return {"title": title or "rule", "category": "rule", "description": desc, "confidence": conf}
        except Exception as e:
            warnings.append(f"rule add failed: {type(e).__name__}: {e}")
            return None
    if op == "update" and target and _fact_already_known(target):
        title = target
    if not title:
        return None
    if category not in ALLOWED_CATEGORIES:
        warnings.append(f"dropped '{title}': bad category {category!r}")
        return None
    if _fact_already_known(title):
        if skip_known and op != "update":
            return None
        _memory.archive_before_update(title)
    try:
        _memory.save(title=title, mtype=category, description=desc, body=body or desc, tags=["auto-extracted"],
                     volatility=vol, supersedes=str(f.get("supersedes") or "").strip() or None,
                     source_chat=chat_id or "", importance=importance)
    except Exception as e:
        warnings.append(f"save failed for '{title}': {type(e).__name__}: {e}")
        return None
    return {"title": title, "category": category, "description": desc, "confidence": conf}


def make_brain_llm_call(model: str = "") -> Callable[[str, str], str]:
    """The pass through the cheapest model on hand (see memory_pass.choose_llm)."""
    from . import memory_pass
    return memory_pass.choose_llm(model)[0]


def make_openai_llm_call(client, model: str, *, temperature: float = 0.0,
                        max_tokens: int = 2000) -> Callable[[str, str], str]:
    """An OpenAI-compatible client as the llm_call extract_and_save takes.

    The budget is generous: a truncated reply fails as an unterminated string
    and nothing is kept, and a reasoning model spends part of it thinking."""
    def _call(system: str, user: str) -> str:
        kwargs = dict(model=model, messages=[{"role": "system", "content": system},
                                             {"role": "user", "content": user}],
                      temperature=temperature, max_tokens=max_tokens)
        # No reasoning for the pass: a model that thinks first can spend the whole
        # budget there. Local servers read chat_template_kwargs, cloud ones the
        # reasoning field; an endpoint that refuses either is asked again plainly.
        try:
            r = chat_create(client, **kwargs,
                            extra_body={"chat_template_kwargs": {"enable_thinking": False},
                                        "reasoning": {"enabled": False}})
        except Exception:
            try:
                r = chat_create(client, **kwargs, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            except Exception:
                r = chat_create(client, **kwargs)
        _msg = r.choices[0].message
        out = (getattr(_msg, "content", None) or "").strip()
        if not out:
            # A reasoning model that put everything in its reasoning channel.
            _ex = getattr(_msg, "model_extra", None) or {}
            out = (getattr(_msg, "reasoning_content", None) or getattr(_msg, "reasoning", None)
                   or _ex.get("reasoning") or _ex.get("reasoning_content") or "").strip()
        return out
    return _call
