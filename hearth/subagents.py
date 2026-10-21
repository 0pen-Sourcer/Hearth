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
  - depth guard: a per-thread counter blocks recursive fan-out at depth >= 3
    so a runaway subagent can't fork-bomb the local LLM

Modes:
  - sync       : spawn_subagent blocks until the child returns or hits max_turns
  - background : returns an agent_id immediately, child's result drops into the
                 parent's next user-role message
"""
from __future__ import annotations

import asyncio
from . import env as _env
import json
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from .chat_call import chat_create

# Personas live as markdown files under hearth/subagents/<slug>.md. The
# YAML-style frontmatter declares allowed_tools, cost_class, and a short
# description; the body is the system prompt the child runs against.
_PERSONA_DIR = Path(__file__).resolve().parent / "subagents"

# Recursion guard — see module docstring.
_DEPTH_ENV = "HEARTH_SUBAGENT_DEPTH"
_MAX_DEPTH = 3

# Depth is tracked per THREAD, not in os.environ. Background subagents each get
# their own thread, so once the server has >1 slot they genuinely overlap, and a
# process-global counter would let one agent's reset clear a sibling's depth
# mid-run — the fork guard would then under-count and allow deeper nesting than
# _MAX_DEPTH. The env var is still honoured as the seed so an externally set
# depth (or a future subprocess) still starts in the right place.
_DEPTH_TLS = threading.local()


def _current_depth() -> int:
    v = getattr(_DEPTH_TLS, "depth", None)
    if v is not None:
        return int(v)
    try:
        return int(os.environ.get(_DEPTH_ENV, "0"))
    except ValueError:
        return 0


def _set_depth(n: int) -> None:
    _DEPTH_TLS.depth = int(n)


def in_subagent() -> bool:
    """True while a sub-agent's own tool call is executing on this thread.

    Sub-agents run with no human watching, so tools use this to refuse the
    blanket auto-approve escape hatch. A user setting HEARTH_AUTO_APPROVE=1 is
    consenting to skip prompts for commands THEY can see scroll past, not to
    hand a background agent unreviewed shell access."""
    return bool(getattr(_DEPTH_TLS, "in_tool", False))


def _set_subagent_tool_ctx(active: bool) -> None:
    _DEPTH_TLS.in_tool = bool(active)

# Per-turn ceiling for any single subagent loop. Tight on purpose: the
# parent should send focused work, not whole projects. 8 turns covers
# "read this PDF chunk, write a summary, return" with room for one retry.
_DEFAULT_MAX_TURNS = 8

# Sidechain transcripts (one JSONL per subagent run) live here so a parent
# can `read_file` them for live progress, and so backgrounded subagents
# leave an audit trail even after the parent has moved on.
_TRANSCRIPT_DIR = Path(_env.get("WORKSPACE")
                       or (Path.home() / "Jarvis")) / "subagents"

# Background subagents put their final result-dict here so anyone (the chat
# bridge, the CLI, or the parent itself via `read_file`) can pick it up.
# Cleared on Hearth restart - background subagents are session-scoped.
_BG_RESULTS: Dict[str, Dict[str, Any]] = {}
_BG_RESULTS_LOCK = threading.Lock()
# agent_ids of background subagents currently in flight (added at launch, removed
# when the worker finishes). Used to stop the parent from fanning out more
# background agents than the server has slots, where extras only queue.
_BG_RUNNING: "set[str]" = set()

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
        # Read the kind:'start' line for persona/name/prompt, kind:'end' for status.
        persona = ""
        label = ""
        prompt = ""
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
                        prompt = (rec.get("prompt") or "").strip()[:160]
                    elif rec.get("kind") == "end":
                        status = "completed" if rec.get("ok") else "failed"
        except OSError:
            pass
        files.append({
            "agent_id": p.stem,
            "persona": persona,
            "name": label,
            "prompt": prompt,
            "status": status,
            "mtime": st.st_mtime,
            "size_bytes": st.st_size,
            "path": str(p),
        })
    files.sort(key=lambda x: -x["mtime"])
    return files[:limit]


_TEAM_CACHE: Dict[str, Tuple[Tuple[float, int], Dict[str, Any]]] = {}


_POINTED = re.compile(r'"(file_path|path|url|query|pattern|command|topic|name)"\s*:\s*"((?:[^"\\]|\\.)*)')


def _pointed_at(args: Any) -> str:
    """The file, page, search or command a tool call is about, short enough
    for a label. `args` is the call's JSON, which may be cut off."""
    m = _POINTED.search(args if isinstance(args, str) else json.dumps(args or {}))
    if not m:
        return ""
    key, val = m.group(1), m.group(2).replace("\\\\", "/").replace('\\"', '"').replace("\\", "/").strip()
    if key in ("file_path", "path"):
        val = val.rstrip("/").rsplit("/", 1)[-1]
    elif key == "url":
        val = re.sub(r"^https?://(www\.)?", "", val).split("/", 1)[0]
    return val if len(val) <= 42 else val[:41] + "…"


