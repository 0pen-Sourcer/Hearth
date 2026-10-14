"""Anthropic's own Messages API behind the chat-completions shape Hearth uses.

Anthropic's OpenAI-compatible endpoint takes `reasoning_effort` and ignores it,
and refuses thinking outright, so the think button did nothing there. This
speaks the Messages API through the official SDK and hands the turn loop the
same streamed chunks every other provider gives it: text, thinking, tool calls,
usage.

Thinking blocks of a tool round are kept and sent back unchanged with that
round's tool calls. A block the API can no longer match to its conversation
(history trimmed, tools added part-way) is dropped by the API rather than
failing the request.
"""
from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from types import SimpleNamespace as NS
from typing import Any, Dict, List, Optional

try:
    import anthropic
except Exception:                       # not bundled in older builds
    anthropic = None

_BINDING_BETA = "thinking-binding-controls-2026-08-01"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_LEVELS = ("low", "medium", "high", "xhigh", "max")

# What a model turned out to refuse, learned from its own 400s: field -> True,
# and the form of "thinking off" it takes ("disabled", "between_tools", or "").
_REFUSED: Dict[str, Dict[str, Any]] = {}
# A tool round's assistant blocks (thinking included), by the ids of its tool calls.
_ROUNDS: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
_ROUNDS_KEPT = 200


def available() -> bool:
    return anthropic is not None


def is_anthropic(base: str) -> bool:
    return "api.anthropic.com" in (base or "").lower()


# ---------------------------------------------------------------- request
def _text_of(content: Any) -> str:
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _user_blocks(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, list):
        t = str(content or "")
        return [{"type": "text", "text": t}] if t else []
    out: List[Dict[str, Any]] = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text" and p.get("text"):
            out.append({"type": "text", "text": p["text"]})
        elif p.get("type") == "image_url":
            url = (p.get("image_url") or {}).get("url") or ""
            m = re.match(r"data:([^;]+);base64,(.*)$", url, re.S)
            if m:
                out.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
            elif url:
                out.append({"type": "image", "source": {"type": "url", "url": url}})
    return out


def _args(text: str) -> Dict[str, Any]:
    try:
        v = json.loads(text or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except ValueError:
        return {}


def convert(messages: List[Dict[str, Any]]):
    """OpenAI-shaped history -> (system text, Messages API messages)."""
    system: List[str] = []
    out: List[Dict[str, Any]] = []

    def add(role: str, blocks: List[Dict[str, Any]]):
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        if role == "system":
            if not out:
                system.append(_text_of(m.get("content")))
            else:                              # a note part-way through goes in as the user's side
                add("user", [{"type": "text", "text": _text_of(m.get("content"))}])
        elif role == "user":
            add("user", _user_blocks(m.get("content")))
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            kept = _ROUNDS.get(calls[0].get("id") or "") if calls else None
            if kept and {b.get("id") for b in kept if b.get("type") == "tool_use"} == {c.get("id") for c in calls}:
                add("assistant", [dict(b) for b in kept])
                continue
            blocks: List[Dict[str, Any]] = []
            t = _text_of(m.get("content")).strip()
            if t:
                blocks.append({"type": "text", "text": t})
            for c in calls:
                fn = c.get("function") or {}
                blocks.append({"type": "tool_use", "id": c.get("id") or "", "name": fn.get("name") or "tool",
                               "input": _args(fn.get("arguments"))})
            add("assistant", blocks)
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "",
                          "content": _text_of(m.get("content")) or "(no output)"}])
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]})
    return "\n\n".join(s for s in system if s), out


