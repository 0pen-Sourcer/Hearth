"""One way to call a chat model, for every place Hearth does.

Providers retire request fields model by model: Anthropic's newest models
refuse `temperature`, some OpenAI models refuse `max_tokens`, some servers
refuse `stream_options`. When the provider's error names a field Hearth sent,
the field is dropped, remembered for that model, and the call made again, so
one retired field doesn't stop every message. Nothing here knows any model by
name; the provider's own answer decides.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set

# Fields a provider refused, by model, for as long as Hearth runs.
_REFUSED: Dict[str, Set[str]] = {}
# The request itself; without these there is nothing to send.
_KEEP = {"model", "messages", "stream", "tools", "tool_choice"}
_REFUSAL = ("deprecated", "not supported", "unsupported", "unknown", "unrecognized", "not allowed",
            "not permitted", "invalid", "extra inputs", "does not support", "no longer supported")


def refused_field(err: Exception, kwargs: Dict[str, Any]) -> Optional[str]:
    """The field of this request the provider's error says it won't take."""
    msg = str(getattr(err, "message", "") or err)
    low = msg.lower()
    if not any(w in low for w in _REFUSAL):
        return None
    padded = " " + msg + " "
    for k in [*kwargs, *(kwargs.get("extra_body") or {})]:
        if k in _KEEP:
            continue
        if re.search(r"[`'\"\s(\[]" + re.escape(k) + r"[`'\"\s.,:;)\]]", padded):
            return k
    return None


def _drop(kwargs: Dict[str, Any], field: str) -> None:
    kwargs.pop(field, None)
    extra = kwargs.get("extra_body")
    if isinstance(extra, dict):
        extra.pop(field, None)


def _prepare(kwargs: Dict[str, Any]) -> str:
    model = str(kwargs.get("model") or "").lower()
    for f in _REFUSED.get(model, ()):
        _drop(kwargs, f)
    return model


def _learn(err: Exception, kwargs: Dict[str, Any], model: str) -> bool:
    field = refused_field(err, kwargs)
    if not field:
        return False
    _REFUSED.setdefault(model, set()).add(field)
    _drop(kwargs, field)
    return True


async def _create_async(client, kwargs: Dict[str, Any]):
    model = _prepare(kwargs)
    for _ in range(4):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as e:
            if not _learn(e, kwargs, model):
                raise
    return await client.chat.completions.create(**kwargs)


def chat_create(client, **kwargs):
    """client.chat.completions.create(**kwargs), learning which fields the
    provider refuses. Returns an awaitable for an async client."""
    if type(client).__name__.startswith("Async"):
        return _create_async(client, kwargs)
    model = _prepare(kwargs)
    for _ in range(4):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            if not _learn(e, kwargs, model):
                raise
    return client.chat.completions.create(**kwargs)