def _read_agent(p: Path) -> Dict[str, Any]:
    """What one transcript says about its agent: who it is, what it is doing
    right now, and how it ended if it has."""
    info: Dict[str, Any] = {"id": p.stem, "persona": "", "name": "", "runtime": "", "on": "", "model": "", "prompt": "",
                            "ended": None, "tools_used": 0, "activity": "thinking",
                            "tool": "", "what": "", "blocked": "", "preview": "", "started": "", "talk": None,
                            "heard": None}
    pending: List[str] = []
    whats: List[str] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except (json.JSONDecodeError, ValueError):
                    continue
                k = rec.get("kind")
                if k == "start":
                    info["persona"] = rec.get("persona") or ""
                    info["name"] = rec.get("name") or ""
                    info["runtime"] = rec.get("runtime") or ""
                    info["prompt"] = (rec.get("prompt") or "").strip()[:200]
                    info["started"] = rec.get("ts") or ""
                elif k == "sent":
                    info["talk"] = {"to_id": rec.get("to_id") or "", "to": rec.get("to") or "",
                                    "text": str(rec.get("text") or "")[:200], "ts": rec.get("ts") or ""}
                elif k == "told" and rec.get("from") not in ("the user", "the assistant", "you", ""):
                    # A word from another helper; the room has it answer.
                    info["heard"] = {"from": rec.get("from") or "", "ts": rec.get("ts") or ""}
                elif k == "route" and not info["runtime"]:
                    # One of Hearth's own helpers: what it thinks with, so a
                    # cloud model is never mistaken for one on this PC.
                    from .headless import PROVIDERS, provider_for
                    pid = provider_for(rec.get("base_url") or "")
                    info["model"] = os.path.basename(str(rec.get("model") or "").replace("\\", "/"))
                    info["on"] = "this PC" if pid == "local" else (PROVIDERS[pid][0] if pid in PROVIDERS else "a server")
                    if pid in PROVIDERS:
                        info["runtime"] = pid
                elif k == "assistant_tool_call":
                    pending = [c.get("name") or "" for c in rec.get("calls") or []]
                    whats = [_pointed_at(c.get("args")) for c in rec.get("calls") or []]
                    if rec.get("content"):
                        info["preview"] = str(rec["content"])[:240]
                elif k == "tool_result":
                    info["tools_used"] += 1
                    if pending:
                        pending.pop(0)
                        whats[:1] = []
                    prev = rec.get("preview") or ""
                    if (prev.startswith("(not run:") and "permission" in prev) or                             "requested permissions" in prev:
                        info["blocked"] = rec.get("name") or ""
                elif k == "assistant_final":
                    info["preview"] = str(rec.get("text") or "")[:240]
                elif k == "end":
                    info["ended"] = "stopped" if rec.get("stopped") else "done" if rec.get("ok") else "failed"
                    if not rec.get("ok"):
                        info["preview"] = str(rec.get("error") or info["preview"])[:240]
    except OSError:
        pass
    if pending:
        info["activity"], info["tool"] = "tool", pending[0]
        info["what"] = whats[0] if whats else ""
    return info


