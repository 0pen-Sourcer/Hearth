"""Sub-agent runtime — a scoped, focused LLM loop that a parent agent can
fan out work to.

    parent calls spawn_subagent(persona='pdf_summarizer', prompt='...',
                                allowed_tools=['read_file','summarize_file'])
    -> a fresh LLM loop runs with ONLY those tools + the persona system prompt
    -> final assistant text is returned as one tool_result string

What's different about Hearth's version:
  - cost-class routing: persona declares cheap|standard|premium; cheap
    forces local even when parent is on Grok, so PDF map-reduce of 50
    chunks doesn't cost real money
  - memory-aware briefing: parent's memory_recall(prompt) gets injected
    into the child's system prompt automatically
  - depth guard: HEARTH_SUBAGENT_DEPTH env var blocks recursive fan-out
    at depth >= 3 so a runaway subagent can't fork-bomb the local LLM

Modes:
  - sync       : spawn_subagent blocks until the child returns or hits max_turns
  - background : returns an agent_id immediately, child's result drops into the
                 parent's next user-role message
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Personas live as markdown files under hearth/subagents/<slug>.md. The
# YAML-style frontmatter declares allowed_tools, cost_class, and a short
# description; the body is the system prompt the child runs against.
_PERSONA_DIR = Path(__file__).resolve().parent / "subagents"

# Recursion guard — see module docstring. The depth counter rides in the
# environment so it survives the fact that subagent execution can hop
# between threads (the openai client + execute_tool both do).
_DEPTH_ENV = "HEARTH_SUBAGENT_DEPTH"
_MAX_DEPTH = 3

# Per-turn ceiling for any single subagent loop. Tight on purpose: the
# parent should send focused work, not whole projects. 8 turns covers
# "read this PDF chunk, write a summary, return" with room for one retry.
_DEFAULT_MAX_TURNS = 8

# Sidechain transcripts (one JSONL per subagent run) live here so a parent
# can `read_file` them for live progress, and so backgrounded subagents
# leave an audit trail even after the parent has moved on.
_TRANSCRIPT_DIR = Path(os.environ.get("JARVIS_WORKSPACE")
                       or (Path.home() / "Jarvis")) / "subagents"

# Background subagents put their final result-dict here so anyone (the chat
# bridge, the CLI, or the parent itself via `read_file`) can pick it up.
# Cleared on Hearth restart - background subagents are session-scoped.
_BG_RESULTS: Dict[str, Dict[str, Any]] = {}
_BG_RESULTS_LOCK = threading.Lock()

# Notifications from background subagents queue here. The chat surfaces
# (web.py /chat and hearth_cli.py respond()) drain this queue before
# assembling messages for the next user turn and prepend any waiting
# notifications as user-role messages — the model treats them like any
# other user input.
_PENDING_NOTIFICATIONS: List[Dict[str, Any]] = []
_NOTIF_LOCK = threading.Lock()


def _default_completion_cb(agent_id: str, result: Dict[str, Any]) -> None:
    """Build a notification dict from a subagent result + enqueue it.
    Also fires an OS toast so the user sees it WHILE idle — otherwise the
    notification only surfaces on the next chat turn, which is invisible
    if they walked away."""
    ok = bool(result.get("ok"))
    text = result.get("text", "") if ok else result.get("error", "")
    persona = result.get("persona", "")
    label = result.get("name", "") or ""
    elapsed = result.get("elapsed_s")
    notif = {
        "agent_id": agent_id,
        "persona": persona,
        "name": label,
        "status": "completed" if ok else "failed",
        "summary": (text or "")[:200],
        "result_text": text,
        "transcript_path": result.get("transcript_path", ""),
        "elapsed_s": elapsed,
        "turns": result.get("turns"),
        "used_tools": result.get("used_tools", []),
    }
    with _NOTIF_LOCK:
        _PENDING_NOTIFICATIONS.append(notif)
    # Best-effort OS toast — never raise into the background thread.
    try:
        from . import tools as _t
        # Toast title reads naturally for both anonymous + named runs:
        #   Subagent done — researcher        (no name)
        #   Subagent done — researcher (Alex) (named run)
        who = persona or "unknown"
        if label:
            who = f"{persona or 'subagent'} ({label})"
        title = f"Subagent {'done' if ok else 'failed'} — {who}"
        body = (text or "").strip().splitlines()[0] if text else ""
        if elapsed is not None:
            body = (body + f"  ({elapsed:.0f}s)").strip()
        body = body[:160]
        _t._notify({"title": title, "message": body or "(no output)"})
    except Exception:
        pass


# Host can override this to ALSO push notifications elsewhere (e.g.
# the GUI SSE stream so the user sees "subagent done" before next turn).
# Default is the in-process queue; chat surfaces drain it on next turn.
_BG_COMPLETION_CB = _default_completion_cb


def set_background_completion_callback(cb) -> None:
    """Override the default queue-enqueue callback. The host can use this
    to e.g. ALSO push a desktop toast or SSE event on completion. The cb
    should typically still call _default_completion_cb (or replicate its
    queue behavior) so the next chat turn gets the notification."""
    global _BG_COMPLETION_CB
    _BG_COMPLETION_CB = cb


def drain_pending_notifications() -> List[Dict[str, Any]]:
    """Atomically remove + return all queued background completion
    notifications. Chat surfaces call this before each new user turn."""
    with _NOTIF_LOCK:
        out = list(_PENDING_NOTIFICATIONS)
        _PENDING_NOTIFICATIONS.clear()
        return out


def enqueue_notification(*, source: str, name: str, status: str,
                         result_text: str, summary: str = "",
                         elapsed_s=None) -> Dict[str, Any]:
    """Enqueue a completion notification from a NON-subagent background
    source (e.g. a fired action-reminder) onto the same queue the chat
    surfaces drain each turn. Lets any background producer surface its
    result in chat as a <task-notification>, not just a toast.

    `source` fills the persona slot ('reminder'), `name` the label
    (the reminder text). `result_text` is the full tool output the model
    relays; `summary` is the short heads-up the GUI idle banner shows."""
    notif = {
        "agent_id": "",
        "persona": source,
        "name": name or "",
        "status": status,
        "summary": (summary or result_text or "")[:200],
        "result_text": result_text,
        "transcript_path": "",
        "elapsed_s": elapsed_s,
        "turns": None,
        "used_tools": [],
    }
    with _NOTIF_LOCK:
        # Collapse accidental doubles (two watcher ticks / two processes firing
        # the same reminder) — an identical notification already waiting in the
        # queue would otherwise render as two identical cards in one turn.
        for _n in _PENDING_NOTIFICATIONS:
            if (_n.get("persona") == notif["persona"]
                    and _n.get("name") == notif["name"]
                    and _n.get("result_text") == notif["result_text"]):
                return _n
        _PENDING_NOTIFICATIONS.append(notif)
    return notif


def list_subagent_activity(limit: int = 50) -> List[Dict[str, Any]]:
    """Browse recent subagent runs by reading the transcript directory.
    Each entry: {agent_id, persona, name, mtime, size_bytes, status}.
    Sorted newest-first. Used by the Logs tab's subagent subview so the
    user can audit what each agent did — they keep multiplying and
    finding them in a flat folder is painful."""
    if not _TRANSCRIPT_DIR.exists():
        return []
    files = []
    for p in _TRANSCRIPT_DIR.glob("sub_*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        # Read the kind:'start' line for persona/name, kind:'end' for status.
        persona = ""
        label = ""
        status = "running"
        try:
            with p.open("r", encoding="utf-8") as f:
                for ln in f:
                    if not ln.strip():
                        continue
                    try:
                        rec = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("kind") == "start":
                        persona = rec.get("persona") or persona
                        label = rec.get("name") or label
                    elif rec.get("kind") == "end":
                        status = "completed" if rec.get("ok") else "failed"
        except OSError:
            pass
        files.append({
            "agent_id": p.stem,
            "persona": persona,
            "name": label,
            "status": status,
            "mtime": st.st_mtime,
            "size_bytes": st.st_size,
            "path": str(p),
        })
    files.sort(key=lambda x: -x["mtime"])
    return files[:limit]


def peek_pending_notifications() -> List[Dict[str, Any]]:
    """Return a COPY of pending notifications WITHOUT clearing the queue.
    Used by the GUI's idle poller — surfaces a heads-up banner ("●
    researcher (Alex) done") the moment a background subagent finishes,
    without waiting for the user to send their next message. The actual
    drain still happens on the next user turn (existing path), so the
    model context still gets the <task-notification> as before."""
    with _NOTIF_LOCK:
        return [dict(n) for n in _PENDING_NOTIFICATIONS]


def format_notification_as_user_message(notif: Dict[str, Any]) -> str:
    """Render one notification dict as a <task-notification> block, led by an
    explicit SYSTEM-event banner so the model never mistakes it for the user
    speaking (it's a background subagent YOU spawned reporting back — the user
    did not type this). Inject it with role 'system' where the chat template
    allows; the banner makes the provenance unmistakable either way."""
    src = (notif.get("source") or notif.get("persona") or "").lower()
    if src == "reminder":
        lead = ("A reminder you set earlier has come due; its details are below. "
                "Deliver it to the user now, naturally")
    elif src in ("schedule", "cron", "timer"):
        lead = ("A scheduled background event you set up has fired; its details "
                "are below. Act on it for the user")
    else:
        lead = ("A subagent you dispatched earlier has finished; its result is "
                "below. Continue the user's original task with it")
    return (
        f"[SYSTEM NOTIFICATION — automated background event, NOT a message from "
        f"the user. {lead}; do not thank the "
        f"user for it or treat it as a new user request.]\n"
        f"<task-notification>\n"
        f"  <agent-id>{notif.get('agent_id', '')}</agent-id>\n"
        f"  <persona>{notif.get('persona', '')}</persona>\n"
        f"  <name>{notif.get('name', '')}</name>\n"
        f"  <status>{notif.get('status', '')}</status>\n"
        f"  <elapsed-seconds>{notif.get('elapsed_s', '')}</elapsed-seconds>\n"
        f"  <transcript-path>{notif.get('transcript_path', '')}</transcript-path>\n"
        f"  <summary>{notif.get('summary', '')}</summary>\n"
        f"  <result>{notif.get('result_text', '')}</result>\n"
        f"</task-notification>"
    )


def _transcript_path(agent_id: str) -> Path:
    _TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    return _TRANSCRIPT_DIR / f"{agent_id}.jsonl"


def _append_transcript(path: Path, entry: Dict[str, Any]) -> None:
    entry = {**entry, "ts": datetime.now().isoformat(timespec="seconds")}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Persona loading
# ---------------------------------------------------------------------------

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_persona(path: Path) -> Dict[str, Any]:
    """Read a persona file and return its frontmatter dict + body. Required
    frontmatter keys: name, description, allowed_tools (list). Optional:
    cost_class (defaults 'standard'), max_turns (defaults 8)."""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    if not m:
        raise ValueError(f"{path.name}: missing YAML frontmatter")
    fm_block, body = m.group(1), m.group(2).strip()
    fm: Dict[str, Any] = {}
    for ln in fm_block.split("\n"):
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        k = k.strip()
        v = v.strip()
        # List values are inline JSON-ish: [a, b, c]
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [t.strip().strip('"\'') for t in v[1:-1].split(",") if t.strip()]
        else:
            fm[k] = v.strip('"\'')
    fm["body"] = body
    return fm


def list_personas() -> List[Dict[str, Any]]:
    """All personas under hearth/subagents/. Sorted by name. Empty list when
    the directory doesn't exist yet — the spawn_subagent tool reports the
    miss cleanly instead of erroring out at import time."""
    if not _PERSONA_DIR.is_dir():
        return []
    out = []
    for fn in sorted(os.listdir(_PERSONA_DIR)):
        if not fn.endswith(".md"):
            continue
        try:
            fm = _parse_persona(_PERSONA_DIR / fn)
            out.append({
                "slug": fn[:-3],
                "name": fm.get("name", fn[:-3]),
                "description": fm.get("description", ""),
                "allowed_tools": fm.get("allowed_tools", []) or [],
                "cost_class": fm.get("cost_class", "standard"),
                "max_turns": int(fm.get("max_turns") or _DEFAULT_MAX_TURNS),
            })
        except Exception as e:
            out.append({"slug": fn[:-3], "error": f"{type(e).__name__}: {e}"})
    return out


def _load_persona(slug: str) -> Dict[str, Any]:
    """Strict: raise ValueError if missing or malformed."""
    path = _PERSONA_DIR / f"{slug}.md"
    if not path.is_file():
        available = ", ".join(p["slug"] for p in list_personas()
                              if "error" not in p) or "(none)"
        raise ValueError(f"persona '{slug}' not found. Available: {available}")
    return _parse_persona(path)


# ---------------------------------------------------------------------------
# Cost-class -> endpoint routing
# ---------------------------------------------------------------------------

def _route_for_cost_class(cost_class: str) -> Tuple[str, str, str]:
    """Return (base_url, api_key, model) the subagent should chat against.

    cheap     -> force local (LM Studio / built-in) regardless of parent's
                 brain. PDF fan-out shouldn't bill 50 cloud calls.
    standard  -> inherit parent's current LOCAL_API_BASE / LOCAL_MODEL.
    premium   -> stay on parent's endpoint (if cloud, that's what the user
                 wanted; if local, we don't auto-upgrade — no surprise bills).
    """
    base = os.environ.get("LOCAL_API_BASE", "")
    key = os.environ.get("LOCAL_API_KEY", "") or "not-needed"
    model = os.environ.get("LOCAL_MODEL", "")
    if cost_class == "cheap":
        # If parent is cloud, swap to local. If parent is already local,
        # no change.
        lower = base.lower()
        is_cloud = any(host in lower for host in
                       ("api.x.ai", "googleapis", "openai.com",
                        "anthropic.com", "openrouter.ai"))
        if is_cloud:
            base = "http://localhost:1234/v1"
            key = "hearth-builtin"
            model = ""  # let probe pick the loaded one
    return base, key, model


# ---------------------------------------------------------------------------
# Subagent loop  (thin wrapper around the openai SDK)
# ---------------------------------------------------------------------------

def _filter_tools(allowed: List[str]) -> List[Dict[str, Any]]:
    """Return TOOL_DEFINITIONS filtered to the persona's allowlist in
    OpenAI tool-call shape. `['*']` (wildcard) inherits the parent's
    FULL toolset. Always silently drops `spawn_subagent` /
    `get_subagent_result` even under wildcard so depth=3 can't be reached
    via nested forks."""
    from . import TOOL_DEFINITIONS
    block = {"spawn_subagent", "get_subagent_result", "list_subagent_personas"}
    # Wildcard: every Hearth tool except the fork primitives.
    if allowed and ("*" in allowed):
        allow = {t["name"] for t in TOOL_DEFINITIONS} - block
    else:
        allow = set(allowed) - block
    out = []
    for t in TOOL_DEFINITIONS:
        if t["name"] not in allow:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object"}),
            },
        })
    return out


def _build_system_prompt(persona: Dict[str, Any], user_prompt: str) -> str:
    """Persona body + memory-aware briefing. The memory inject is the
    Hearth twist — child gets project-specific context the source agent
    frameworks don't auto-surface."""
    body = persona.get("body", "")
    try:
        from . import memory as _mem
        mem_block = _mem.recall_for_prompt(user_prompt, max_chars=600, limit=2)
    except Exception:
        mem_block = ""
    if mem_block:
        body = body + "\n\n" + mem_block
    return body


