"""When the memory pass runs, and on which model.

Once per chat, when it goes quiet for IDLE_S (a new turn in it starts the wait
again), and at once before a chat's history is compacted. Not after every
message: on a paid agent plan that would be a second request per turn.

The model: one on this PC when one is loaded (free), else the brain's own
cheapest one-shot: an agent program asked once with no tools, or the cloud
model in use. The same rule on every brain and every surface, since every turn
goes through headless.run_once.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from .local_engine import local_base as _local_base

from . import env as _env

IDLE_S = float(_env.get("MEMORY_IDLE", "180") or 180)
CATCH_UP_DAYS = 2           # chats this recent that never had a pass get one
CATCH_UP_MAX = 3

_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_PENDING: Dict[str, Dict[str, Any]] = {}
_WORKER: Dict[str, Any] = {"thread": None, "caught_up": False}
LAST: Dict[str, Any] = {}          # the last pass: chat, model, what it kept (for logs and tests)


# ---------------------------------------------------------------- which model
def _complete(base: str, key: str, model: str) -> Callable[[str, str], str]:
    from . import anthropic_chat as _ac, memory_extract as _mx
    if _ac.is_anthropic(base):
        from .headless import one_shot
        return lambda system, user: one_shot(system + "\n\n" + user, model, base=base, key=key,
                                             max_tokens=2000, timeout=90)
    import openai
    if not model:
        from .headless import provider_models
        listed = provider_models(base, key, timeout=3) or []
        model = listed[0] if listed else "local"
    return _mx.make_openai_llm_call(openai.OpenAI(api_key=key or "hearth-builtin", base_url=base, timeout=120), model)


def _local_route() -> Optional[Tuple[str, str, str]]:
    """(base, key, model) of a model loaded on this PC, or None."""
    try:
        from . import llmserver
        hs = llmserver.helper_status()
        if hs.get("running") and hs.get("url"):
            return hs["url"], "hearth-builtin", ""
    except Exception:
        pass
    import os
    from . import claude_brain, headless
    base = os.environ.get("LOCAL_API_BASE") or headless.LOCAL_API_BASE or ""
    if not claude_brain.active() and headless._is_local_endpoint(base):
        return base, os.environ.get("LOCAL_API_KEY") or headless.LOCAL_API_KEY or "hearth-builtin", \
            os.environ.get("LOCAL_MODEL") or ""
    try:
        from .subagents import _local_server_has_model
        if _local_server_has_model(_local_base()):
            return _local_base(), "hearth-builtin", ""
    except Exception:
        pass
    return None


def choose_llm(model: str = "") -> Tuple[Callable[[str, str], str], str]:
    """(llm_call, what it runs on) for the memory pass."""
    local = _local_route()
    if local:
        return _complete(*local), "local"
    from . import claude_brain
    if claude_brain.active():
        agent = claude_brain.current() or "agent"
        return (lambda system, user: claude_brain.quick(system + "\n\n" + user, timeout=180, any_agent=True)), agent
    import os
    from . import headless
    base = os.environ.get("LOCAL_API_BASE") or headless.LOCAL_API_BASE or ""
    key = os.environ.get("LOCAL_API_KEY") or headless.LOCAL_API_KEY or ""
    return _complete(base, key, model or os.environ.get("LOCAL_MODEL") or ""), "cloud"


def ask_once(prompt: str, timeout: float = 30) -> str:
    """A short answer (a chat's title) from the same model the pass uses, on every
    brain kind, within `timeout`. Empty when nothing answers in time."""
    try:
        from . import claude_brain
        if not _local_route() and claude_brain.active():
            return claude_brain.quick(prompt, timeout=timeout, any_agent=True) or ""
        llm, _where = choose_llm()
        return llm("", prompt) or ""
    except Exception:
        return ""


# ---------------------------------------------------------------- the pass
def _messages_for(chat_id: str, snapshot: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The chat as kept on disk (clean of what was put in front of messages),
    else what the turn handed over."""
    if chat_id:
        try:
            from . import chat_sync
            d = chat_sync.load(chat_id)
            msgs = [{"role": m.get("role"), "content": chat_sync._text(m.get("content"))}
                    for m in (d or {}).get("messages") or [] if m.get("role") in ("user", "assistant")]
            if msgs and len(msgs) >= len([m for m in snapshot or [] if m.get("role") in ("user", "assistant")]):
                return msgs
        except Exception:
            pass
    return list(snapshot or [])


def _done_rounds(chat_id: str) -> int:
    from . import session_search
    conn = session_search._connect()
    try:
        row = conn.execute("SELECT rounds FROM passes WHERE chat = ?", (chat_id,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _mark(chat_id: str, rounds: int) -> None:
    from . import session_search
    with session_search._LOCK:
        conn = session_search._connect()
        try:
            conn.execute("INSERT OR REPLACE INTO passes(chat, rounds, at) VALUES (?,?,?)", (chat_id, rounds, time.time()))
            conn.commit()
        finally:
            conn.close()


def run(chat_id: str, messages: Optional[List[Dict[str, Any]]] = None, model: str = "", emit=None,
        llm: Optional[Callable[[str, str], str]] = None) -> List[Dict[str, Any]]:
    """The memory pass over one chat, now. Only what is new since its last pass
    is read (with a little before it). Returns the facts kept."""
    from . import memory_extract as _mx, session_search
    if not _mx.enabled():
        return []
    msgs = _messages_for(chat_id, messages)
    rounds = len(session_search.rounds_of(msgs))
    done = _done_rounds(chat_id) if chat_id else 0
    if chat_id and rounds <= done:
        return []
    if llm is None:
        llm, on = choose_llm(model)
    else:
        on = "given"
    with _RUN_LOCK:
        saved, warns = _mx.extract_and_save(msgs, llm, recent_turns=max(3, min(12, rounds - done + 1)),
                                            chat_id=chat_id or None)
    if chat_id:
        _mark(chat_id, rounds)
    LAST.clear()
    LAST.update(chat=chat_id, on=on, saved=[f.get("title") for f in saved], warnings=warns)
    if saved and emit is not None:
        try:
            emit("facts_saved", count=len(saved), titles=[f["title"] for f in saved])
        except Exception:
            pass
    elif warns and not saved:
        print("[hearth.memory_pass] nothing kept: " + "; ".join(warns)[:300], flush=True)
    return saved


def turn_started(chat_id: Optional[str]) -> None:
    """A turn in a chat starts its wait again."""
    with _LOCK:
        p = _PENDING.get(chat_id or "")
        if p:
            p["due"] = time.time() + IDLE_S


def turn_ended(chat_id: Optional[str], messages: List[Dict[str, Any]], model: str = "", emit=None) -> None:
    """A turn finished: the chat gets its pass once it has been quiet IDLE_S."""
    try:
        from . import memory_extract as _mx
        if not _mx.enabled():
            return
    except Exception:
        return
    key = chat_id or ""
    if key.startswith("rem_"):
        return
    with _LOCK:
        _PENDING[key] = {"due": time.time() + IDLE_S, "messages": list(messages or []), "model": model, "emit": emit}
    _ensure_worker()


def now(chat_id: Optional[str], messages: List[Dict[str, Any]], model: str = "", emit=None) -> List[Dict[str, Any]]:
    """The pass at once (before a compaction drops what it would read)."""
    with _LOCK:
        _PENDING.pop(chat_id or "", None)
    try:
        return run(chat_id or "", messages, model, emit)
    except Exception as e:
        print(f"[hearth.memory_pass] failed: {type(e).__name__}: {e}", flush=True)
        return []


def flush() -> int:
    """Every waiting pass, now (tests, and a process about to end)."""
    with _LOCK:
        items = list(_PENDING.items())
        _PENDING.clear()
    for chat, p in items:
        try:
            run(chat, p["messages"], p["model"], p["emit"])
        except Exception as e:
            print(f"[hearth.memory_pass] failed: {type(e).__name__}: {e}", flush=True)
    return len(items)


def pending() -> Dict[str, float]:
    with _LOCK:
        return {k: v["due"] for k, v in _PENDING.items()}


def _catch_up() -> None:
    """Recent chats that changed since their last pass (a run that ended before
    its chat went quiet) get one, a few at most."""
    try:
        from . import chat_sync, session_search
        cutoff = (time.time() - CATCH_UP_DAYS * 86400) * 1000
        n = 0
        for c in chat_sync.listing(40):
            if n >= CATCH_UP_MAX or float(c.get("updated") or 0) < cutoff:
                break
            cid = c["id"]
            if cid.startswith("rem_") or cid in _PENDING:
                continue
            idle = time.time() - float(c.get("updated") or 0) / 1000.0
            d = chat_sync.load(cid) or {}
            if idle < IDLE_S or len(session_search.rounds_of(d.get("messages") or [])) <= _done_rounds(cid):
                continue
            with _LOCK:
                _PENDING.setdefault(cid, {"due": time.time(), "messages": [], "model": "", "emit": None})
            n += 1
    except Exception:
        pass


def _ensure_worker() -> None:
    t = _WORKER["thread"]
    if t is not None and t.is_alive():
        return
    t = threading.Thread(target=_work, daemon=True, name="hearth-memory-pass")
    _WORKER["thread"] = t
    t.start()


def _work() -> None:
    if not _WORKER["caught_up"]:
        _WORKER["caught_up"] = True
        _catch_up()
    while True:
        time.sleep(min(5.0, max(0.2, IDLE_S / 10)))
        with _LOCK:
            if not _PENDING:
                return
            due = [(k, v) for k, v in _PENDING.items() if v["due"] <= time.time()]
        if not due:
            continue
        try:
            from .headless import turn_is_active
            if turn_is_active():
                continue          # a turn owns the model; the pass waits for it
        except Exception:
            pass
        for chat, p in due:
            with _LOCK:
                if _PENDING.get(chat) is not p:
                    continue
                _PENDING.pop(chat, None)
            try:
                run(chat, p["messages"], p["model"], p["emit"])
            except Exception as e:
                print(f"[hearth.memory_pass] failed: {type(e).__name__}: {e}", flush=True)