def agent_story(agent_id: str) -> Dict[str, Any]:
    """One helper's transcript as what happened, in order: who asked and for
    what, each step with what it was pointed at and what came back, anything
    it was told on the way, and how it ended."""
    if not re.fullmatch(r"sub_\d+_[0-9a-zA-Z]{1,12}", agent_id or ""):
        return {}
    p = _TRANSCRIPT_DIR / f"{agent_id}.jsonl"
    out: Dict[str, Any] = {"id": agent_id, "prompt": "", "by": "", "steps": [], "final": "", "error": "", "ended": None}
    waiting: List[Dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except (json.JSONDecodeError, ValueError):
                    continue
                k = rec.get("kind")
                if k == "start":
                    out["prompt"] = str(rec.get("prompt") or "").strip()[:6000]
                    out["by"] = str(rec.get("by") or "")
                    out["name"] = str(rec.get("name") or rec.get("persona") or "")
                elif k == "assistant_tool_call":
                    for c in rec.get("calls") or []:
                        step = {"tool": c.get("name") or "tool", "what": _pointed_at(c.get("args")), "result": None}
                        out["steps"].append(step)
                        waiting.append(step)
                elif k == "tool_result" and waiting:
                    waiting.pop(0)["result"] = str(rec.get("preview") or "")[:600]
                elif k == "told":
                    out["steps"].append({"told": str(rec.get("text") or "")[:600], "from": str(rec.get("from") or "")})
                elif k == "sent":
                    out["steps"].append({"sent": str(rec.get("text") or "")[:600], "to": str(rec.get("to") or "")})
                elif k == "guard":
                    out["steps"].append({"guard": str(rec.get("reason") or "")[:300], "stop": rec.get("action") == "stop"})
                elif k == "assistant_final":
                    out["final"] = str(rec.get("text") or "")[:4000]
                elif k == "end":
                    out["ended"] = "stopped" if rec.get("stopped") else "done" if rec.get("ok") else "failed"
                    out["error"] = str(rec.get("error") or "")[:600]
    except OSError:
        return {}
    out["steps"] = out["steps"][-200:]
    from . import helper_changes as _changes
    out["changes"] = _changes.summary(agent_id)
    return out


def team_state(since_s: float = 6 * 3600, limit: int = 24) -> List[Dict[str, Any]]:
    """Every helper agent that is running, plus those that finished recently,
    for the Team view. Status is working, done, failed, or stopped (the app
    closed while it ran: its transcript never got an end line and no thread
    owns it any more)."""
    if not _TRANSCRIPT_DIR.exists():
        return []
    with _BG_RESULTS_LOCK:
        running = set(_BG_RUNNING)
    now = time.time()
    out: List[Dict[str, Any]] = []
    seen = set()
    for p in _TRANSCRIPT_DIR.glob("sub_*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        live = p.stem in running
        if not live and now - st.st_mtime > since_s:
            continue
        seen.add(p.stem)
        key = (st.st_mtime, st.st_size)
        hit = _TEAM_CACHE.get(p.stem)
        info = hit[1] if hit and hit[0] == key else _read_agent(p)
        _TEAM_CACHE[p.stem] = (key, info)
        status = info["ended"] or ("working" if live else "stopped")
        out.append({**{k: v for k, v in info.items() if k != "ended"},
                    "status": status, "updated": st.st_mtime,
                    "can_tell": status == "working" and can_tell(p.stem)})
    for gone in set(_TEAM_CACHE) - seen:
        _TEAM_CACHE.pop(gone, None)
    order = {"working": 0, "failed": 1, "stopped": 2, "done": 3}
    out.sort(key=lambda a: (order.get(a["status"], 4), -a["updated"]))
    return out[:limit]


def peek_pending_notifications() -> List[Dict[str, Any]]:
    """Return a COPY of pending notifications WITHOUT clearing the queue.
    Used by the GUI's idle poller — surfaces a heads-up banner ("●
    researcher (Alex) done") the moment a background subagent finishes,
    without waiting for the user to send their next message. The actual
    drain still happens on the next user turn (existing path), so the
    model context still gets the <task-notification> as before."""
    with _NOTIF_LOCK:
        return [dict(n) for n in _PENDING_NOTIFICATIONS]


def card_fields(notif: Dict[str, Any]) -> Dict[str, Any]:
    """What a `subagent_done` event carries for one notification."""
    return {"agent_id": notif.get("agent_id"), "persona": notif.get("persona"), "name": notif.get("name"),
            "status": notif.get("status"), "summary": notif.get("summary"),
            "result_text": notif.get("result_text"), "elapsed_s": notif.get("elapsed_s"),
            "used_tools": notif.get("used_tools") or []}


def still_running(agent_ids) -> "set[str]":
    with _BG_RESULTS_LOCK:
        return set(agent_ids) & _BG_RUNNING


def format_notification_as_user_message(notif: Dict[str, Any]) -> str:
    """Render one notification dict as a <task-notification> block, led by an
    explicit SYSTEM-event banner so the model never mistakes it for the user
    speaking (it's a background subagent YOU spawned reporting back — the user
    did not type this). Inject it with role 'system' where the chat template
    allows; the banner makes the provenance unmistakable either way."""
    src = (notif.get("source") or notif.get("persona") or "").lower()
    if src == "reminder":
        lead = ("A reminder they set has come due; its details are below. Remind "
                "them in your own words")
    elif src == "command":
        lead = ("A command you ran earlier outlived its wait, carried on in the background "
                "and has now ended; its output is below. Continue with it")
    elif src == "video":
        lead = ("A video you started earlier has finished; details below. If it is ready, "
                "show it with check_video_task and tell them in a line")
    elif src == "update":
        lead = ("A helper you dispatched is still working and sent you word part-way; it is "
                "below. Use it, answer with tell_subagent if it asks something, and keep "
                "waiting for its final report")
    elif src in ("schedule", "cron", "timer"):
        lead = ("Something you scheduled has fired; its details are below. Act on it")
    else:
        lead = ("A helper you started has finished; its result is below. Carry on "
                "with the task it was for")
    return (
        f"[Background event, not a message from them. {lead}. Don't thank them "
        f"for it or treat it as a new request.]\n"
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


# First names for helpers the model didn't name. A role ("coder") or a
# product ("Claude Code") is what a helper is, not who; the Team view shows
# that underneath the name.
_FIRST_NAMES = ["Maya", "Noah", "Ivy", "Theo", "Clara", "Lucas", "Aria", "Kai", "Zara", "Leo",
                "Nia", "Omar", "Iris", "Ravi", "Sana", "Milo", "Ezra", "Lena", "Arjun", "Yuki",
                "Tara", "Finn", "Ada", "Jonah", "Mira", "Dev", "Rosa", "Hugo"]


def helper_name(given: str = "", *what: str) -> str:
    """`given` if it is a real name, else a first name no working helper has."""
    g = (given or "").strip()
    generic = {w.strip().lower() for w in what if w} | {"helper", "agent", "subagent", "member", "worker"}
    if g and g.lower() not in generic:
        return g
    with _BG_RESULTS_LOCK:
        running = set(_BG_RUNNING)
    busy = {str((hit[1] or {}).get("name") or "").lower() for aid, hit in _TEAM_CACHE.items() if aid in running}
    free = [n for n in _FIRST_NAMES if n.lower() not in busy] or _FIRST_NAMES
    return random.choice(free)


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

def _local_server_has_model(base: str) -> bool:
    """True only if a local server at `base` is actually up AND has a model
    ready to serve. Cheap-class routing uses this so it diverts to local ONLY
    when local can serve — a cloud-only user (no LM Studio / built-in) would
    otherwise dead-end with 'no model loaded at localhost'."""
    try:
        from . import llmserver as _ls
        st = _ls.status(base)
        if st.get("builtin_running") and st.get("builtin_model"):
            return True
    except Exception:
        pass
    import urllib.request
    hdr = {"Authorization": "Bearer hearth-builtin"}
    # v0: a genuinely-loaded llm/vlm. v1: any listed model (LM Studio only lists
    # loaded ones there). Either positive means local can serve right now.
    try:
        req = urllib.request.Request(base.replace("/v1", "/api/v0") + "/models", headers=hdr)
        with urllib.request.urlopen(req, timeout=1.5) as r:
            for m in json.loads(r.read().decode("utf-8")).get("data", []):
                if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm"):
                    return True
    except Exception:
        pass
    try:
        req = urllib.request.Request(base + "/models", headers=hdr)
        with urllib.request.urlopen(req, timeout=1.5) as r:
            if json.loads(r.read().decode("utf-8")).get("data"):
                return True
    except Exception:
        pass
    return False


def _keep_local() -> bool:
    try:
        from .web import _load_settings
        return bool(_load_settings().get("keep_local_warm"))
    except Exception:
        return False


def route_for(on: str, model: str = "") -> Tuple[Optional[Tuple[str, str, str]], str]:
    """Where one of Hearth's own helpers thinks, from how it was asked for:
    nothing or "main" for the main brain, "local" for the model on this PC,
    or a provider a key is saved for, with one of its models (a loose name is
    matched to the nearest one the provider lists). (route, error)."""
    on = (on or "").strip().lower()
    if on in ("", "main", "brain", "same"):
        return None, ""
    if on in ("local", "pc", "this pc", "here"):
        try:
            from . import llmserver as _ls
            hs = _ls.helper_status()
            if hs.get("running"):
                return (hs["url"], "hearth-builtin", ""), ""
        except Exception:
            pass
        return ("http://localhost:1234/v1", "hearth-builtin", ""), ""
    from .headless import PROVIDERS, settle_model
    from .web import _provider_key, _saved_providers
    saved = [p["id"] for p in _saved_providers()]
    if on not in PROVIDERS:
        return None, f"'{on}' isn't a provider Hearth knows. Keys are saved for: {', '.join(saved) or 'none'}."
    key = _provider_key(on)
    if not key:
        return None, f"No key is saved for {on}. Keys are saved for: {', '.join(saved) or 'none'}."
    url = PROVIDERS[on][1]
    return (url, key, settle_model(url, key, (model or "").strip()) or (model or "").strip()), ""


def _route_for_cost_class(cost_class: str) -> Tuple[str, str, str]:
    """Return (base_url, api_key, model) the subagent should chat against.

    cheap     -> prefer local (LM Studio / built-in) so a fan-out doesn't bill
                 50 cloud calls — but ONLY if local can actually serve; else
                 fall back to the parent's brain (correctness > cost).
    standard  -> inherit parent's current LOCAL_API_BASE / LOCAL_MODEL.
    premium   -> stay on parent's endpoint (if cloud, that's what the user
                 wanted; if local, we don't auto-upgrade — no surprise bills).
    """
    base = os.environ.get("LOCAL_API_BASE", "")
    key = os.environ.get("LOCAL_API_KEY", "") or "hearth-builtin"
    model = os.environ.get("LOCAL_MODEL", "")
    # A helper model, when one is running, is there for exactly this: helpers
    # get their own model and the main one stays free for the conversation.
    # Premium work stays on the main brain.
    if cost_class != "premium":
        try:
            from . import llmserver as _ls
            hs = _ls.helper_status()
            if hs.get("running"):
                return hs["url"], "hearth-builtin", ""
        except Exception:
            pass
    # A model on this PC kept loaded beside a cloud brain is the second brain:
    # every helper short of premium work runs on it, free.
    if cost_class == "cheap" or (cost_class != "premium" and _keep_local()):
        lower = base.lower()
        is_cloud = any(host in lower for host in
                       ("api.x.ai", "googleapis", "openai.com",
                        "anthropic.com", "openrouter.ai", "api.groq.com"))
        # Only divert a cloud parent to local when local is genuinely ready.
        # Otherwise keep the parent's cloud brain so the subagent still runs.
        if is_cloud and _local_server_has_model("http://localhost:1234/v1"):
            base = "http://localhost:1234/v1"
            key = "hearth-builtin"
            model = ""  # let probe pick the loaded one
    return base, key, model


# ---------------------------------------------------------------------------
# Subagent loop  (thin wrapper around the openai SDK)
# ---------------------------------------------------------------------------

# Tools a WILDCARD subagent should not inherit by default. Scoped tight on
# purpose: file ops are already sandboxed to the workspace and run_command
# already gates destructive patterns, so those stay available. What's left is
# browser driving and physical desktop takeover (mouse/keyboard/window) — a
# fanned-out, unattended child grabbing the user's actual input is the wrong
# default. An explicit allowed_tools list still grants any of these by name;
# HEARTH_SUBAGENT_ALLOW_DANGEROUS=1 lifts it globally.
_DANGEROUS_FOR_SUBAGENTS = {
    "browse", "browse_click", "browse_type", "browse_scroll", "browse_key", "browse_close",
    "computer_move", "computer_click", "computer_type", "computer_key",
    "computer_scroll", "computer_drag",
    "desktop_click", "desktop_type", "smart_click", "manage_window", "execute_actions",
}


# Only the main assistant runs these: starting or judging a team, and asking
# the person a question (a helper has no one to ask).
_MAIN_ONLY = {"launch_team", "race_helpers", "helper_changes", "ask_user", "end_session"}


def _load_for_helper(tools: List[Dict[str, Any]], query: str) -> str:
    """load_tools for a helper: adds what matches to this helper's own list."""
    from .tools import unlock_tools
    have = {t["function"]["name"] for t in tools}
    added = []
    for td in unlock_tools(query, touch=False):
        n = td["name"]
        if n in have or n in _DANGEROUS_FOR_SUBAGENTS or n in _MAIN_ONLY or n.startswith(("spawn_", "tell_")):
            continue
        tools.append({"type": "function", "function": {
            "name": n, "description": td.get("description", ""),
            "parameters": td.get("parameters", {"type": "object"})}})
        added.append(td)
    if not added:
        return "No other tools matched that, or the ones that did stay with the main assistant."
    return "Loaded these tools, you can call them now:\n" + "\n".join(
        f"- {td['name']}: {td.get('description', '')[:160]}" for td in added)


def concurrency_capacity() -> Tuple[int, str]:
    """(how many sub-agents can genuinely run at once, one-line reason).

    A cloud endpoint serves concurrent requests, so fanning out wide is free.
    A local llama.cpp server serves one request per slot and queues the rest,
    so spawning six against a single slot isn't parallel, it's a queue with
    extra steps. Surfaced to the model so it sizes the team to the machine."""
    base = os.environ.get("LOCAL_API_BASE", "") or ""
    if not ("localhost" in base or "127.0.0.1" in base):
        return 4, "cloud endpoint, requests run concurrently"
    try:
        from . import llmserver as _ls
        slots = _ls.current_slots()
    except Exception:
        return 1, "local server, assume one at a time"
    if slots > 1:
        return slots, f"local server has {slots} slots"
    return 1, ("local server has 1 slot, so agents queue — raise "
               "Settings > server_parallel_slots to overlap them")


def _filter_tools(allowed: List[str]) -> List[Dict[str, Any]]:
    """Return TOOL_DEFINITIONS filtered to the persona's allowlist in
    OpenAI tool-call shape. `['*']` (wildcard) inherits the parent's
    toolset MINUS the fork primitives and the dangerous set above (so a
    fanned-out child can't shell out / delete / drive the mouse). An explicit
    allowlist is honored verbatim — list a dangerous tool by name to grant it."""
    import os
    from . import TOOL_DEFINITIONS
    block = {"spawn_subagent", "get_subagent_result", "list_subagent_personas", "tell_subagent"}
    if allowed and "core" in allowed:
        # The main assistant's everyday set, with load_tools for the rest. The
        # team tools and asking the person stay with the main assistant, and the
        # mouse and keyboard stay with the person.
        from .tools import _DEFERRED_TOOLS, _LOAD_TOOLS_SCHEMA
        allow = ({t["name"] for t in TOOL_DEFINITIONS if t["name"] not in _DEFERRED_TOOLS}
                 - block - _DANGEROUS_FOR_SUBAGENTS - _MAIN_ONLY)
        out = [_LOAD_TOOLS_SCHEMA]
        allow.add("message_helper")
        for t in TOOL_DEFINITIONS:
            if t["name"] in allow:
                out.append({"type": "function", "function": {
                    "name": t["name"], "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object"})}})
        return out
    if allowed and ("*" in allowed):
        allow = {t["name"] for t in TOOL_DEFINITIONS} - block
        # Wildcard doesn't mean "hand a child the keys to run_command + mouse".
        if not os.environ.get("HEARTH_SUBAGENT_ALLOW_DANGEROUS"):
            allow -= _DANGEROUS_FOR_SUBAGENTS
    else:
        # Explicit list = explicit intent; honor it (dangerous tools included).
        allow = set(allowed) - block
    allow.add("message_helper")
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


# Prepended to every sub-agent's prompt. Background workers with no human
# watching them tended to bail on the first hiccup — a not-found path, a tool
# error — and hand the task back undone. A common one: the parent passes a single
# path and the worker treats it as two and quits. This makes them push through.
_SUBAGENT_RESILIENCE = (
    "You are a focused worker running on your own. FINISH the task you were "
    "given. Do not hand it back half-done.\n"
    "- A tool error is a hurdle, not a stop sign. A path you were given is ONE "
    "path, read it as written. If it is not found, do NOT quit: try it verbatim, "
    "then use find_file / list_directory to locate the real one, then continue.\n"
    "- Never end after a single failed attempt with an excuse like 'the path did "
    "not match' or 'I could not find it'. Try at least two recovery routes first.\n"
    "- Stop only when the task is genuinely done, or you are truly blocked after "
    "real attempts. If blocked, state exactly what you tried and what you need.\n"
)


def _build_system_prompt(persona: Dict[str, Any], user_prompt: str) -> str:
    """Persona body + memory-aware briefing. The memory inject is the
    Hearth twist — child gets project-specific context the source agent
    frameworks don't auto-surface."""
    body = _SUBAGENT_RESILIENCE + "\n" + persona.get("body", "")
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
        "name": persona.get("_label") or "",
        "prompt": user_prompt, "max_turns": max_turns, "by": persona.get("_by") or "",
    })

    try:
        from openai import AsyncOpenAI
    except ImportError:
        out = {"ok": False, "error": "openai package not installed"}
        _append_transcript(tpath, {"kind": "end", **out})
        return out

    base, key, model = persona.get("_route") or _route_for_cost_class(persona.get("cost_class", "standard"))
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
        await _close(client)
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

    with _INBOX_LOCK:
        _INBOX[agent_id] = []
    try:
        return await _subagent_turns(client, model, messages, tools, max_turns, agent_id, tpath,
                                     used, should_cancel, execute_tool)
    finally:
        with _INBOX_LOCK:
            _INBOX.pop(agent_id, None)
        await _close(client)