def _run_coro_quiet(coro):
    """Run a coroutine on a private event loop whose teardown won't spew the
    Windows 'Event loop is closed' race — a background subagent's AsyncOpenAI/
    httpx client finishes its TLS aclose() AFTER asyncio.run() would have closed
    the loop, and CPython's proactor reports that through the loop's exception
    handler. We drain async-gens, give pending transport-close callbacks one
    tick to run, and swallow only that specific RuntimeError."""
    loop = asyncio.new_event_loop()

    def _quiet(_loop, ctx):
        exc = ctx.get("exception")
        if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
            return
        _loop.default_exception_handler(ctx)

    loop.set_exception_handler(_quiet)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(asyncio.sleep(0))  # let transport closes fire
        except Exception:
            pass
        loop.close()


async def _run_subagent_async(
    persona: Dict[str, Any],
    user_prompt: str,
    max_turns: int,
    agent_id: str,
    should_cancel=None,
) -> Dict[str, Any]:
    """One subagent turn-loop. Returns {ok, text, turns, used_tools, transcript_path}.

    Writes a JSONL sidechain transcript at ~/Jarvis/subagents/<agent_id>.jsonl
    so the parent (or anyone) can `read_file` it for live progress.
    """
    tpath = _transcript_path(agent_id)
    _append_transcript(tpath, {
        "kind": "start", "persona": persona.get("name") or persona.get("slug"),
        "prompt": user_prompt, "max_turns": max_turns,
    })

    try:
        from openai import AsyncOpenAI
    except ImportError:
        out = {"ok": False, "error": "openai package not installed"}
        _append_transcript(tpath, {"kind": "end", **out})
        return out

    base, key, model = _route_for_cost_class(persona.get("cost_class", "standard"))
    client = AsyncOpenAI(base_url=base, api_key=key, timeout=180.0)

    # If model is empty, probe the endpoint for whatever's loaded. Mirrors
    # the autodetect path in headless.py without duplicating its full ctx
    # math (subagents don't need it — they run tight prompts).
    if not model:
        try:
            r = await client.models.list()
            for m in (r.data or []):
                if getattr(m, "id", ""):
                    model = m.id
                    break
        except Exception:
            pass
    if not model:
        out = {"ok": False, "error": f"no model loaded at {base}"}
        _append_transcript(tpath, {"kind": "end", **out})
        return out

    tools = _filter_tools(persona.get("allowed_tools", []))
    sys_prompt = _build_system_prompt(persona, user_prompt)
    _append_transcript(tpath, {
        "kind": "route", "base_url": base, "model": model,
        "cost_class": persona.get("cost_class"),
        "sys_prompt_chars": len(sys_prompt),
        "tools_exposed": [t["function"]["name"] for t in tools],
    })

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    used: List[str] = []

    from . import execute_tool

    for turn in range(max_turns):
        # Abort propagation — sync subagents inherit the parent's cancel
        # signal. Background agents intentionally do NOT inherit it (the
        # whole point of background is that they survive the parent turn).
        if should_cancel is not None:
            try:
                if should_cancel():
                    out = {"ok": False, "error": "cancelled by parent",
                           "turns": turn, "used_tools": used,
                           "transcript_path": str(tpath)}
                    _append_transcript(tpath, {"kind": "end", **out})
                    return out
            except Exception:
                pass
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
                temperature=0.4,
                stream=False,
            )
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}",
                   "turns": turn, "used_tools": used,
                   "transcript_path": str(tpath)}
            _append_transcript(tpath, {"kind": "end", **out})
            return out

        choice = resp.choices[0]
        msg = choice.message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            text = (msg.content or "").strip()
            _append_transcript(tpath, {"kind": "assistant_final", "text": text})
            out = {"ok": True, "text": text, "turns": turn + 1,
                   "used_tools": used, "model": model,
                   "transcript_path": str(tpath)}
            _append_transcript(tpath, {"kind": "end", **out})
            return out

        # Echo the assistant's tool-call message into history so the next
        # turn can append matching tool results.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            } for tc in tool_calls],
        })
        _append_transcript(tpath, {
            "kind": "assistant_tool_call",
            "content": msg.content or "",
            "calls": [{"name": tc.function.name, "args": tc.function.arguments[:400]}
                      for tc in tool_calls],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            # Record name + args so the parent's completion card can show
            # what each subagent actually did (web_search → which query,
            # web_fetch → which URL, run_command → which cmd).
            used.append({"name": name, "args": args})
            try:
                result = execute_tool(name, args)
                content = str(result) if result is not None else ""
            except Exception as e:
                content = f"(tool {name} failed: {type(e).__name__}: {e})"
            content_capped = content[:4000]
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": content_capped,
            })
            _append_transcript(tpath, {
                "kind": "tool_result", "name": name,
                "chars": len(content), "preview": content[:400],
            })

    out = {"ok": False, "error": "max_turns reached without final text",
           "turns": max_turns, "used_tools": used,
           "transcript_path": str(tpath)}
    _append_transcript(tpath, {"kind": "end", **out})
    return out


