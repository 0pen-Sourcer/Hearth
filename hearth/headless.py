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

from . import system_prompt, execute_tool, to_openai_tools
from .loop_guard import ToolLoopGuard, MAX_TURNS
from .errors import classify_api_error

LOCAL_API_BASE = os.getenv("LOCAL_API_BASE", "http://localhost:1234/v1")
# Real API key for cloud endpoints (Gemini, OpenRouter, OpenAI, etc.).
# Local LM Studio ignores it, so the harmless default is fine. To use a
# cloud model: set LOCAL_API_BASE + LOCAL_API_KEY + LOCAL_MODEL.
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY") or os.getenv("OPENAI_API_KEY") or "not-needed"


def _is_local_endpoint(base: str) -> bool:
    """True for localhost / LAN endpoints (LM Studio, Ollama, vLLM, llama.cpp).
    Used to gate LM-Studio-specific request params that cloud APIs reject."""
    b = (base or "").lower()
    return any(h in b for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1",
                                "192.168.", "10.", "host.docker.internal"))
DEFAULT_MAX_DEPTH = 20  # generous — let the model decide when it has enough,
                        # not an artificial chain. The model almost always
                        # stops on its own well before this.
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
    fields = ("loaded_context_length", "max_context_length",
              "context_length", "n_ctx", "max_position_embeddings")
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
        print(f"\n[tool] {fields.get('name')} {args_str}", flush=True)
    elif event_type == "tool_result":
        body = fields.get('content', '')
        head = body.split('\n', 1)[0][:200]
        more = "" if '\n' not in body else f"  (+{body.count(chr(10))} more lines)"
        print(f"     -> {head}{more}", flush=True)
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
    # "not-needed") gets a 401 on the auto-detect probe + every chat request.
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
        # Last resort — first model in the v1 list. May fail if it's not loaded.
        try:
            models_resp = await client.models.list()
            model = models_resp.data[0].id
        except Exception as e:
            emit("error", message=f"Could not auto-detect a model on {LOCAL_API_BASE}: {e}")
            return 3

    # Proactive memory: fold the saved facts most relevant to this prompt into
    # the system message, fenced + authoritative, so the model uses what it
    # knows instead of ignoring the passive index. Bounded; adds nothing when
    # nothing matches. Same behavior as the CLI's _prepare_context.
    from . import memory as _mem
    _sys = system_prompt()
    _block = _mem.recall_for_prompt(prompt)
    if _block:
        _sys += "\n\n" + _block
    messages: List[Dict[str, Any]] = [{"role": "system", "content": _sys}]
    if history:
        # Drop any system entries the caller smuggled in — only ours is canonical.
        for h in history:
            if h.get("role") and h.get("role") != "system":
                messages.append({"role": h["role"], "content": h.get("content", "")})
    messages.append({"role": "user", "content": prompt})
    emit("user", content=prompt)

    tools = to_openai_tools()
    # Context budget so a long history (the GUI re-sends a growing one every
    # turn) gets trimmed to fit instead of overflowing — the CLI does this in
    # _prepare_context; without it run_once eventually overflows and LM Studio
    # drops the user turn ("No user query found in messages"). Tool schemas ride
    # every prompt, so reserve them + output. trim_to_budget structurally keeps a
    # surviving user turn, so no extra invariant is needed.
    from .tools import trim_to_budget, estimate_tokens, CHARS_PER_TOKEN
    context_tokens = CONTEXT_TOKENS  # default 32K — see CONTEXT_TOKENS comment
    if not os.getenv("JARVIS_CONTEXT"):  # autodetect unless the user pinned it
        _d = autodetect_context(model)
        if _d:
            context_tokens = _d
    try:
        _tool_tokens = len(json.dumps(tools)) // CHARS_PER_TOKEN
    except Exception:
        _tool_tokens = 0
    _budget = max(2048, context_tokens - _tool_tokens)
    # Emit so the GUI / CLI can show what budget actually got picked.
    try:
        emit("context_budget",
             context_tokens=context_tokens,
             tool_tokens=_tool_tokens,
             effective_budget=_budget,
             source=("env JARVIS_CONTEXT" if os.getenv("JARVIS_CONTEXT")
                     else "endpoint probe" if context_tokens != CONTEXT_TOKENS
                     else f"default {CONTEXT_TOKENS//1024}K"))
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

    for depth in range(max_depth):
        iterations = depth + 1
        # User hit Stop in the GUI — bail out cleanly between turns.
        if should_cancel is not None and should_cancel():
            emit("cancelled", message="stopped by user")
            break
        # Auto-compact: trim before each call so a long history OR a long tool
        # chain within one turn can't overflow the context window.
        if estimate_tokens(messages) > _budget:
            messages[:] = trim_to_budget(messages, _budget, RESERVED_OUTPUT)
        # Belt-and-suspenders: trim_to_budget now guarantees a user role, but
        # ANY upstream caller that hands us a history without one will crash
        # LM Studio's Jinja with "No user query found in messages". Cheap to
        # check and idempotent — synthesize a continue-marker if needed.
        if not any(m.get("role") == "user" for m in messages):
            messages.append({"role": "user",
                             "content": "Continue using the results above."})
        try:
            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
            }
            if _force_answer:
                # Spiral guard tripped last turn: withhold tools so the model
                # is FORCED to produce a text answer instead of calling again.
                # One-shot — clear it so the next turn can use tools normally.
                _force_answer = False
            else:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            # `chat_template_kwargs` is an LM-Studio / llama.cpp-specific extra.
            # Cloud OpenAI-compatible endpoints (Gemini, OpenAI, OpenRouter)
            # reject unknown fields with a 400. Only send it to local servers.
            # Both `chat_template_kwargs` and the `stop=["<think>"]` hack are
            # local-only. Cloud models (Grok rejects `stop` outright; Gemini
            # rejects `chat_template_kwargs`) stream reasoning via a dedicated
            # `reasoning_content` channel, so neither workaround is needed.
            if _is_local_endpoint(LOCAL_API_BASE):
                kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": think}}
                if not think:
                    kwargs["stop"] = ["<think>", "<thinking>"]
            resp = await client.chat.completions.create(**kwargs)
        except Exception as e:
            info = classify_api_error(e, _is_local_endpoint(LOCAL_API_BASE))
            # Auto-retry retryable failures (rate_limit / timeout / server_error /
            # transient unreachable). Exponential backoff capped at 3 attempts.
            if info.retryable and depth < 25:  # don't infinitely retry
                _retry_n = 0
                _max_retries = int(os.getenv("HEARTH_API_RETRIES", "3"))
                while info.retryable and _retry_n < _max_retries:
                    _retry_n += 1
                    _wait = min(2 ** _retry_n, 8)  # 2s, 4s, 8s
                    emit("retry", attempt=_retry_n, max_attempts=_max_retries,
                         wait_s=_wait, category=info.category, message=info.hint)
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

                # Reasoning chunks (some LM Studio backends stream this)
                rc = getattr(delta, "reasoning_content", None)
                if rc and think:
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

        if reasoning and think:
            # Emit aggregate for clients that don't process chunks
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
                    "browse_click", "browse_type",
                }
                if name in RISKY and permission_check is not None:
                    try:
                        decision = permission_check(name, args)
                        if asyncio.iscoroutine(decision):
                            decision = await decision
                    except Exception:
                        decision = "deny"
                    if decision in ("deny", "never"):
                        tool_result = (
                            f"USER DECLINED this tool call ('{decision}'). "
                            f"Move on or pick a non-risky alternative. Don't "
                            f"retry the same call."
                        )
                        emit("tool_result", name=name, content=tool_result, ms=0)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": tool_result,
                        })
                        continue
                elif name in RISKY:
                    # No callback — fall back to legacy env var
                    if os.environ.get("JARVIS_AUTO_APPROVE", "1") == "0":
                        tool_result = (
                            f"USER DECLINED this tool call. Strict permission "
                            f"mode is on (JARVIS_AUTO_APPROVE=0) and '{name}' "
                            f"is risky."
                        )
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
                try:
                    tool_result = await asyncio.to_thread(execute_tool, name, args)
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

        emit("done",
             duration_ms=int((time.time() - t_start) * 1000),
             iterations=iterations,
             reason="finished")
        return 0

    emit("done",
         duration_ms=int((time.time() - t_start) * 1000),
         iterations=iterations,
         reason="max_depth_reached")
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