async def _close(client) -> None:
    """Close a helper's connections in the loop that opened them. A sync helper
    runs in a loop of its own that ends with it; left to be collected later,
    the client tried to use that closed loop and printed a traceback."""
    try:
        await client.close()
    except Exception:
        pass


def _helper_label(tpath: Path, field: str = "name") -> str:
    try:
        with open(tpath, encoding="utf-8") as f:
            first = json.loads(f.readline() or "{}")
        return first.get(field) or first.get("persona") or "a helper"
    except Exception:
        return "a helper"


async def _subagent_turns(client, model, messages, tools, max_turns, agent_id, tpath, used,
                          should_cancel, execute_tool) -> Dict[str, Any]:
    # The same watch the main loop keeps: a helper repeating a call that fails
    # or returns nothing new is told so, then made to answer with what it has.
    from .loop_guard import ToolLoopGuard
    from . import helper_changes as _changes
    guard, wrap_up = ToolLoopGuard(patience=2), False
    for turn in range(max_turns):
        for said in _take_told(agent_id):
            messages.append({"role": "user", "content": said})
        if _stop_asked(agent_id):
            out = {"ok": False, "error": "Stopped before it finished.", "stopped": True,
                   "turns": turn, "used_tools": used, "transcript_path": str(tpath)}
            _append_transcript(tpath, {"kind": "end", **out})
            return out
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
            resp = await chat_create(client,
                model=model,
                messages=messages,
                tools=tools if tools and not wrap_up else None,
                tool_choice="auto" if tools and not wrap_up else None,
                temperature=0.4,
                stream=False,
            )
        except Exception as e:
            # Said the way the chat says it: what went wrong and what to do,
            # not the provider's whole reply.
            try:
                from .errors import classify_api_error
                info = classify_api_error(e, is_local="localhost" in str(getattr(client, "base_url", ""))
                                          or "127.0.0.1" in str(getattr(client, "base_url", "")))
                said = info.hint if info.category not in ("unknown", "other") else f"{type(e).__name__}: {e}"
            except Exception:
                said = f"{type(e).__name__}: {e}"
            out = {"ok": False, "error": said,
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
            # A sub-agent works where no one can be asked, so a call that would
            # prompt runs only on a standing yes: allow-always for the tool, or
            # auto-approve. Without this a sub-agent ran commands the user's own
            # setting would have stopped to ask about.
            from .tools import standing_permission
            _perm = standing_permission(name, args)
            skip = guard.before(name, args)
            if skip is not None:
                content = skip.note
            elif _perm != "allow":
                content = (
                    f"(not run: {name} is set to never run in Settings.)" if _perm == "deny"
                    else f"(not run: {name} needs the user's permission and a sub-agent "
                         f"cannot ask. Finish with what you have and say what is left to "
                         f"do with {name}; the main assistant can ask the user.)")
            elif name == "message_helper":
                content = json.dumps(await message_helper(agent_id, _helper_label(tpath), args), ensure_ascii=False)
            elif name == "load_tools":
                content = _load_for_helper(tools, str(args.get("query") or ""))
            elif (held := _changes.claim(agent_id, name, args)):
                content = held
            else:
                _changes.keep_before(agent_id, name, args)
                try:
                    # Flag the thread for the duration of the call so tools can
                    # tell an autonomous sub-agent from the user-facing loop.
                    _set_subagent_tool_ctx(True)
                    result = execute_tool(name, args)
                    content = str(result) if result is not None else ""
                except Exception as e:
                    content = f"(tool {name} failed: {type(e).__name__}: {e})"
                finally:
                    _set_subagent_tool_ctx(False)
            if skip is None:
                decision = guard.after(name, args, content)
                if decision.action in ("warn", "stop"):
                    content = content[:3600] + "\n\n" + decision.note
                    _append_transcript(tpath, {"kind": "guard", "action": decision.action,
                                               "reason": decision.reason or decision.note})
                if decision.action == "stop":
                    wrap_up = True
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
    from . import helper_changes as _changes
    _changes.release(agent_id)
    result["elapsed_s"] = round(time.time() - started, 2)
    result["agent_id"] = agent_id
    return result


def _bg_worker(p: Dict[str, Any], prompt: str, turns_cap: int,
               agent_id: str, parent_depth: int) -> None:
    """Background subagent runner. Runs in a dedicated thread (not jobs.py
    because the loop is in-process, not a shell subprocess). Stashes the
    final result + fires the completion callback for the chat surface."""
    _set_depth(parent_depth + 1)
    persona_name = p.get("name") or p.get("slug") or ""
    label = p.get("_label") or ""
    try:
        result = _run_one_sync(p, prompt, turns_cap, agent_id)
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}",
                  "agent_id": agent_id}
    finally:
        _set_depth(parent_depth)
        with _BG_RESULTS_LOCK:
            _BG_RUNNING.discard(agent_id)
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