# ---------------------------------------------------------------------------
# Public entrypoints (called from tool handlers in hearth/tools.py)
# ---------------------------------------------------------------------------

def _run_one_sync(p: Dict[str, Any], prompt: str, turns_cap: int,
                  agent_id: str, should_cancel=None) -> Dict[str, Any]:
    """Wrapper around _run_subagent_async that picks the right asyncio entry
    based on whether we're already inside a running loop. Reused by both
    sync spawn (block-and-return) and background spawn (thread)."""
    started = time.time()
    try:
        try:
            loop = asyncio.get_running_loop()
            fut = asyncio.run_coroutine_threadsafe(
                _run_subagent_async(p, prompt, turns_cap, agent_id,
                                    should_cancel=should_cancel), loop)
            result = fut.result(timeout=180.0 * turns_cap)
        except RuntimeError:
            result = _run_coro_quiet(
                _run_subagent_async(p, prompt, turns_cap, agent_id,
                                    should_cancel=should_cancel))
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    result["elapsed_s"] = round(time.time() - started, 2)
    result["agent_id"] = agent_id
    return result


def _bg_worker(p: Dict[str, Any], prompt: str, turns_cap: int,
               agent_id: str, parent_depth: int) -> None:
    """Background subagent runner. Runs in a dedicated thread (not jobs.py
    because the loop is in-process, not a shell subprocess). Stashes the
    final result + fires the completion callback for the chat surface."""
    os.environ[_DEPTH_ENV] = str(parent_depth + 1)
    persona_name = p.get("name") or p.get("slug") or ""
    label = p.get("_label") or ""
    try:
        result = _run_one_sync(p, prompt, turns_cap, agent_id)
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}",
                  "agent_id": agent_id}
    finally:
        os.environ[_DEPTH_ENV] = str(parent_depth)
    # Stamp the persona name + human label onto the result so the toast +
    # notification can render "researcher (Alex) done" instead of
    # "unknown done".
    if persona_name and not result.get("persona"):
        result["persona"] = persona_name
    if label and not result.get("name"):
        result["name"] = label
    with _BG_RESULTS_LOCK:
        _BG_RESULTS[agent_id] = result
    if _BG_COMPLETION_CB:
        try:
            _BG_COMPLETION_CB(agent_id, result)
        except Exception:
            pass