def _tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out = []
    for t in tools or []:
        fn = t.get("function") if t.get("type") == "function" else t
        if not fn or not fn.get("name"):
            continue
        out.append({"name": fn["name"], "description": fn.get("description") or "",
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _params(kw: Dict[str, Any], think: bool, level: str) -> Dict[str, Any]:
    model = kw.get("model") or ""
    seen = _REFUSED.setdefault(model, {})
    system, msgs = convert(kw.get("messages") or [])
    p: Dict[str, Any] = {"model": model, "messages": msgs,
                         "max_tokens": max(int(kw.get("max_tokens") or 0), 32000),
                         "cache_control": {"type": "ephemeral"}}
    if system:
        p["system"] = system
    tools = _tools(kw.get("tools"))
    if tools:
        p["tools"] = tools
        if kw.get("tool_choice") == "none":
            p["tool_choice"] = {"type": "none"}
    betas = []
    if think:
        th: Dict[str, Any] = {"type": "adaptive", "display": "summarized"}
        if not seen.get("block_binding"):
            th["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
            betas.append(_BINDING_BETA)
        if not seen.get("adaptive"):
            p["thinking"] = th
        if level in _LEVELS and not seen.get("effort"):
            p["output_config"] = {"effort": level}
    else:
        off = seen.get("off", "disabled")
        if off:
            p["thinking"] = {"type": off}
        elif not seen.get("effort"):
            p["output_config"] = {"effort": "low"}      # can't switch off: think as little as it can
    if not seen.get("fallbacks"):
        p["fallbacks"] = "default"
        betas.append(_FALLBACK_BETA)
    if betas:
        p["betas"] = betas
    return p


def _learn(err: Exception, p: Dict[str, Any], think: bool) -> bool:
    """Drop what the model's 400 names and say whether to try again."""
    msg = str(getattr(err, "message", "") or err).lower()
    seen = _REFUSED.setdefault(p.get("model") or "", {})
    th = p.get("thinking") or {}
    if "fallback" in msg and "fallbacks" in p:
        seen["fallbacks"] = True
    elif ("block_binding" in msg or "thinking-binding" in msg or "prefix_mismatch" in msg) and th.get("block_binding"):
        seen["block_binding"] = True
    elif "thinking" in msg and not think and th.get("type") == "disabled":
        seen["off"] = "between_tools"
    elif "thinking" in msg and not think and th.get("type") == "between_tools":
        seen["off"] = ""
    elif ("adaptive" in msg or "thinking" in msg) and think and th:
        seen["adaptive"] = True
    elif "effort" in msg and "output_config" in p:
        seen["effort"] = True
    else:
        return False
    return True


# ---------------------------------------------------------------- response
def _chunk(content=None, reasoning=None, tool=None, finish=None, usage=None):
    delta = NS(content=content, reasoning_content=reasoning, tool_calls=[tool] if tool else None,
               role="assistant", model_extra={})
    choices = [] if usage is not None else [NS(index=0, delta=delta, finish_reason=finish)]
    return NS(choices=choices, usage=usage, model_extra={})


_FINISH = {"tool_use": "tool_calls", "end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop",
           "refusal": "stop", "pause_turn": "stop"}


def _keep_round(final) -> None:
    blocks = []
    for b in getattr(final, "content", None) or []:
        d = b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b)
        if d.get("type") in ("thinking", "redacted_thinking", "text", "tool_use"):
            blocks.append(d)
    ids = [b["id"] for b in blocks if b.get("type") == "tool_use"]
    if ids:
        _ROUNDS[ids[0]] = blocks
        while len(_ROUNDS) > _ROUNDS_KEPT:
            _ROUNDS.popitem(last=False)


class _Stream:
    def __init__(self, client, kw, think, level):
        self._client, self._kw, self._think, self._level = client, kw, think, level

    async def open(self):
        """Send the request. A refused field fails it before anything streams, so
        it is learned and sent again; any other error is raised here, at the call."""
        p = _params(self._kw, self._think, self._level)
        for _ in range(5):
            self._mgr = self._client.beta.messages.stream(**p)
            try:
                self._s = await self._mgr.__aenter__()
                return self
            except anthropic.BadRequestError as e:
                if not _learn(e, p, self._think):
                    raise
                p = _params(self._kw, self._think, self._level)
        self._s = await self._mgr.__aenter__()
        return self

    def __aiter__(self):
        return self._run()

    async def close(self):
        """Stop now: the request ends instead of reading on to the end."""
        await self._mgr.__aexit__(None, None, None)

    async def _run(self):
        try:
            async for ch in self._read(self._s):
                yield ch
        finally:
            await self._mgr.__aexit__(None, None, None)

    async def _read(self, s):
        tool_index: Dict[int, int] = {}
        async for ev in s:
            t = getattr(ev, "type", "")
            if t == "content_block_start":
                cb = ev.content_block
                if getattr(cb, "type", "") == "tool_use":
                    n = tool_index[ev.index] = len(tool_index)
                    yield _chunk(tool=NS(index=n, id=cb.id, type="function",
                                         function=NS(name=cb.name, arguments="")))
            elif t == "content_block_delta":
                d = ev.delta
                dt = getattr(d, "type", "")
                if dt == "text_delta" and d.text:
                    yield _chunk(content=d.text)
                elif dt == "thinking_delta" and d.thinking:
                    yield _chunk(reasoning=d.thinking)
                elif dt == "input_json_delta" and ev.index in tool_index:
                    yield _chunk(tool=NS(index=tool_index[ev.index], id=None, type="function",
                                         function=NS(name=None, arguments=d.partial_json or "")))
        final = await s.get_final_message()
        _keep_round(final)
        stop = getattr(final, "stop_reason", "") or "end_turn"
        if stop == "refusal":
            why = getattr(getattr(final, "stop_details", None), "explanation", "") or ""
            yield _chunk(content="\n\nThe model declined this one." + (f" {why}" if why else ""))
        yield _chunk(finish=_FINISH.get(stop, "stop"))
        u = final.usage
        prompt = ((u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                  + (getattr(u, "cache_creation_input_tokens", 0) or 0))
        yield _chunk(usage=NS(prompt_tokens=prompt, completion_tokens=u.output_tokens or 0,
                              total_tokens=prompt + (u.output_tokens or 0)))


class _Completions:
    def __init__(self, owner):
        self._o = owner

    async def create(self, **kw):
        think = kw.pop("hearth_think", None)
        level = (kw.get("reasoning_effort") or "").lower()
        if think is None:
            think = level not in ("", "none", "minimal")
        p = _params(kw, think, level)
        if not kw.get("stream"):
            for _ in range(5):
                try:
                    final = await self._o.sdk.beta.messages.create(**p)
                    break
                except anthropic.BadRequestError as e:
                    if not _learn(e, p, think):
                        raise
                    p = _params(kw, think, level)
            _keep_round(final)
            text = "".join(getattr(b, "text", "") for b in final.content if getattr(b, "type", "") == "text")
            calls = [NS(id=b.id, type="function", function=NS(name=b.name, arguments=json.dumps(b.input)))
                     for b in final.content if getattr(b, "type", "") == "tool_use"]
            msg = NS(role="assistant", content=text, tool_calls=calls or None, reasoning_content=None, model_extra={})
            return NS(choices=[NS(index=0, message=msg, finish_reason=_FINISH.get(final.stop_reason or "", "stop"))],
                      usage=NS(prompt_tokens=final.usage.input_tokens, completion_tokens=final.usage.output_tokens))
        return await _Stream(self._o.sdk, kw, think, level).open()


def async_client(base: str, key: str, timeout=None):
    """The chat client for an endpoint: Anthropic's own API for Anthropic, else OpenAI's."""
    from openai import AsyncOpenAI
    oc = AsyncOpenAI(base_url=base, api_key=key, timeout=timeout)
    if is_anthropic(base) and available():
        return AsyncAnthropicChat(api_key=key, models=oc.models)
    return oc


class AsyncAnthropicChat:
    """Looks like AsyncOpenAI to the turn loop: `.chat.completions.create`."""

    def __init__(self, api_key: str, timeout=None, models=None):
        # No overall deadline: a long answer streams for minutes; a stalled read still trips.
        self.sdk = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout or anthropic.Timeout(None, connect=15.0, read=300.0, write=60.0, pool=15.0))
        self.chat = NS(completions=_Completions(self))
        self.models = models