# Messages for a helper that is working, handed over between its steps.
_INBOX: Dict[str, List[str]] = {}
_INBOX_LOCK = threading.Lock()


def can_tell(agent_id: str) -> bool:
    """Whether a helper is one of Hearth's own and still working, so a message
    can reach it."""
    with _INBOX_LOCK:
        if agent_id in _INBOX:
            return True
    from . import external_agents as _xa
    return _xa.can_tell(agent_id)


def tell_subagent(agent_id: str, message: str, sender: str = "the user", reply_to: str = "") -> Dict[str, Any]:
    """Give a working helper a message. It reads it before its next step.
    `reply_to` names a helper it can answer with message_helper."""
    text = str(message or "").strip()
    if not text:
        return {"ok": False, "error": "there is nothing to tell it"}
    hint = f" (Answer with message_helper to {reply_to} if they need one.)" if reply_to else ""
    with _INBOX_LOCK:
        if agent_id in _INBOX:
            _INBOX[agent_id].append(f"A message from {sender} while you work: {text}{hint}")
            _append_transcript(_transcript_path(agent_id), {"kind": "told", "from": sender, "text": text})
            return {"ok": True, "note": "It will read this before its next step."}
    from . import external_agents as _xa
    passed = _xa.tell(agent_id, f"A message from {sender} while you work: {text}{hint}")
    if passed is not None:
        if passed.get("ok"):
            _append_transcript(_transcript_path(agent_id), {"kind": "told", "from": sender, "text": text})
        return passed
    with _BG_RESULTS_LOCK:
        running = agent_id in _BG_RUNNING
    if running:
        return {"ok": False, "error": "This agent's program runs its task to the end and can't take a "
                                      "message part-way."}
    return follow_up(agent_id, text, sender)


