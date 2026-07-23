"""API-error classification: map a cryptic endpoint failure to a category, one
human next step, and whether retrying could help.

A bare `except Exception: print(e)` can't tell "the server is down" from
"you're rate-limited, wait 2s" from "bad API key", so every failure looks the
same to the user (sometimes as a raw wall of HTML). Hints stay endpoint-neutral
on purpose: a local endpoint may be Hearth's own server, LM Studio, Ollama, or
a LAN box, so never name one of them as though it were the only option. The
CLI, GUI and headless bridge all route through here so the wording matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ApiErrorInfo:
    category: str   # short machine label, e.g. "rate_limit"
    hint: str       # one human sentence: what to do about it
    retryable: bool # could the SAME request succeed if retried (maybe after a wait)?


def _status_code(exc: object, text: str) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\b(400|401|403|404|408|409|413|422|429|500|502|503|504)\b", text)
    return int(m.group(1)) if m else None


def classify_api_error(exc: object, is_local: bool) -> ApiErrorInfo:
    """Classify an exception raised by the chat-completions call.

    is_local: True for any localhost/LAN endpoint (built-in server, LM Studio,
    Ollama), False for cloud. The same symptom gets a different hint depending
    on where it came from: a connection error locally means no server is up,
    on cloud it means check the network or the endpoint URL."""
    text = str(exc).lower()
    code = _status_code(exc, text)
    here = "the local model server" if is_local else "the cloud endpoint"

    # --- connectivity: nothing answered at all ---
    if any(s in text for s in ("connection", "refused", "10061", "connecterror",
                               "failed to establish", "name or service not known",
                               "getaddrinfo", "ssl", "certificate")):
        if is_local:
            # Don't name LM Studio here. A local endpoint is just as likely to
            # be Hearth's own built-in server, Ollama, or nothing at all, and
            # telling someone to "start LM Studio" when they don't have it
            # installed sends them looking for a program that isn't the problem.
            return ApiErrorInfo("unreachable",
                "No model server is answering. Pick a model in Models and Hearth "
                "will start its own, or start LM Studio or Ollama if you use one.",
                retryable=True)
        return ApiErrorInfo("unreachable",
            "Can't reach the cloud endpoint. Check your internet and the LOCAL_API_BASE URL.",
            retryable=True)

    # --- auth ---
    if code in (401, 403) or "api key" in text or "unauthorized" in text or "invalid_api_key" in text:
        return ApiErrorInfo("auth",
            "Authentication failed. Check LOCAL_API_KEY for this endpoint." if not is_local
            else "The local server rejected the API key. If Hearth started it, "
                 "restart Hearth to reconnect. If you started it yourself, check "
                 "whether it was launched with an API key set.",
            retryable=False)

    # --- rate limit / quota ---
    if code == 429 or "rate limit" in text or "quota" in text or "resource_exhausted" in text or "too many requests" in text:
        return ApiErrorInfo("rate_limit",
            "Rate-limited or out of quota. Wait a moment and retry, or switch model/provider.",
            retryable=True)

    # --- context overflow ---
    if ("context" in text and ("exceed" in text or "too long" in text or "maximum" in text)) \
            or "n_keep" in text or "prompt is too long" in text or "context_length_exceeded" in text:
        return ApiErrorInfo("context_overflow",
            "Prompt overflowed the model's context. Run /compact, or load a larger context, then retry.",
            retryable=True)

    # --- model not loaded / not found ---
    if code == 404 or "model not found" in text or "no model" in text or "not loaded" in text \
            or "does not exist" in text or "model_not_found" in text:
        # The usual local cause is a stale saved model id: the server is up and
        # serving something, but under a different name than the one Hearth
        # asked for. Say that, because "load a model" reads as "nothing is
        # loaded" and sends people to re-load a model that's already running.
        return ApiErrorInfo("no_model",
            "The server is running but doesn't have that model. It may be a "
            "leftover saved name. Pick the model again in Models, or /models "
            "in the CLI, to point Hearth at what's actually loaded." if is_local
            else "Model id not found for this provider. Check LOCAL_MODEL or use /model.",
            retryable=False)

    # --- malformed request (a param the endpoint rejects) ---
    if code in (400, 422) and any(s in text for s in ("unknown name", "invalid argument",
                                                       "unsupported", "does not support",
                                                       "unexpected", "invalid_request")):
        return ApiErrorInfo("bad_request",
            "The endpoint rejected a request parameter, likely a model or endpoint quirk.",
            retryable=False)

    # --- server-side blowups (the run.txt 500 cascade) ---
    if code in (500, 502, 503, 504) or "internal server error" in text or "<!doctype html" in text:
        return ApiErrorInfo("server_error",
            "The model server errored (often a too-long prompt or a wedged model). "
            "Try /compact, or reload the model, then resend." if is_local
            else "The provider had a server error. Retry shortly, and switch model if it persists.",
            retryable=True)

    # --- timeout ---
    if code == 408 or "timeout" in text or "timed out" in text:
        return ApiErrorInfo("timeout",
            "The request timed out. The model may be slow or stuck. Retry, or pick a faster model.",
            retryable=True)

    return ApiErrorInfo("unknown",
        f"Unexpected error talking to {here}. Your message is preserved, so you can resend it.",
        retryable=True)
