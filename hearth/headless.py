"""Headless / scriptable mode for Hearth.

The CLI (`hearth_cli.py`) is built for humans — interactive prompts, voice,
spinners, prompt_toolkit. Headless mode is for everything else: testing,
CI, scripting, automation, and (the original motivation) so another agent
or a regression harness can drive Hearth without typing.

USAGE
-----

  # Single prompt, JSONL events to stdout
  python -m hearth.headless --prompt "find my latest screenshot and open it"

  # With reasoning visible (model thinks out loud)
  python -m hearth.headless --prompt "..." --think

  # Pretty text instead of JSONL (for human eyeballs)
  python -m hearth.headless --prompt "..." --format text

  # Override the model / endpoint / context
  python -m hearth.headless --prompt "..." --model qwen/qwen3.5-9b
  LOCAL_API_BASE=http://other-host:1234/v1 python -m hearth.headless --prompt "..."

EVENTS
------

Each line of JSONL stdout is one object. Event types:

  {"type": "user",        "content": "..."}
  {"type": "thinking",    "content": "..."}   # only when --think
  {"type": "tool_call",   "name": "...", "args": {...}}
  {"type": "tool_result", "name": "...", "content": "...", "ms": N}
  {"type": "assistant",   "content": "..."}    # final reply
  {"type": "done",        "duration_ms": N, "iterations": N, "reason": "..."}
  {"type": "error",       "message": "..."}

PERMISSION POLICY
-----------------

Headless mode bypasses the interactive `[y/n/a/N]` prompts on risky tools —
there's no human there to answer. By default this is enabled (the runner is
expected to be supervising). To enforce strict permissions, set the
environment variable `JARVIS_AUTO_APPROVE=0` before invocation; risky tool
calls will then auto-DENY instead of auto-allowing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from . import system_prompt, execute_tool, to_openai_tools, TOOL_DEFINITIONS
from .loop_guard import ToolLoopGuard, MAX_TURNS
from .errors import classify_api_error

LOCAL_API_BASE = os.getenv("LOCAL_API_BASE", "http://localhost:1234/v1")
# Real API key for cloud endpoints (Gemini, OpenRouter, OpenAI, etc.).
# Local LM Studio ignores it, so the harmless default is fine. To use a
# cloud model: set LOCAL_API_BASE + LOCAL_API_KEY + LOCAL_MODEL.
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY") or os.getenv("OPENAI_API_KEY") or "hearth-builtin"


def _is_local_endpoint(base: str) -> bool:
    """True for localhost / LAN endpoints (LM Studio, Ollama, vLLM, llama.cpp).
    Used to gate LM-Studio-specific request params that cloud APIs reject."""
    b = (base or "").lower()
    return any(h in b for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1",
                                "192.168.", "10.", "host.docker.internal"))


# Cloud models that 400 on `reasoning_effort` (plain grok-4, grok-3 — only
# grok-3-mini / grok-4.3+ expose it). Learned at runtime so we send it once,
# remember the rejection, and don't pay a failed round-trip again.
_NO_REASONING_EFFORT: set = set()


def _is_reasoning_param_error(e: Exception) -> bool:
    """True when an API error looks like the model rejecting `reasoning_effort`,
    so the caller can transparently retry without it."""
    msg = str(getattr(e, "message", "") or e).lower()
    status = getattr(e, "status_code", None)
    if "reasoning_effort" in msg or "reasoning effort" in msg:
        return True
    if (status == 400 or "invalid" in msg or "unsupported" in msg
            or "unknown" in msg or "does not support" in msg) and "reasoning" in msg:
        return True
    return False


def _cloud_reasoning_effort(base: str, think: bool, model: str):
    """`reasoning_effort` value for a cloud model when /think is off (skip the
    reasoning pass), or None to send nothing. /think on → None (default).
    OpenAI bottoms out at "minimal"; xAI/Gemini/OpenRouter accept "none"."""
    if think or (model or "").lower() in _NO_REASONING_EFFORT:
        return None
    return "minimal" if "openai.com" in (base or "").lower() else "none"


DEFAULT_MAX_DEPTH = 40  # Bumped 20 → 40 after a live LM Studio PDF read hit
                        # the ceiling mid-task: Qwen 3.5 Harmonic 9B was
                        # doing legitimate map-reduce-by-hand on a 514-page
                        # PDF (extract → chunked reads → summarize) and
                        # ran out of turn budget at 20. Override with
                        # `--max-depth N` or `HEARTH_MAX_TURNS=N`.
# Inject a wrap-up nudge to the model when we cross this fraction of the
# cap so it has a chance to write a STATE_SNAPSHOT before we hard-stop.
# Without it, the cap fires silently and the model has no idea on the next
# turn that it was interrupted — it just starts the work over.
WRAP_UP_AT_FRACTION = 0.75
# Same context/compaction constants as the CLI (hearth_cli.py) so the bridge +
# GUI auto-compact identically.
#
# Default bumped from 8K → 32K so the GUI doesn't immediately overflow on a
# cloud brain (where 128K-1M is normal). The old 8K against a 131K Grok was
# the actual root cause of the "model forgot the conversation" bug — persona
# alone is ~8K tokens, so trim chopped the persona in half before history
# even arrived. The autodetect_context() probe overrides this when LM Studio
# reports a real loaded_context_length.
CONTEXT_TOKENS = int(os.getenv("JARVIS_CONTEXT", "32768"))
RESERVED_OUTPUT = int(os.getenv("JARVIS_RESERVED_OUTPUT", "2048"))
COMPACT_AT = float(os.getenv("JARVIS_COMPACT_AT", "0.75"))


def autodetect_context(model_id: str) -> Optional[int]:
    """Ask LM Studio for the loaded context length of the active model. Copied
    verbatim from hearth_cli.autodetect_context so the bridge uses the SAME logic
    (probe /v1/models then /api/v0/models, then the single-model detail endpoint).
    Returns None on total miss.

    Passes Authorization so probes against our built-in server (which requires
    `hearth-builtin` API key) don't generate a 401 storm in the server log
    every time chat turns over. Without this every chat call leaked one 401.
    """
    import urllib.request
    _key = os.environ.get("LOCAL_API_KEY") or LOCAL_API_KEY or ""
    _hdr = {"Authorization": f"Bearer {_key}"} if _key else {}
    candidates = (
        f"{LOCAL_API_BASE}/models",
        LOCAL_API_BASE.replace("/v1", "/api/v0") + "/models",
    )
    fields = ("loaded_context_length", "n_ctx",
              "max_context_length", "context_length")
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=_hdr)
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read().decode())
        except Exception:
            continue
        for m in data.get("data", []):
            if m.get("id") != model_id:
                continue
            for key in fields:
                v = m.get(key)
                if isinstance(v, int) and v > 0:
                    return v
    try:
        detail_url = LOCAL_API_BASE.replace("/v1", "/api/v0") + f"/models/{model_id}"
        req = urllib.request.Request(detail_url, headers=_hdr)
        with urllib.request.urlopen(req, timeout=2) as r:
            m = json.loads(r.read().decode())
        for key in fields:
            v = m.get(key)
            if isinstance(v, int) and v > 0:
                return v
    except Exception:
        pass
    return None


def resolve_context_tokens(model_id: str) -> tuple:
    """Return (tokens, source) for the given model on the current LOCAL_API_BASE.

    Single source of truth for "what context window are we using?". Both
    the chat path and the /api/context-budget endpoint call this so the
    GUI's bottom bar + ring + chat budget all agree. Without one helper
    the ring was showing 32K default while the chat path used 200K via
    provider table.

    Precedence:
      1. JARVIS_CONTEXT env var (hard pin — testing / debugging)
      2. Endpoint probe (LM Studio / Ollama / built-in expose loaded_context)
      3. Per-provider known-context table (cloud endpoints don't expose ctx)
      4. CONTEXT_TOKENS default (32K)
    """
    pinned = os.getenv("JARVIS_CONTEXT")
    if pinned:
        try:
            v = int(pinned)
            if v > 0:
                return v, f"JARVIS_CONTEXT={v // 1024}K (env pin)"
        except ValueError:
            pass
    # Built-in server: use the context we ACTUALLY booted it with, not the model's
    # theoretical max. A "1M" GGUF advertises max_position_embeddings ~200K via
    # /v1/models, but we only loaded (say) 32K — trusting the advertised ceiling
    # over-packs the prompt and overflows the server. _proc_info["ctx"] is the truth.
    try:
        from . import llmserver as _ls
        from urllib.parse import urlparse as _up
        _pi = getattr(_ls, "_proc_info", {}) or {}
        if _pi.get("ctx") and _pi.get("url") and LOCAL_API_BASE:
            if _up(_pi["url"]).port == _up(LOCAL_API_BASE).port:
                return int(_pi["ctx"]), "built-in server (loaded ctx)"
    except Exception:
        pass
    probed = autodetect_context(model_id)
    if probed:
        return probed, "endpoint probe"
    # Per-provider known-context table. Cloud providers almost never
    # expose ctx via /v1/models (the probe above tries; comes back None
    # for nearly all of them), so this table is the fallback. Values are
    # the REAL ceiling each provider advertises. Update here whenever a
    # new model lands with a different ctx. Tracked in IDEAS.md so the
    # next session knows to revisit.
    base = (LOCAL_API_BASE or "").lower()
    m = (model_id or "").lower()
    known = None
    if "api.x.ai" in base:
        # Grok 4 / 4-fast = 2M, Grok 4.3 = 1M, Grok build = 256K,
        # Grok 2 = 131K. Sources: x.ai/api docs as of 2026-06.
        if "grok-4-fast" in m or "grok-4-mini" in m: known = 2_000_000
        elif "grok-4.3" in m or "grok-4-3" in m: known = 1_000_000
        elif "grok-4" in m: known = 2_000_000  # base grok-4 = 2M
        elif "grok-build" in m: known = 256_000
        elif "grok-2" in m: known = 131_072
        else: known = 131_072  # safe Grok default for unknown ids
    elif "generativelanguage.googleapis" in base:
        # Every modern Gemini (1.5 / 2.x / 3.x, flash and pro) is 1M+. Only the
        # long-retired gemini-1.0 / bare gemini-pro were 32K. Match on "is it the
        # old one" so a fresh version string (e.g. 3.5) never falls back to 32K.
        known = 32_768 if ("1.0" in m or m in ("gemini-pro", "gemini-pro-vision")) else 1_000_000
    elif "api.openai.com" in base:
        # GPT-4.1 = 1M, GPT-4o / o3 / o1 = 128K
        if "gpt-4.1" in m: known = 1_000_000
        elif "gpt-4o" in m or "o3" in m or "o1" in m: known = 128_000
        else: known = 128_000
    elif "api.anthropic.com" in base or "claude" in m:
        # Claude Sonnet 4.x / Opus 4.x = 200K (1M Sonnet via beta header,
        # but we don't set that; conservative default).
        known = 200_000
    elif "openrouter.ai" in base:
        # OpenRouter is a passthrough — assume 128K conservatively.
        known = 128_000
    if known:
        return known, f"provider table ({known // 1024}K)"
    return CONTEXT_TOKENS, f"default {CONTEXT_TOKENS // 1024}K"


# Phrases that signal "I'm going to do X" without an actual tool call.
# Kept in sync with hearth_cli.py's _YIELD_TRIGGERS.
_YIELD_TRIGGERS = (
    "i'll search", "i'll check", "i'll look", "i'll find",
    "i'll open", "i'll run", "i'll fetch", "i'll grab",
    "let me search", "let me check", "let me look", "let me find",
    "let me open", "let me run", "let me fetch",
    "i'm going to search", "i'm going to check", "i'm going to look",
    "i'm going to run", "i'm going to open",
    "going to run a", "going to check that", "going to look that up",
    "i will search", "i will check", "i will run",
    "one moment", "one sec while i", "one second",
    "hold on while i", "give me a sec",
)


def _looks_like_yield(text: str) -> bool:
    """Conservative yield detector — only fires on clear-cut patterns."""
    if not text:
        return False
    t = text.lower().strip()
    if len(t) > 500:
        return False
    return any(trigger in t for trigger in _YIELD_TRIGGERS)


# ---------------------------------------------------------------------------
# Event emitters
# ---------------------------------------------------------------------------

def emit_json(event_type: str, **fields: Any) -> None:
    """Write one JSONL event to stdout, flushed."""
    print(json.dumps({"type": event_type, **fields}, ensure_ascii=False, default=str), flush=True)


def emit_text(event_type: str, **fields: Any) -> None:
    """Write a colorless, human-readable event line."""
    if event_type == "user":
        print(f"\n>>> USER: {fields.get('content', '')}", flush=True)
    elif event_type == "thinking":
        body = fields.get('content', '').strip()
        # indent for readability
        print(f"\n[thinking]\n{body}\n[/thinking]", flush=True)
    elif event_type == "tool_call":
        args_str = json.dumps(fields.get('args', {}), ensure_ascii=False)
        if len(args_str) > 150:
            args_str = args_str[:150] + "…"
        print(f"\n  ⚙ [tool] {fields.get('name')} {args_str}", flush=True)
    elif event_type == "tool_result":
        body = fields.get('content', '')
        head = body.split('\n', 1)[0][:200]
        more = "" if '\n' not in body else f"  (+{body.count(chr(10))} more lines)"
        print(f"     ↳ {head}{more}", flush=True)
    elif event_type in ("assistant_chunk", "thinking_chunk"):
        # Suppress streaming token spam in text mode — it buries the [tool]
        # lines. The final `assistant` / `thinking` events print the full text.
        return
    elif event_type == "context_budget":
        print(f"[ctx] budget {fields.get('effective_budget')} tok "
              f"(window {fields.get('context_tokens')}, tools {fields.get('tool_tokens')})",
              flush=True)
    elif event_type == "context_state":
        print(f"[ctx] {fields.get('used')}/{fields.get('budget')} tok "
              f"({fields.get('pct')}%) · {fields.get('messages')} msgs", flush=True)
    elif event_type == "compacted":
        print(f"[ctx] compacted → {fields.get('after')} msgs "
              f"({fields.get('pct')}% of budget)", flush=True)
    elif event_type == "assistant":
        print(f"\n<<< ASSISTANT:\n{fields.get('content', '')}", flush=True)
    elif event_type == "done":
        bits = []
        if 'iterations' in fields:
            bits.append(f"{fields['iterations']} iteration(s)")
        if 'duration_ms' in fields:
            bits.append(f"{fields['duration_ms']} ms")
        if 'reason' in fields:
            bits.append(f"reason={fields['reason']}")
        print(f"\n[done — {' · '.join(bits)}]", flush=True)
    elif event_type == "error":
        print(f"\n[ERROR] {fields.get('message')}", file=sys.stderr, flush=True)
    else:
        print(json.dumps({"type": event_type, **fields}, default=str), flush=True)


# ---------------------------------------------------------------------------
# Builtin-server tool calling (manual injection)
# ---------------------------------------------------------------------------
# Hearth's builtin llama_cpp.server can't inject the OpenAI `tools` param on its
# own — that needs a function-calling chat_format, and that handler returns empty
# / crashes the stream on real prompts. So for the builtin ONLY we hand-inject
# the <tools> spec as text (the format Qwen/Hermes are trained on) and recover
# <tool_call> from the reply via hearth.tool_call_parser. Cloud + LM Studio keep
# the native `tools` API — it works there.

def _manual_tools_block(tools: List[Dict[str, Any]]) -> str:
    lines = ["You can call functions. Their signatures are inside "
             "<tools></tools>:", "<tools>"]
    for t in tools:
        fn = t.get("function", t)
        lines.append(json.dumps(fn, ensure_ascii=False))
    lines.append("</tools>")
    lines.append('To call one, emit a JSON object inside <tool_call></tool_call>:')
    lines.append('<tool_call>\n{"name": "func_name", "arguments": {...}}\n</tool_call>')
    lines.append("Emit several <tool_call> blocks to call several functions. "
                 "Results return inside <tool_response> tags — use them to answer.")
    return "\n".join(lines)


def _to_manual_messages(msgs: List[Dict[str, Any]],
                        tools_block: str) -> List[Dict[str, Any]]:
    """Rewrite the standard OpenAI message list into the text form the builtin
    server's native template accepts: append <tools> to the system message,
    render assistant tool_calls back to <tool_call> text, and fold tool results
    into a user-role <tool_response> turn (role=tool 500s on the builtin).
    Consecutive tool results merge into one user turn."""
    out: List[Dict[str, Any]] = []
    sys_done = False
    pending: List[str] = []

    def _flush():
        if pending:
            out.append({"role": "user", "content": "\n".join(pending)})
            pending.clear()

    for m in msgs:
        role = m.get("role")
        if role == "tool":
            pending.append(f"<tool_response>\n{m.get('content','')}\n</tool_response>")
            continue
        _flush()
        if role == "system" and not sys_done:
            sys_done = True
            content = m.get("content", "")
            if tools_block:
                content = content + "\n\n" + tools_block
            out.append({"role": "system", "content": content})
        elif role == "assistant" and m.get("tool_calls"):
            parts = [m["content"]] if m.get("content") else []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                parts.append(
                    f'<tool_call>\n{{"name": "{fn.get("name","")}", '
                    f'"arguments": {fn.get("arguments") or "{}"}}}\n</tool_call>')
            out.append({"role": "assistant", "content": "\n".join(parts).strip()})
        else:
            out.append(dict(m))
    _flush()
    return out


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

async def run_once(
    prompt: str,
    *,
    emit,
    model: Optional[str] = None,
    think: bool = False,
    max_depth: int = DEFAULT_MAX_DEPTH,
    temperature: float = 0.7,
    history: Optional[List[Dict[str, Any]]] = None,
    permission_check=None,
    should_cancel=None,
    supervised: bool = True,
) -> int:
    """Run a single prompt against the model. Returns process exit code.

    `history` lets the caller pass prior conversation messages (without the
    system prompt — that's injected here). When set, the bridge is doing a
    multi-turn conversation: each `[{role,content}, ...]` entry is prepended
    after the system prompt, then the new `prompt` is appended as a user msg.
    Without it, behavior is the same as before: single-shot prompt.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        emit("error", message="openai package not installed — run `pip install openai`")
        return 2

    # If we're talking to Hearth's BUILTIN llama-cpp server, it was booted
    # with --api_key hearth-builtin. Any other key (including the default
    # "hearth-builtin") gets a 401 on the auto-detect probe + every chat request.
    # Detect by probing: does this base answer with our builtin's signature?
    _effective_key = LOCAL_API_KEY
    try:
        from . import llmserver as _ls
        st = _ls.status(LOCAL_API_BASE)
        if st.get("builtin_running") and st.get("builtin_url"):
            # Match on port, not exact URL string (localhost vs 127.0.0.1)
            from urllib.parse import urlparse
            if urlparse(LOCAL_API_BASE).port == urlparse(st["builtin_url"]).port:
                _effective_key = "hearth-builtin"
    except Exception:
        pass
    # Only our builtin server uses the hearth-builtin key. That's the exclusive
    # signal to hand-inject tools as text instead of via the API `tools` param.
    _use_manual_tools = (_effective_key == "hearth-builtin")
    client = AsyncOpenAI(base_url=LOCAL_API_BASE, api_key=_effective_key, timeout=180.0)

    # Auto-pick the LOADED model (not just downloaded) — LM Studio's v1
    # /models endpoint returns everything it knows about, but only one is
    # actually loaded into VRAM at a time. Picking a non-loaded one triggers
    # "Failed to load model: Operation canceled" errors.
    if not model:
        model = os.getenv("LOCAL_MODEL", "") or None
    if not model:
        try:
            import urllib.request as _urlreq
            v0 = LOCAL_API_BASE.replace("/v1", "/api/v0")
            _hdr = ({"Authorization": f"Bearer {_effective_key}"}
                    if _effective_key else {})
            _req = _urlreq.Request(f"{v0}/models", headers=_hdr)
            with _urlreq.urlopen(_req, timeout=3) as r:
                v0_data = json.loads(r.read().decode("utf-8"))
            for m in v0_data.get("data", []):
                if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm"):
                    model = m.get("id")
                    break
        except Exception:
            pass
    if not model:
        # Last resort — first CHAT model in the v1 list. Skip image/video/music/
        # speech/embedding models (Gemini lists imagen/veo/lyria/native-audio/
        # embedding alongside chat models, and picking one yields an empty reply
        # or a 400). May still fail if the picked model isn't actually loaded.
        _NONCHAT = ("imagine-image", "imagine-video", "imagine-audio", "imagen",
                    "veo-", "lyria", "native-audio", "-tts", "tts-", "aqa",
                    "robotics", "embedding", "embed-")
        try:
            models_resp = await client.models.list()
            _chat = [m.id for m in models_resp.data
                     if m.id and not any(p in m.id.lower() for p in _NONCHAT)]
            model = _chat[0] if _chat else (models_resp.data[0].id if models_resp.data else None)
            if not model:
                raise RuntimeError("no models listed")
        except Exception as e:
            emit("error", message=f"Could not auto-detect a model on {LOCAL_API_BASE}: {e}")
            return 3

    # Proactive memory: fold the saved facts most relevant to this prompt into
    # the system message, fenced + authoritative, so the model uses what it
    # knows instead of ignoring the passive index. Bounded; adds nothing when
    # nothing matches. Same behavior as the CLI's _prepare_context.
    from . import memory as _mem
    _sys = system_prompt()
    # Inject the current local time on every turn so the model can reason about
    # "what were we doing" / "is it late?" / "remind me tomorrow" without a
    # get_time call, and read the passage of time across turns.
    import datetime as _dt
    _now = _dt.datetime.now().astimezone()
    _sys += (f"\n\nCurrent local time: {_now.strftime('%Y-%m-%d %H:%M')} "
             f"({_now.strftime('%A')}, tz {_now.tzname() or _now.strftime('%z')}).")
    # Where the brain lives — weigh resource vs cost for heavy tools.
    if _is_local_endpoint(os.environ.get("LOCAL_API_BASE", "")):
        _sys += ("\n\nRuntime: LOCAL model server — free + private, but it serves ONE "
                 "request at a time on limited VRAM, so big parallel fan-outs (large "
                 "teams, many concurrent subagents) SERIALIZE and crawl. Keep teams "
                 "small (~3-4) and prefer one capable pass over a wide fan-out.")
    else:
        _sys += ("\n\nRuntime: CLOUD model endpoint — every token costs the user real "
                 "money. Parallel agents run fast here, but each one multiplies spend. "
                 "Only fan out a team when the task genuinely needs it, and use the "
                 "smallest team that does the job.")
    _block = _mem.recall_for_prompt(prompt)
    if _block:
        _sys += "\n\n" + _block
    messages: List[Dict[str, Any]] = [{"role": "system", "content": _sys}]
    if history:
        # Drop any system entries the caller smuggled in — only ours is canonical.
        for h in history:
            if h.get("role") and h.get("role") != "system":
                messages.append({"role": h["role"], "content": h.get("content", "")})
    # A "notification flush" turn carries no real user message — the GUI fires
    # it (idle) only to let the model surface a due reminder / finished subagent.
    # The notification itself becomes the trailing user-role message (Claude
    # Code's isMeta pattern: the model reads + reports it, but no user bubble was
    # ever rendered for it). Sentinel must match ui.html's flush call.
    NOTIFY_FLUSH = "__HEARTH_NOTIFY_FLUSH__"
    _is_flush = prompt.strip() == NOTIFY_FLUSH

    # Background subagent completions + due reminders queue up between turns;
    # drain them so the model sees the results. On a normal turn they're inserted
    # as SYSTEM events just before the user's message (provenance: a background
    # task reporting back isn't the user talking). On a flush turn they ARE the
    # turn, appended as user-role messages so the sequence still ends on a user
    # turn (strict local chat templates require that).
    _flushed = 0
    _notif_ctx: List[str] = []   # notifications to fold into this turn's user msg
    try:
        from . import subagents as _sa
        pending = _sa.drain_pending_notifications()

        def _emit_card(notif):
            emit("subagent_done", agent_id=notif.get("agent_id"),
                 persona=notif.get("persona"), name=notif.get("name"),
                 status=notif.get("status"),
                 summary=notif.get("summary"),
                 result_text=notif.get("result_text"),
                 elapsed_s=notif.get("elapsed_s"),
                 used_tools=notif.get("used_tools") or [])

        if _is_flush:
            for notif in pending:
                xml = _sa.format_notification_as_user_message(notif)
                messages.append({"role": "user", "content": xml})
                _flushed += 1
                _emit_card(notif)
        else:
            # Collect notifications to fold into THIS turn's user message (below).
            # NOT inserted as mid-conversation system messages — strict templates
            # (Qwythos / Qwen3.5) reject any system message that isn't the first.
            for notif in pending:
                _notif_ctx.append(_sa.format_notification_as_user_message(notif))
                _emit_card(notif)
    except Exception:
        pass

    if _is_flush:
        # Race: the queue was already drained by a real user turn between the
        # GUI's peek and this flush. Nothing to surface — don't feed the model a
        # bare sentinel. Tell the GUI to drop its empty placeholder and bail.
        if _flushed == 0:
            emit("done", reply="", iterations=0)
            return
        # No emit("user") — the GUI rendered no user bubble for this turn.
    else:
        # Fold any background notifications into the user turn (template-safe:
        # avoids a mid-conversation system message, which strict Qwen3.5/Qwythos
        # templates reject with "System message must be at the beginning"). The
        # GUI shows the clean prompt via emit().
        _user_content = ("\n\n".join(_notif_ctx) + "\n\n---\n\n" + prompt) if _notif_ctx else prompt
        messages.append({"role": "user", "content": _user_content})
        emit("user", content=prompt)

    tools = to_openai_tools()
    # Context budget so a long history (the GUI re-sends a growing one every
    # turn) gets trimmed to fit instead of overflowing — the CLI does this in
    # _prepare_context; without it run_once eventually overflows and LM Studio
    # drops the user turn ("No user query found in messages"). Tool schemas ride
    # every prompt, so reserve them + output. trim_to_budget structurally keeps a
    # surviving user turn, so no extra invariant is needed.
    from .tools import (trim_to_budget, estimate_tokens, CHARS_PER_TOKEN,
                        compact_history, dedup_tool_results, sanitize_tool_pairing,
                        _truncate_kept_tool_results)
    # Single source of truth — see resolve_context_tokens() above. Same call
    # is reused by the /api/context-budget endpoint so the GUI ring + bottom
    # bar agree with what the chat path actually uses.
    context_tokens, context_source = resolve_context_tokens(model)
    # SAFETY: if the SERVER's loaded context is smaller than what we're
    # about to pack into a prompt, we'll get "Requested tokens exceed
    # context window" 500s. This happened on launch day: GUI defaulted to
    # 8K builtin while persona+tools alone needed 19K. Now we cross-check.
    # If we detect a server-reported ctx (autodetect_context > 0) and it's
    # smaller than our estimate of persona+tools+output overhead, emit a
    # loud warning so the GUI can surface it instead of silent 500s.
    # Compute tool-schema overhead FIRST (the warning math below reads it).
    # Earlier ordering bug had the warning read _tool_tokens before it was
    # assigned — UnboundLocalError on every chat. Image gen, all chat,
    # everything 500'd at this line.
    try:
        _tool_tokens = len(json.dumps(tools)) // CHARS_PER_TOKEN
    except Exception:
        _tool_tokens = 0
    _budget = max(2048, context_tokens - _tool_tokens)
    try:
        from . import system_prompt as _sp
        _sys_tok = len(_sp()) // 4
    except Exception:
        _sys_tok = 8000  # conservative fallback
    _est_overhead = _sys_tok + _tool_tokens + RESERVED_OUTPUT
    if context_tokens < _est_overhead:
        emit("context_warning",
             loaded_ctx=context_tokens,
             estimated_overhead=_est_overhead,
             hint=(
                 f"Loaded model context ({context_tokens}) is smaller than "
                 f"Hearth's prompt overhead ({_est_overhead} = persona "
                 f"{_sys_tok}t + tools {_tool_tokens}t + reserved {RESERVED_OUTPUT}t). "
                 f"Chats will 500. Reload the model at >={_est_overhead + 4096} "
                 f"context (Models tab → expand model → ctx slider)."
             ))
    # Emit so the GUI / CLI can show what budget actually got picked.
    try:
        emit("context_budget",
             context_tokens=context_tokens,
             tool_tokens=_tool_tokens,
             effective_budget=_budget,
             # Use the source string we computed above — covers env override,
             # endpoint probe, and the per-provider table (Grok 1M, Gemini 1M,
             # GPT-4o 128K, Claude 200K, etc).
             source=("env JARVIS_CONTEXT" if os.getenv("JARVIS_CONTEXT")
                     else context_source))
    except Exception:
        pass
    t_start = time.time()
    iterations = 0
    # Tool-loop guard (outcome-hash based, tiered skip/warn/stop). Shared with
    # the CLI — see hearth/loop_guard.py for the full rationale. Replaces the
    # old magic-number "stop after N calls of one tool" counter.
    guard = ToolLoopGuard()
    _force_answer = False
    # Tracks if we already nudged the model in this run_once invocation
    # (anti-yield wrapper).
    _yielded_already = False
    # Set to a hint string when the last tool result contained a "NEXT STEP:"
    # directive. If the next assistant message doesn't act on it (just narrates
    # what happened), we fire a stronger nudge than the trigger-phrase one.
    _pending_next_step: Optional[str] = None
    _nextstep_nudged: bool = False
    # Tracks whether we've already injected the wrap-up nudge for this turn.
    # Fires exactly once when iterations crosses WRAP_UP_AT_FRACTION * max_depth.
    _wrapup_nudged: bool = False
    _wrapup_threshold = max(1, int(max_depth * WRAP_UP_AT_FRACTION))

    for depth in range(max_depth):
        iterations = depth + 1
        # User hit Stop in the GUI — bail out cleanly between turns.
        if should_cancel is not None and should_cancel():
            emit("cancelled", message="stopped by user")
            break
        # Wrap-up nudge: at 75% of the cap, tell the model how many turns
        # it has left and ask it to write a STATE_SNAPSHOT if the task
        # isn't done. Without this, the cap fires silently at the limit
        # and the model has zero memory of being interrupted — on the next
        # user message it just starts the work over. By injecting a system-
        # role nudge at 75%, the model's NEXT response naturally ends with
        # the snapshot, which then persists into chat history so future
        # turns can resume.
        if not _wrapup_nudged and iterations >= _wrapup_threshold:
            _wrapup_nudged = True
            turns_left = max_depth - iterations
            messages.append({
                "role": "user",
                "content": (
                    f"[SYSTEM] You've used {iterations} of your {max_depth} agentic turns "
                    f"this prompt — {turns_left} remain. If the task isn't complete, "
                    f"end your NEXT response with a `STATE_SNAPSHOT` block in this exact "
                    f"shape so the user can say 'continue' and you can resume cleanly:\n\n"
                    f"```STATE_SNAPSHOT\n"
                    f"task: <one-line restatement of what the user asked>\n"
                    f"done: <what you've accomplished so far, concrete>\n"
                    f"remaining: <what still needs to happen>\n"
                    f"next_step: <the exact next tool call or action>\n"
                    f"resume_from: <file/offset/state to pick up from, e.g. "
                    f"'D:/books/.../history_extract.txt line 2001'>\n"
                    f"```\n\n"
                    f"If the task IS complete or near done, ignore this and finish "
                    f"normally."
                ),
            })
            emit("nudge", reason=f"wrap-up at {iterations}/{max_depth} — asked for STATE_SNAPSHOT")
        # Auto-compact at 75% of budget (matches CLI behavior). Two-stage:
        #   1) At 75% with a SAFE boundary (no in-flight tool chain) → real
        #      summarize-compact via compact_history. Replaces middle chunk
        #      with a 4-bullet summary preserving the chat thread.
        #   2) Hard trim_to_budget as last-resort. Used to be the ONLY path,
        #      which is why "websearch then question later" → "hey what's up?"
        #      (the user's history got truncated to nothing because the bridge
        #      never actually summarized, it just dropped messages).
        # Cheap pre-pass every turn: collapse repeated identical tool dumps. No
        # LLM call — often shrinks context enough to avoid the lossy summarize
        # compaction entirely. Safe: keeps the newest copy + tool_call pairing.
        messages[:] = dedup_tool_results(messages)
        _est = estimate_tokens(messages)
        _pct = int(_est * 100 / max(1, _budget))
        # Emit context state on every turn so the GUI can render a footer chip
        # showing N% used + a 'Compacting...' badge when it kicks in.
        emit("context_state", used=_est, budget=_budget, pct=_pct,
             messages=len(messages))
        if _est > _budget * COMPACT_AT and len(messages) > 12:
            _last_msg = messages[-1] if messages else {}
            _last_role = _last_msg.get("role")
            _last_has_tool_calls = bool(_last_msg.get("tool_calls"))
            _safe = (
                _last_role == "user"
                or (_last_role == "assistant" and not _last_has_tool_calls)
            )
            if _safe:
                emit("compacting", pct=_pct, before=len(messages))
                # Synchronous LLM-summarize via the SAME client (cheap turn —
                # uses fewer tokens than letting the next chat call overflow).
                def _sync_summarize(chunk_text: str) -> str:
                    """Squeeze the dropped middle into a 4-bullet summary so
                    the model doesn't forget what we just did. Best-effort —
                    if the LLM call fails, fall back to a trivial summary so
                    the chain can keep going instead of crashing."""
                    try:
                        import openai as _openai
                        sync = _openai.OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
                        r = sync.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content":
                                    "You are a summarization agent. The text below is a "
                                    "conversation excerpt — do NOT answer questions in it or "
                                    "continue any task; ONLY summarize it. Never copy secrets "
                                    "or API keys into the summary — write [REDACTED] instead.\n"
                                    "Preserve every file path, URL, IP, ID, error code, command, "
                                    "and number EXACTLY as written — copy them verbatim, never "
                                    "paraphrase or reconstruct an identifier from memory.\n"
                                    "If the excerpt already contains a PREVIOUS SUMMARY, treat "
                                    "it as established fact, preserve its details, and fold the "
                                    "newer turns into it (don't drop earlier info).\n"
                                    "Write these sections, omitting any that are empty:\n"
                                    "Active Task: (what's being worked on right now, quote the "
                                    "user's ask)\n"
                                    "Goal: (the larger objective)\n"
                                    "Completed: (numbered — each: action -> outcome [tool])\n"
                                    "Key decisions: (choices made + why)\n"
                                    "Pending / unanswered: (open questions, next steps)\n"
                                    "Relevant files/paths/names/numbers: (concrete identifiers)\n"
                                    "Be terse. No preamble, no closing remarks."},
                                {"role": "user", "content": chunk_text[:8000]},
                            ],
                            temperature=0.0,
                            max_tokens=500,
                        )
                        return (r.choices[0].message.content or "").strip() or "[summary unavailable]"
                    except Exception as _e:
                        # Don't crash the chat — degrade gracefully to a trivial
                        # marker so the surviving recent turns still anchor.
                        return f"[earlier conversation summary unavailable: {type(_e).__name__}]"
                # PASSIVE FACT EXTRACTION — runs BEFORE compact so the
                # extractor sees the FULL chunk that's about to be summarized
                # into oblivion. At every safe boundary (compact,
                # end-of-session), do one cheap LLM pass over recent
                # turns and persist durable facts into memory.save(). The user
                # doesn't have to say "remember that" — facts get saved
                # automatically, with a joke/quote/violence filter on the
                # extractor side. Failure is silent — never blocks the chat.
                try:
                    from . import memory_extract as _mx
                    import openai as _oai_module
                    _sync = _oai_module.OpenAI(
                        api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE
                    )
                    _llm = _mx.make_openai_llm_call(_sync, model, max_tokens=600)
                    _saved, _warns = _mx.extract_and_save(
                        messages, _llm, recent_turns=6
                    )
                    if _saved:
                        emit("facts_saved", count=len(_saved),
                             titles=[f["title"] for f in _saved])
                except Exception as _mx_err:
                    # Don't surface as an error event — fact extraction failing
                    # mid-compact shouldn't look scary in the GUI. Just log.
                    emit("nudge", reason=f"fact_extract skipped: {type(_mx_err).__name__}")
                # Aggressive target so the kept tail (which may include
                # big browse / read_file results) gets re-tightened if
                # the first pass leaves us still over budget.
                _target = max(2000, _budget * CHARS_PER_TOKEN // 2)
                messages[:] = compact_history(messages, _sync_summarize,
                                              keep_recent=8,
                                              target_chars=_target)
                _est = estimate_tokens(messages)
                emit("compacted", after=len(messages), used=_est,
                     pct=int(_est * 100 / max(1, _budget)))
        # Hard trim as last-resort guard if compact didn't fire (unsafe boundary
        # or already past budget). This is the truncate path — keeps the
        # invariant "prompt fits" but drops messages, so we'd rather compact.
        if estimate_tokens(messages) > _budget:
            messages[:] = trim_to_budget(messages, _budget, RESERVED_OUTPUT)
        # Belt-and-suspenders: trim_to_budget now guarantees a user role, but
        # ANY upstream caller that hands us a history without one will crash
        # LM Studio's Jinja with "No user query found in messages". Cheap to
        # check and idempotent — synthesize a continue-marker if needed.
        if not any(m.get("role") == "user" for m in messages):
            messages.append({"role": "user",
                             "content": "Continue using the results above."})
        # CRITICAL: enforce tool_call<->tool_result pairing on EVERY send,
        # unconditionally. trim_to_budget (which also sanitizes) is gated on
        # being over budget, but compact_history drops the array UNDER budget,
        # so on the compaction turn the trim is skipped and an orphaned pairing
        # ships straight to the server → 400 bad_request right after compact.
        # This is the GUI-visible failure; the CLI trims unconditionally so it
        # was already covered. Idempotent on a valid array.
        messages[:] = sanitize_tool_pairing(messages)
        try:
            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
            }
            _tools_block_text = ""
            if _force_answer:
                # Spiral guard tripped last turn: withhold tools so the model
                # is FORCED to produce a text answer instead of calling again.
                # One-shot — clear it so the next turn can use tools normally.
                _force_answer = False
            else:
                # Rebuilt each turn so tools the model unlocks via `load_tools`
                # (tool-diet) appear on the next request.
                _tools = to_openai_tools()
                if _use_manual_tools:
                    _tools_block_text = _manual_tools_block(_tools)
                else:
                    kwargs["tools"] = _tools
                    kwargs["tool_choice"] = "auto"
            if _use_manual_tools:
                # Builtin server: send tools as text + tool history rewritten to
                # the <tool_call>/<tool_response> form its native template reads.
                kwargs["messages"] = _to_manual_messages(messages, _tools_block_text)
            # `chat_template_kwargs` is an LM-Studio / llama.cpp-specific extra.
            # Cloud OpenAI-compatible endpoints (Gemini, OpenAI, OpenRouter)
            # reject unknown fields with a 400. Only send it to local servers.
            # Both `chat_template_kwargs` and the `stop=["<think>"]` hack are
            # local-only. Cloud models (Grok rejects `stop` outright; Gemini
            # rejects `chat_template_kwargs`) stream reasoning via a dedicated
            # `reasoning_content` channel, so neither workaround is needed.
            if _is_local_endpoint(LOCAL_API_BASE):
                # enable_thinking=False is the non-destructive way to skip reasoning
                # on models that respect it. No stop=["<think>"] anymore: on a
                # reasoning-FORCED model it halted at the first think tag (empty
                # reply) and hid the reasoning we now surface.
                kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": think}}
            else:
                # Cloud: actually disable reasoning at the API when think is
                # off, not just drop the reasoning_content. Otherwise the model
                # reasons server-side and the latency is spent invisibly.
                eff = _cloud_reasoning_effort(LOCAL_API_BASE, think, model)
                if eff is not None:
                    kwargs["reasoning_effort"] = eff
            try:
                resp = await client.chat.completions.create(**kwargs)
            except Exception as _re:
                # Model doesn't expose reasoning_effort (plain grok-4, grok-3):
                # strip it, remember, retry once. Other errors fall through to
                # the outer handler's classify/backoff machinery.
                if "reasoning_effort" in kwargs and _is_reasoning_param_error(_re):
                    _NO_REASONING_EFFORT.add((model or "").lower())
                    kwargs.pop("reasoning_effort", None)
                    resp = await client.chat.completions.create(**kwargs)
                else:
                    raise
        except Exception as e:
            info = classify_api_error(e, _is_local_endpoint(LOCAL_API_BASE))
            # Auto-retry retryable failures (rate_limit / timeout / server_error /
            # transient unreachable). Exponential backoff capped at 3 attempts.
            if info.retryable and depth < 25:  # don't infinitely retry
                _retry_n = 0
                _max_retries = int(os.getenv("HEARTH_API_RETRIES", "3"))
                while info.retryable and _retry_n < _max_retries:
                    _retry_n += 1
                    # A context_overflow never recovers by re-sending the same
                    # oversized prompt — a big tool result (file read / browse)
                    # can blow past the limit in one step, and the proactive
                    # top-of-loop compaction already ran. So HARD-shrink before
                    # retrying: trim to a fraction of budget (truncates the fat
                    # tool payloads), re-pair, and rebuild the request. Without
                    # this the user just watches 2s+4s+8s of pointless retries
                    # and then a dead turn.
                    if info.category == "context_overflow":
                        # estimate_tokens undercounts dense code/JSON, so a single
                        # big file-read can overflow while the estimate still looks
                        # "under budget" — trimming by estimate alone won't cut
                        # enough. So: hard-cap every fat tool result, then trim to
                        # an ESCALATING fraction (½ → ¼ → ⅛) so it provably
                        # converges within the retry budget no matter how far off
                        # the estimate was.
                        _cap = max(400, 1200 // _retry_n)
                        messages[:] = _truncate_kept_tool_results(messages, max_chars=_cap)
                        _shrink = max(2048, int(_budget * (0.5 ** _retry_n)))
                        messages[:] = trim_to_budget(messages, _shrink, RESERVED_OUTPUT)
                        messages[:] = sanitize_tool_pairing(messages)
                        kwargs["messages"] = (
                            _to_manual_messages(messages, _tools_block_text)
                            if _use_manual_tools else messages)
                        _wait = 0  # already fixed the cause; don't stall
                    else:
                        _wait = min(2 ** _retry_n, 8)  # 2s, 4s, 8s
                    emit("retry", attempt=_retry_n, max_attempts=_max_retries,
                         wait_s=_wait, category=info.category, message=info.hint)
                    if _wait:
                        await asyncio.sleep(_wait)
                    try:
                        resp = await client.chat.completions.create(**kwargs)
                        info = None
                        break
                    except Exception as e2:
                        info = classify_api_error(e2, _is_local_endpoint(LOCAL_API_BASE))
                        e = e2
            if info is not None:
                emit("error", message=info.hint, category=info.category,
                     retryable=info.retryable, detail=f"{type(e).__name__}: {e}")
                return 4

        # Streaming accumulator. OpenAI streams tool_calls as partial deltas
        # keyed by `index` — we merge them as they arrive. Content streams as
        # plain text chunks via delta.content.
        content_buf: List[str] = []
        reasoning_buf: List[str] = []
        tool_calls_by_idx: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None

        try:
            async for chunk in resp:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # Reasoning chunks (some LM Studio backends stream this). Emit
                # whenever they arrive — with think off we asked the model to skip
                # reasoning, so anything that streams here is a FORCED model, and
                # showing it beats a blind wait. The GUI collapses it by default.
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_buf.append(rc)
                    emit("thinking_chunk", content=rc)

                # Plain content chunks — emit as they arrive for live UI
                if getattr(delta, "content", None):
                    content_buf.append(delta.content)
                    emit("assistant_chunk", content=delta.content)

                # Tool call deltas — merge by index
                tcs = getattr(delta, "tool_calls", None)
                if tcs:
                    for tc in tcs:
                        idx = tc.index if hasattr(tc, "index") else 0
                        slot = tool_calls_by_idx.setdefault(idx, {
                            "id": None, "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        if getattr(tc, "type", None):
                            slot["type"] = tc.type
                        fn = getattr(tc, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                slot["function"]["name"] = (slot["function"]["name"] or "") + fn.name
                            if getattr(fn, "arguments", None):
                                slot["function"]["arguments"] = (slot["function"]["arguments"] or "") + fn.arguments
        except Exception as e:
            emit("error", message=f"Streaming failed at depth {depth}: {type(e).__name__}: {e}")
            return 4

        # Empty-stream rescue. llama.cpp's chatml-function-calling STREAMING
        # handler crashes mid-generation on some models ("ASGI callable
        # returned without completing response") — the 200 header is sent but
        # the generator yields nothing, so content + tool_calls arrive empty
        # with finish_reason None. The NON-streaming path for the same request
        # is reliable. A local model always produces something, so an empty
        # local stream means the bug, not a real empty answer — retry once,
        # non-streamed. Cloud is left alone (empty there is a real throttle).
        if (not content_buf and not tool_calls_by_idx
                and "tools" in kwargs and _is_local_endpoint(LOCAL_API_BASE)):
            try:
                ns_kwargs = dict(kwargs)
                ns_kwargs["stream"] = False
                r2 = await client.chat.completions.create(**ns_kwargs)
                m2 = r2.choices[0].message
                finish_reason = r2.choices[0].finish_reason or finish_reason
                if getattr(m2, "content", None):
                    content_buf.append(m2.content)
                    emit("assistant_chunk", content=m2.content)
                for tc in (getattr(m2, "tool_calls", None) or []):
                    tool_calls_by_idx[len(tool_calls_by_idx)] = {
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.function.name,
                                     "arguments": tc.function.arguments or ""},
                    }
            except Exception:
                pass  # fall through to the normal empty-response handling below

        # Build the synthetic message in the same shape the rest of the loop
        # expects. The legacy strip_tool_markup() + nudge here was made
        # redundant by hearth.tool_call_parser below, which detects the same
        # patterns AND extracts them into proper tool_calls instead of just
        # deleting them. Keeping only the deletion left a visible "(stripped
        # malformed tool-call markup)" line in the chat surface — confusing
        # without adding value.
        msg_content = "".join(content_buf)
        msg_tool_calls = [tool_calls_by_idx[i] for i in sorted(tool_calls_by_idx)]
        msg_reasoning = "".join(reasoning_buf) if reasoning_buf else None

        # Build a tiny stand-in object so we don't have to refactor the rest
        # of the loop. Match attribute names used below.
        class _Msg:
            pass
        msg = _Msg()
        msg.content = msg_content
        # Coerce to namespace objects with .id, .function.name, .function.arguments
        class _TC:
            pass
        class _Fn:
            pass
        msg.tool_calls = []
        for tc_dict in msg_tool_calls:
            tc = _TC()
            tc.id = tc_dict["id"] or f"call_{int(time.time()*1000)}"
            tc.function = _Fn()
            tc.function.name = tc_dict["function"]["name"]
            tc.function.arguments = tc_dict["function"]["arguments"]
            msg.tool_calls.append(tc)
        reasoning = msg_reasoning

        # Multi-family tool-call fallback. Many open-weights families (Gemma,
        # Llama 3, Phi, Command-R, Granite, some Hermes builds, and any model
        # whose --chat_format wasn't set) emit tool calls as RAW TEXT in the
        # content stream because llama_cpp.server didn't parse them. Detect
        # those patterns here and inject them as if the server had parsed
        # them natively. Without this, the model's tool intent is lost and
        # the user just sees gibberish like `<|toolcall>call:viewimage{...}`
        # in the chat.
        if not msg.tool_calls and msg.content:
            try:
                from . import tool_call_parser as _tcp
                if _tcp.has_tool_call(msg.content):
                    tool_names = [t["name"] for t in TOOL_DEFINITIONS]
                    cleaned, fallback_calls = _tcp.parse(msg.content, tool_names)
                    if fallback_calls:
                        msg.content = cleaned  # strip raw syntax from display
                        for fc in fallback_calls:
                            tc = _TC()
                            tc.id = fc["id"]
                            tc.function = _Fn()
                            tc.function.name = fc["function"]["name"]
                            tc.function.arguments = fc["function"]["arguments"]
                            msg.tool_calls.append(tc)
            except Exception as e:
                # Parser is a best-effort fallback — if it throws, fall back
                # to the original behavior of just showing the raw content.
                emit("nudge", reason=f"tool-call parser error: {type(e).__name__}: {e}")

        if reasoning:
            # Emit aggregate for clients that don't process chunks. Un-gated from
            # `think` so a forced model's reasoning still reaches the UI.
            emit("thinking", content=reasoning)

        if msg.tool_calls:
            # Reset the yield-nudge guard once we actually see tool calls
            # in a turn — they're moving forward, not stuck.
            _yielded_already = False
            tc_dicts = []
            for tc in msg.tool_calls:
                tc_dicts.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })
            assistant_entry: Dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": tc_dicts,
            }
            messages.append(assistant_entry)

            for tc in msg.tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
                emit("tool_call", name=name, args=args)

                # Permission policy.
                # 1. Caller can supply a `permission_check(name, args) -> str`
                #    that returns "allow" / "deny" / "always" / "never" or a
                #    coroutine resolving to same. The GUI uses this to pop a
                #    modal and wait for the user's click. The CLI has its own
                #    parallel implementation in hearth_cli.py.
                # 2. Otherwise the legacy JARVIS_AUTO_APPROVE env var applies:
                #    when set to "0", risky tools are auto-denied (no UI).
                RISKY = {
                    "write_file", "edit_file", "create_directory",
                    "delete_path", "move_path", "run_command",
                    "open_app", "open_url", "open_in_browser",
                    "memory_forget", "extract_archive_file",
                    "create_plugin", "delete_plugin",
                    "read_inbox", "send_email",
                    # Computer-use drives the REAL mouse/keyboard over the whole
                    # desktop, so the state-changing actions are gated here too
                    # (the GUI persists an "always allow" so it prompts once,
                    # not on every click). computer_screen/move and
                    # desktop_snapshot are read-only / harmless -> left ungated.
                    "computer_click", "computer_type", "computer_key",
                    "computer_scroll", "computer_drag",
                    "desktop_click", "desktop_type",
                    "launch_team",  # spawns a team of autonomous agents + panes
                    # browse_click / browse_type / browse_scroll are NOT
                    # listed — once the user has already approved the
                    # initial `browse` call, every subsequent click/type/
                    # scroll inside that same session shouldn't re-prompt.
                    # Otherwise the agent stalls on page 1 waiting for
                    # approval on every interaction.
                }
                if name in RISKY and permission_check is not None:
                    _deny_reason = ""
                    try:
                        decision = permission_check(name, args)
                        if asyncio.iscoroutine(decision):
                            decision = await decision
                    except Exception:
                        decision = "deny"
                    # A decline can arrive as {"decision","reason"} — the user's own
                    # words for WHY they said no. Surface that to the model.
                    if isinstance(decision, dict):
                        _deny_reason = (decision.get("reason") or "").strip()
                        decision = decision.get("decision") or "deny"
                    if decision in ("deny", "never", "timeout"):
                        if decision == "timeout":
                            tool_result = (
                                f"This tool call was NOT executed - the user did "
                                f"not respond to the approval prompt in time. "
                                f"NOTHING ran. No file was created, written, moved, "
                                f"or changed. Do NOT say it succeeded or that any "
                                f"file/output was produced. Tell the user it is "
                                f"still waiting on their approval, then stop."
                            )
                        else:
                            tool_result = (
                                f"The user DECLINED this action - it did NOT run, "
                                f"nothing was created, written, moved, or changed. A "
                                f"decline means the user is steering and likely wants "
                                f"something different. STOP this line of action now: "
                                f"do NOT retry it and do NOT try a workaround. Give a "
                                f"brief reply that you've stopped, and ask them what "
                                f"they'd like instead."
                            )
                            if _deny_reason:
                                tool_result += (
                                    f"\n\nWhat they said when they declined: "
                                    f"\"{_deny_reason}\". Follow that as a direct "
                                    f"instruction."
                                )
                            # A decline is the user taking the wheel — end the tool
                            # loop now so the model asks, instead of grinding through
                            # retries or workarounds they never asked for.
                            _force_answer = True
                        # Run the decline through the loop guard so a
                        # second identical retry trips FAILURE_WARN and
                        # a fourth trips FAILURE_STOP. Without this the
                        # model can repeat the same denied call forever.
                        gd = guard.after(name, args, tool_result)
                        if gd.action in ("warn", "stop"):
                            tool_result = f"{tool_result}\n\n{gd.note}"
                        if gd.action == "stop":
                            _force_answer = True
                        emit("tool_result", name=name, content=tool_result, ms=0)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": tool_result,
                        })
                        continue
                elif name in RISKY:
                    if os.environ.get("JARVIS_AUTO_APPROVE", "1") == "0":
                        tool_result = (
                            f"The user declined this tool call. Strict permission "
                            f"mode is on (JARVIS_AUTO_APPROVE=0) and '{name}' "
                            f"is risky."
                        )
                        gd = guard.after(name, args, tool_result)
                        if gd.action in ("warn", "stop"):
                            tool_result = f"{tool_result}\n\n{gd.note}"
                        if gd.action == "stop":
                            _force_answer = True
                        emit("tool_result", name=name, content=tool_result, ms=0)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": tool_result,
                        })
                        continue

                # Loop guard: skip identical MUTATING dup (no dup side effect).
                skip = guard.before(name, args)
                if skip is not None:
                    emit("tool_result", name=name, content=skip.note, ms=0)
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "name": name, "content": skip.note,
                    })
                    continue

                t0 = time.time()
                # Pass _approved=True since this branch only runs AFTER the
                # permission check said allow — without it run_command's
                # destructive guard would refuse again even though it's approved.
                # EXCEPTION: unsupervised callers (phone bridges, where the user
                # can't see a prompt) do NOT get _approved, so run_command's
                # destructive-pattern guard still blocks rm/format/taskkill etc.
                # from a remote message while benign commands still run.
                _approved_args = (dict(args, _approved=True)
                                  if (isinstance(args, dict) and supervised) else args)
                # Watchdog: fire slow_tool events at 30s + 90s so the GUI
                # can pop "this is taking a while — send to background?" —
                # tells the user the loop didn't hang, just a slow tool.
                tool_task = asyncio.create_task(
                    asyncio.to_thread(execute_tool, name, _approved_args))
                NUDGE_AT = (30, 90)
                nudge_idx = 0
                tool_result = None
                try:
                    while True:
                        if nudge_idx < len(NUDGE_AT):
                            timeout = NUDGE_AT[nudge_idx] - (time.time() - t0)
                            if timeout <= 0:
                                emit("slow_tool", name=name,
                                     elapsed_s=int(time.time() - t0),
                                     suggestion="background" if nudge_idx == 0 else "stop")
                                nudge_idx += 1
                                continue
                            try:
                                tool_result = await asyncio.wait_for(
                                    asyncio.shield(tool_task), timeout=timeout)
                                break
                            except asyncio.TimeoutError:
                                emit("slow_tool", name=name,
                                     elapsed_s=int(time.time() - t0),
                                     suggestion="background" if nudge_idx == 0 else "stop")
                                nudge_idx += 1
                        else:
                            tool_result = await tool_task
                            break
                except Exception as e:
                    tool_result = f"Error: tool '{name}' raised {type(e).__name__}: {e}"
                ms = int((time.time() - t0) * 1000)

                # Loop guard: outcome check. warn -> append nudge the model sees;
                # stop -> also force a text answer next turn.
                decision = guard.after(name, args, tool_result)
                display_result = tool_result  # what the GUI shows
                model_result = tool_result    # what the model sees next turn
                if decision.action in ("warn", "stop"):
                    model_result = f"{tool_result}\n\n{decision.note}"
                    # NOTE: don't show the loop-guard note in the UI - it's an
                    # internal directive to the model, not user-facing. The
                    # 'nudge' event is suppressed in the GUI for the same reason.
                if decision.action == "stop":
                    _force_answer = True

                emit("tool_result", name=name, content=display_result, ms=ms)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": model_result,
                })
                # Did the tool result hand back a "NEXT STEP:" directive?
                # If yes, arm the auto-nudge so a yielding next turn fires
                # the harder prompt instead of the soft trigger-phrase one.
                if "NEXT STEP:" in (tool_result or ""):
                    snippet = tool_result.split("NEXT STEP:", 1)[1].strip()
                    snippet = snippet.split("\n", 1)[0].strip()
                    _pending_next_step = snippet[:300]
                    _nextstep_nudged = False
                else:
                    _pending_next_step = None
                    _nextstep_nudged = False

                if name == "end_session":
                    emit("done",
                         duration_ms=int((time.time() - t_start) * 1000),
                         iterations=iterations,
                         reason="end_session")
                    return 0
            # Spiral guard tripped this turn — append a hard "answer now"
            # directive AFTER all tool results (valid ordering), so the next
            # model call (which will have tools withheld) produces text.
            if _force_answer:
                emit("nudge", reason="loop guard: no progress / repeated failure — forcing an answer")
                messages.append({
                    "role": "user",
                    "content": (
                        "STOP. You've already called tools enough times this turn "
                        "to have what you need. Do NOT call any more tools. Answer "
                        "the original request NOW in plain text using the results "
                        "you already have. If something genuinely failed, say so "
                        "plainly and stop."
                    ),
                })
            # Loop back for next model turn after tool results
            continue

        # Final assistant text — no more tool calls.
        # Emit the aggregate `assistant` event for clients that don't process
        # `assistant_chunk` events (so existing CLI / bridge consumers still
        # work). New clients render chunks live and use this as the canonical
        # final text.
        if msg.content:
            emit("assistant", content=msg.content)
        elif not msg.tool_calls:
            # Empty completion, no tool call — the model returned NOTHING. Say
            # why instead of leaving a blank turn (which looks like Hearth broke).
            # Emit as an ERROR (system line, red meta) — NOT an assistant message,
            # so it's clearly Hearth talking, not the model. Providers return
            # empty content on safety/recitation blocks, on truncation before any
            # text, and on a free-tier throttle (finish_reason=stop, no content).
            _fr = (finish_reason or "").lower()
            if _fr in ("content_filter", "safety"):
                _why = "the provider blocked this response with its safety filter — try rephrasing."
            elif _fr == "recitation":
                _why = ("the provider blocked it as too close to copyrighted/training text "
                        "(recitation) — common on piracy/DLC topics. Rephrase or ask more generally.")
            elif _fr in ("length", "max_tokens"):
                _why = "it hit the output-length cap before producing any text — ask for something shorter."
            elif _is_local_endpoint(LOCAL_API_BASE):
                _why = (f"the local model returned no text (finish_reason: {finish_reason or 'unknown'}). "
                        "The server's streaming tool-call handler likely dropped the response — "
                        "retry, or switch to a different local model.")
            else:
                _why = (f"the model returned no text (finish_reason: {finish_reason or 'unknown'}). "
                        "On a cloud free tier this is usually a rate/quota throttle — wait a "
                        "moment, check your provider quota, or switch brains.")
            emit("error", message=f"No output — {_why}", category="empty_response")

        # NEXT-STEP nudge: previous tool emitted a "NEXT STEP:" directive,
        # and the model just narrated instead of executing it.
        if (
            _pending_next_step
            and not _nextstep_nudged
            and msg.content
            and iterations < max_depth - 1
        ):
            _nextstep_nudged = True
            emit("nudge", reason="ignored NEXT STEP hint from tool result")
            messages.append({"role": "assistant", "content": msg.content})
            messages.append({
                "role": "user",
                "content": (
                    f"The previous tool result told you exactly what to do next: "
                    f"'{_pending_next_step}'. Execute that NEXT STEP NOW — call "
                    f"the tool. Don't narrate, don't describe, don't ask. Act."
                ),
            })
            continue

        # NOTE: removed the old trigger-phrase anti-yield wrapper. It was
        # producing false positives (model saying "let me check" as a polite
        # opener while ACTUALLY calling the tool), adding latency, and rarely
        # helping. The NEXT-STEP wrapper above covers the real failure mode.

        # AGENT-LOOP SELF-CHECK: did the model stop too early?
        # The user's #1 complaint: "feels like a chatbot, not an agent —
        # does X and stops when I asked for the whole alphabet". The persona
        # rule above tells the model to keep going; this is the safety net.
        #
        # Cheap, pattern-based — no extra LLM call. Triggers ONE nudge if
        # the final response shape SCREAMS "I'm bailing early":
        #   - Trailing chatbot-pitch question ("want me to also...?")
        #   - Tiny answer to a clearly-large ask (long prompt, short reply)
        #   - "Let me know" / "anything else" sign-off after a small action
        #
        # Fires at most ONCE per user turn (tracked via _early_stop_nudged).
        try:
            _final_msg = next(
                (m for m in reversed(messages)
                 if m.get("role") == "assistant" and isinstance(m.get("content"), str)),
                None,
            )
            _final = (_final_msg or {}).get("content", "") or ""
            _user_msg = next(
                (m for m in messages
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                None,
            )
            _user_text = (_user_msg or {}).get("content", "") or ""
            if (
                _final and _user_text
                and not getattr(run_once, "_early_stop_nudged", False)
                and iterations < max_depth - 1
            ):
                early_stop_patterns = [
                    r"want me to (also|then|next)",
                    r"shall i (also|continue|proceed|run)",
                    r"should i (also|continue|proceed)",
                    r"let me know (if|when)",
                    r"anything else\?",
                    r"would you like me to",
                ]
                import re as _re
                tail = _final[-300:].lower()
                hit = next((p for p in early_stop_patterns if _re.search(p, tail)), None)
                # Sample-and-bail detector for long asks: user wrote >=300
                # chars, but reply is <200 chars AND contains no real numbers
                # (suggests "gave 3 things and stopped" on a "read the whole
                # X" prompt). Heuristic, not exact.
                long_ask_short_reply = (
                    len(_user_text) >= 300
                    and len(_final) < 200
                    and not _re.search(r"\b\d{2,}\b", _final)
                )
                if hit or long_ask_short_reply:
                    run_once._early_stop_nudged = True  # type: ignore[attr-defined]
                    reason = ("trailing chatbot-pitch in reply"
                              if hit else "short reply to a long ask")
                    emit("nudge",
                         reason=f"agent-loop self-check: {reason} — keep going")
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM] You stopped early. Re-read the ORIGINAL "
                            "user request and check: did you actually cover "
                            "the whole ask, or sample and bail? If the ask "
                            "was 'read it thoroughly' you should still be "
                            "running the chunk loop. If it was 'find + launch X' "
                            "you should be calling open_app, not asking 'want "
                            "me to launch?'. Do the next concrete step. NOW. "
                            "No 'want me to' questions. Just act."
                        ),
                    })
                    continue  # loop back, run another turn
        except Exception:
            pass  # never block the chat reply on a self-check exception

        # PASSIVE FACT EXTRACTION on clean finish too — same pattern as the
        # compaction-boundary hook above. Runs once per turn that actually
        # completes (no max_depth bail) so durable facts accumulate naturally
        # without the user ever saying "remember that".
        try:
            from . import memory_extract as _mx
            import openai as _oai_module
            _sync = _oai_module.OpenAI(api_key=LOCAL_API_KEY, base_url=LOCAL_API_BASE)
            _llm = _mx.make_openai_llm_call(_sync, model, max_tokens=600)
            _saved, _warns = _mx.extract_and_save(messages, _llm, recent_turns=4)
            if _saved:
                emit("facts_saved", count=len(_saved),
                     titles=[f["title"] for f in _saved])
        except Exception:
            pass  # fact extraction failure NEVER blocks the chat reply

        # Reset the per-turn nudge flag so a fresh user message can trigger
        # the self-check again (we only block re-triggering within one user
        # turn — across user turns it must be re-enabled).
        try:
            del run_once._early_stop_nudged  # type: ignore[attr-defined]
        except AttributeError:
            pass

        emit("done",
             duration_ms=int((time.time() - t_start) * 1000),
             iterations=iterations,
             reason="finished")
        return 0

    # We hit the cap. The wrap-up nudge at 75% should have prompted the
    # model to write a STATE_SNAPSHOT block in one of the last few responses.
    # Extract it (if present) and persist + emit so:
    #   - The CLI/GUI can show "stopped at turn N — say 'continue' to resume"
    #   - The user's next message starts with the snapshot context visible
    #     in chat history (it's already in `messages`)
    #   - We optionally save to ~/Jarvis/cache/task_state.json so a fresh
    #     session can find the last in-flight task.
    snapshot_text = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            content = m["content"] or ""
            if "STATE_SNAPSHOT" in content:
                # Extract the fenced block (```STATE_SNAPSHOT ... ```)
                import re as _re
                match = _re.search(r"```STATE_SNAPSHOT\s*\n(.*?)\n```", content, _re.DOTALL)
                if match:
                    snapshot_text = match.group(1).strip()
                else:
                    # Loose fallback: keep everything after the keyword
                    snapshot_text = content.split("STATE_SNAPSHOT", 1)[1][:1200].strip()
                break
    # Persist for cross-session recovery — cheap, gitignored workspace path.
    try:
        from .tools import WORKSPACE as _WS
        state_dir = os.path.join(_WS, "cache", "task_state")
        os.makedirs(state_dir, exist_ok=True)
        state_path = os.path.join(state_dir, "last_stopped.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "prompt": prompt,
                "iterations": iterations,
                "max_depth": max_depth,
                "snapshot": snapshot_text or "(model did not write STATE_SNAPSHOT)",
                "stopped_at": time.time(),
                "model": model,
            }, f, indent=2)
    except Exception:
        state_path = None
    emit("done",
         duration_ms=int((time.time() - t_start) * 1000),
         iterations=iterations,
         reason="max_depth_reached",
         snapshot=snapshot_text or None,
         resume_hint=(
             f"Stopped at turn {iterations}/{max_depth}. "
             f"Type 'continue' to resume — the snapshot above tells the model "
             f"where to pick up." if snapshot_text else
             f"Stopped at turn {iterations}/{max_depth}. The model didn't "
             f"write a STATE_SNAPSHOT before the cap. Type 'continue' and it "
             f"will see its own last tool calls in history."
         ),
         state_file=state_path if state_path else None)
    return 0


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.headless",
        description="Run a single prompt against Hearth, emit JSONL events to stdout.",
    )
    parser.add_argument("--prompt", "-p", required=True,
                        help="The user message to send.")
    parser.add_argument("--model", "-m", default=None,
                        help="Model id (default: first model returned by the server).")
    parser.add_argument("--think", action="store_true",
                        help="Enable reasoning mode (show model thinking).")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
                        help=f"Max tool-call iterations (default {DEFAULT_MAX_DEPTH}).")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="Sampling temperature (default 0.7).")
    parser.add_argument("--format", choices=("json", "text"), default="json",
                        help="Output format: jsonl (default) or pretty text.")
    args = parser.parse_args(argv)

    emit = emit_json if args.format == "json" else emit_text
    try:
        return asyncio.run(run_once(
            args.prompt,
            emit=emit,
            model=args.model,
            think=args.think,
            max_depth=args.max_depth,
            temperature=args.temperature,
        ))
    except KeyboardInterrupt:
        emit("error", message="interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