def follow_up(agent_id: str, message: str, sender: str = "the user") -> Dict[str, Any]:
    """A message for a helper that has finished: it starts again on it, under
    the same name and role, knowing what it was asked and what it reported."""
    start: Dict[str, Any] = {}
    try:
        with _transcript_path(agent_id).open("r", encoding="utf-8") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except (json.JSONDecodeError, ValueError):
                    continue
                if rec.get("kind") == "start":
                    start = rec
                    break
    except OSError:
        pass
    persona = str(start.get("persona") or "").strip()
    if not persona:
        return {"ok": False, "error": "that helper has already finished, and it can't be picked up again"}
    if not (_PERSONA_DIR / f"{persona}.md").is_file():
        # The transcript keeps the role's name; find the role it names.
        persona = next((p["slug"] for p in list_personas()
                        if str(p.get("name") or "").lower() == persona.lower()), persona)
    story = agent_story(agent_id)
    before = (story.get("final") or story.get("error") or "(it ended without a report)").strip()[:3000]
    prompt = (f"{(story.get('prompt') or start.get('prompt') or '').strip()}\n\n"
              f"You worked on this before and reported:\n{before}\n\n"
              f"Now a follow-up from {sender}: {message}")
    r = spawn_subagent(persona, prompt, mode="background", name=str(start.get("name") or "") or None,
                       by=str(start.get("by") or ""))
    if r.get("ok"):
        r["note"] = ("It had finished, so it started again on this, knowing what it did before. "
                     "Its report arrives like the first one.")
        r["follows"] = agent_id
    return r