def get_subagent_result(agent_id: str) -> Dict[str, Any]:
    """Poll for a background subagent's result. Returns:
      - {ok: True, status: 'running'} if still in flight
      - {ok: True, status: 'done', result: {...}} on completion
      - {ok: False, error: 'no such agent'} otherwise
    The chat surface auto-injects a synthetic notification when a bg
    subagent finishes (no polling needed in normal use), but this tool
    exists for the model to peek deliberately."""
    with _BG_RESULTS_LOCK:
        if agent_id in _BG_RESULTS:
            return {"ok": True, "status": "done",
                    "result": _BG_RESULTS[agent_id]}
    # Not done. Check transcript existence to distinguish "running" from
    # "no such id".
    tpath = _TRANSCRIPT_DIR / f"{agent_id}.jsonl"
    if tpath.is_file():
        return {"ok": True, "status": "running",
                "transcript_path": str(tpath)}
    return {"ok": False, "error": f"no subagent with id {agent_id}"}


def spawn_subagent(persona: str, prompt: str,
                   max_turns: Optional[int] = None,
                   mode: str = "sync",
                   name: Optional[str] = None) -> Dict[str, Any]:
    """Spawn a sub-agent with a scoped tool allowlist + tight prompt.

    mode='sync' (default): block until the child returns its final text.
    mode='background': spawn in a thread, return immediately with
        {ok, agent_id, transcript_path, status: 'launched'}. When the
        child finishes, the chat surface auto-injects a notification as
        the parent's next user-role message. The parent can ALSO call
        get_subagent_result(agent_id) or read_file(transcript_path) for
        live progress.

    name (optional): a human label for this run — useful when the parent
        spawns multiple instances of the same persona (e.g. three
        researchers "Alex", "Beth", "Carla" each on a different topic).
        Appears in the completion toast, the task-notification, and the
        transcript so the parent can address each by name.

    Depth-guarded at 3 nested forks so a runaway agent can't fork-bomb
    the local LLM. Cost-class routing in the persona frontmatter swaps
    cheap subagents to local even if the parent is on cloud.
    """
    if not persona or not prompt:
        return {"ok": False, "error": "persona and prompt are required"}

    try:
        depth = int(os.environ.get(_DEPTH_ENV, "0"))
    except ValueError:
        depth = 0
    if depth >= _MAX_DEPTH:
        return {"ok": False, "error":
                f"subagent depth limit reached ({_MAX_DEPTH}). "
                f"Parent should consolidate work instead of forking deeper."}

    try:
        p = _load_persona(persona)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    turns_cap = int(max_turns or p.get("max_turns") or _DEFAULT_MAX_TURNS)
    agent_id = f"sub_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    # Carry the human label through both paths so the toast / notification /
    # transcript can read it. Empty string is fine — surfaces fall back to
    # the persona slug ("researcher") when no name is given.
    label = (name or "").strip()
    p = dict(p)
    p["_label"] = label

    if mode == "background":
        t = threading.Thread(
            target=_bg_worker, name=f"hearth-subagent-{agent_id}",
            args=(p, prompt, turns_cap, agent_id, depth), daemon=True,
        )
        t.start()
        return {
            "ok": True, "status": "launched", "agent_id": agent_id,
            "persona": persona, "name": label,
            "transcript_path": str(_transcript_path(agent_id)),
            "note": ("Subagent " +
                     (f"'{label}' " if label else "") +
                     "is running in the background. When it finishes, "
                     "you'll see a <task-notification> as the next user "
                     "message. You can also peek via "
                     f"read_file({str(_transcript_path(agent_id))!r}) or "
                     f"get_subagent_result(agent_id={agent_id!r})."),
        }

    # Sync path — inherits the parent chat's cancel signal if one's
    # wired (web.py's _CANCEL event, CLI's interrupt handler). Background
    # subagents deliberately survive the parent pressing Stop.
    cancel_fn = _resolve_parent_cancel()
    os.environ[_DEPTH_ENV] = str(depth + 1)
    try:
        result = _run_one_sync(p, prompt, turns_cap, agent_id,
                               should_cancel=cancel_fn)
    finally:
        os.environ[_DEPTH_ENV] = str(depth)
    result["persona"] = persona
    if label:
        result["name"] = label
    return result


# Parent surfaces (web.py /chat, CLI respond()) register a callable here
# so sync subagents bail when the user hits Stop. Returns True when set
# (= cancelled). Background subagents intentionally skip this check.
_PARENT_CANCEL_FN = None


def set_parent_cancel_check(fn) -> None:
    """fn() -> bool; truthy = parent wants this subagent to stop."""
    global _PARENT_CANCEL_FN
    _PARENT_CANCEL_FN = fn


def _resolve_parent_cancel():
    return _PARENT_CANCEL_FN