# How many messages one helper may send in a task, so two can't talk in circles.
MESSAGES_PER_HELPER = 12
_SENT: Dict[str, int] = {}
# What a helper calls whoever sent it.
_PARENT_NAMES = {"jarvis", "parent", "main", "the assistant", "assistant", "boss"}


def _main_name() -> str:
    try:
        from .persona import NAME
        return NAME
    except Exception:
        return "JARVIS"


def _working_helper(target: str, sender_id: str) -> Optional[Dict[str, Any]]:
    """A helper that is working right now, by id, name or role."""
    want = (target or "").strip().lower()
    for a in team_state():
        if a["status"] != "working" or a["id"] == sender_id:
            continue
        if want in (a["id"].lower(), (a.get("name") or "").lower(), (a.get("persona") or "").lower()):
            return a
    return None


async def message_helper(sender_id: str, sender_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """One helper asks or tells another, and can wait for the answer. Both
    transcripts keep the exchange, so JARVIS and the person can see it."""
    to, text = str(args.get("to") or "").strip(), str(args.get("text") or args.get("message") or "").strip()
    if not to or not text:
        return {"ok": False, "error": "say who it is for (to) and what to tell them (text)"}
    if _SENT.get(sender_id, 0) >= MESSAGES_PER_HELPER:
        return {"ok": False, "error": "you have sent enough messages for this task; finish with what you have"}
    if to.lower() in _PARENT_NAMES or to.lower() == _main_name().lower():
        # Word for whoever sent it, part-way: it reaches JARVIS the way a report does.
        _SENT[sender_id] = _SENT.get(sender_id, 0) + 1
        with _NOTIF_LOCK:
            _PENDING_NOTIFICATIONS.append({
                "agent_id": sender_id, "source": "update", "persona": _helper_label(_transcript_path(sender_id), "persona"),
                "name": sender_name, "status": "update",
                "summary": text[:200], "result_text": text, "transcript_path": str(_transcript_path(sender_id)),
                "elapsed_s": None, "turns": None, "used_tools": []})
        _append_transcript(_transcript_path(sender_id), {"kind": "sent", "to": _main_name(), "to_id": "__main", "text": text[:600]})
        return {"ok": True, "note": "Sent. Keep working; if they answer, it reaches you before your next step."}
    peer = _working_helper(to, sender_id)
    if not peer:
        here = [a.get("name") or a.get("persona") for a in team_state()
                if a["status"] == "working" and a["id"] != sender_id]
        return {"ok": False, "error": f"no helper called '{to}' is working now"
                + (f"; working now: {', '.join(here)}" if here else "; no one else is working")}
    peer_name = peer.get("name") or peer.get("persona") or "them"
    got = tell_subagent(peer["id"], text, sender=sender_name, reply_to=sender_name)
    if not got.get("ok"):
        return got
    _SENT[sender_id] = _SENT.get(sender_id, 0) + 1
    _append_transcript(_transcript_path(sender_id), {"kind": "sent", "to": peer_name, "to_id": peer["id"], "text": text[:600]})
    if not args.get("wait_for_reply"):
        return {"ok": True, "note": f"{peer_name} will read it before their next step."}
    # Their answer arrives in this helper's own inbox; take it from there.
    head = f"A message from {peer_name} while you work: "
    deadline = time.time() + 300
    while time.time() < deadline:
        with _INBOX_LOCK:
            box = _INBOX.get(sender_id) or []
            hit = next((m for m in box if m.startswith(head)), None)
            if hit:
                box.remove(hit)
        if hit:
            reply = hit[len(head):].split(" (Answer with message_helper to ")[0]
            _append_transcript(_transcript_path(sender_id), {"kind": "told", "from": peer_name, "text": reply[:600]})
            return {"ok": True, "reply": reply}
        with _BG_RESULTS_LOCK:
            still = peer["id"] in _BG_RUNNING
            done = _BG_RESULTS.get(peer["id"])
        if not still and not _working_helper(peer["id"], sender_id):
            said = ((done or {}).get("text") or "").strip()
            return {"ok": True, "reply": None,
                    "note": f"{peer_name} finished without answering." + (f" Their result: {said[:1500]}" if said else "")}
        await asyncio.sleep(1)
    return {"ok": False, "error": f"{peer_name} didn't answer within 5 minutes; carry on without it"}


_STOP_ASKED: set = set()


def _stop_asked(agent_id: str) -> bool:
    with _INBOX_LOCK:
        return agent_id in _STOP_ASKED


def stop_subagent(agent_id: str) -> Dict[str, Any]:
    """Stop a working helper. An agent program is ended at once; one of
    Hearth's own stops before its next step."""
    from . import external_agents as _xa
    if _xa.stop(agent_id):
        return {"ok": True, "note": "Stopped."}
    with _BG_RESULTS_LOCK:
        running = agent_id in _BG_RUNNING
    if not running:
        return {"ok": False, "error": "that helper has already finished"}
    with _INBOX_LOCK:
        _STOP_ASKED.add(agent_id)
    return {"ok": True, "note": "It stops before its next step."}


def _take_told(agent_id: str) -> List[str]:
    with _INBOX_LOCK:
        got = _INBOX.get(agent_id) or []
        if agent_id in _INBOX:
            _INBOX[agent_id] = []
    return got


def _agent_for_helpers() -> Optional[str]:
    """The agent program helpers should run on: the one that is the brain,
    when no local model is there to run them. With Claude Code or Codex as
    the brain a local model is optional, so a helper can't depend on one."""
    try:
        from . import claude_brain as _cb
        agent = _cb.current()
    except Exception:
        return None
    if not agent:
        return None
    try:
        from . import llmserver as _ls
        if _ls.helper_status().get("running"):
            return None
    except Exception:
        pass
    base = os.environ.get("LOCAL_API_BASE", "") or "http://localhost:1234/v1"
    try:
        from .headless import port_reachable
        if port_reachable(base) and _local_server_has_model(base):
            return None
    except Exception:
        pass
    return agent


def _spawn_on_agent(agent: str, p: Dict[str, Any], persona: str, prompt: str,
                    label: str, mode: str) -> Dict[str, Any]:
    """Run a helper's task on the agent brain, briefed with the helper's role."""
    from . import external_agents as _xa
    brief = (f"You are working as a helper for the user's assistant, in this role: "
             f"{p.get('name') or persona}.\n\n{p.get('body') or ''}\n\nYour task:\n{prompt}\n\n"
             f"Report what you found or did, briefly, when you are done.")
    done = threading.Event()
    got: Dict[str, Any] = {}

    def _finished(agent_id: str, result: Dict[str, Any]) -> None:
        got.update(result)
        done.set()
        if mode == "background" and _BG_COMPLETION_CB:
            _BG_COMPLETION_CB(agent_id, result)

    r = _xa.spawn(agent, brief, name=label, on_done=_finished, task=prompt, by=p.get("_by") or "")
    if not r.get("ok"):
        return r
    if mode == "background":
        return {"ok": True, "status": "launched", "agent_id": r["agent_id"], "persona": persona,
                "name": label, "transcript_path": str(_transcript_path(r["agent_id"])),
                "note": ("Helper " + (f"'{label}' " if label else "") + "is running in the background on "
                         f"{_xa.AGENTS[agent]['label']}. When it finishes you'll see a <task-notification>.")}
    done.wait(timeout=1800)
    if not done.is_set():
        return {"ok": False, "error": "the helper is still running after 30 minutes", "persona": persona,
                "name": label, "agent_id": r["agent_id"]}
    out = dict(got)
    out["persona"] = persona
    if label:
        out["name"] = label
    return out


def spawn_subagent(persona: str, prompt: str,
                   max_turns: Optional[int] = None,
                   mode: str = "sync",
                   name: Optional[str] = None, by: str = "",
                   route: Optional[Tuple[str, str, str]] = None) -> Dict[str, Any]:
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
        depth = _current_depth()
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

    # Held to the same ceiling as every other surface. The tool told the model
    # an override was capped, and nothing capped it, so any number it passed
    # ran as given.
    from .loop_guard import MAX_TURNS as _CEILING
    try:
        turns_cap = int(max_turns or p.get("max_turns") or _DEFAULT_MAX_TURNS)
    except (TypeError, ValueError):
        turns_cap = _DEFAULT_MAX_TURNS
    turns_cap = max(1, min(turns_cap, _CEILING))
    agent_id = f"sub_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    # Carry the human label through both paths so the toast / notification /
    # transcript can read it. Empty string is fine — surfaces fall back to
    # the persona slug ("researcher") when no name is given.
    label = helper_name(name or "", persona, p.get("name") or "")
    p = dict(p)
    p["_label"] = label
    p["_by"] = by                 # who asked: "you" from the Team view, else the assistant
    if route:
        p["_route"] = tuple(route)    # (base, key, model) picked by hand: a provider or this PC

    on_agent = None if route else _agent_for_helpers()
    if on_agent:
        return _spawn_on_agent(on_agent, p, persona, prompt, label, mode)

    if mode == "background":
        _cap, _cap_why = concurrency_capacity()
        # Don't let the parent stack more background agents than there are slots.
        # Extra ones don't run in parallel, they queue behind the busy slot, which
        # just looks like a hang. Make the parent wait or run this one inline.
        with _BG_RESULTS_LOCK:
            _running = len(_BG_RUNNING)
        if _running >= _cap:
            return {"ok": False, "error": (
                f"{_running} background subagent(s) already running and there "
                f"{'is' if _cap == 1 else 'are'} only {_cap} slot"
                f"{'' if _cap == 1 else 's'} ({_cap_why}). Another background "
                f"agent would just queue behind them, not run in parallel. Wait "
                f"for one to finish (its result arrives as a notification), or "
                f"run this one now with mode='sync', or raise "
                f"Settings > server_parallel_slots.")}
        with _BG_RESULTS_LOCK:
            _BG_RUNNING.add(agent_id)
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
            # Tell the parent how wide it can usefully fan out. Without this it
            # spawns six against a one-slot server and calls the queue a team.
            "concurrency": _cap, "concurrency_note": _cap_why,
        }

    # Sync path — inherits the parent chat's cancel signal if one's
    # wired (web.py's _CANCEL event, CLI's interrupt handler). Background
    # subagents deliberately survive the parent pressing Stop.
    cancel_fn = _resolve_parent_cancel()
    _set_depth(depth + 1)
    try:
        result = _run_one_sync(p, prompt, turns_cap, agent_id,
                               should_cancel=cancel_fn)
    finally:
        _set_depth(depth)
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
