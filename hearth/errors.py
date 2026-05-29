"""API-error classification — turn cryptic LLM-endpoint failures into a clear
category + a human next step + whether a retry could help.

Why this exists: in a real session (run.txt) LM Studio returned a raw HTML 500
mid-chat and EVERY following turn died with the same wall of HTML — the worst
kind of "looks broken" moment. Generic `except Exception: print(e)` can't tell
"server is down, go start it" from "you're rate-limited, wait 2s" from "bad API
key". This maps the common failure shapes (local LM Studio + cloud Gemini/Grok/
OpenAI) to a taxonomy, mirroring how Claude Code / Hermes / OpenClaw route
errors to recovery. Both the CLI and the headless bridge use it so the message
is consistent everywhere.
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

    is_local: True for LM Studio / Ollama / LAN endpoints, False for cloud.
    The same symptom gets a different hint depending on where it came from
    (a connection error locally means "start LM Studio"; on cloud it means
    "check your network / the endpoint URL")."""
    text = str(exc).lower()
    code = _status_code(exc, text)
    here = "LM Studio" if is_local else "the cloud endpoint"

    # --- connectivity: nothing answered at all ---
    if any(s in text for s in ("connection", "refused", "10061", "connecterror",
                               "failed to establish", "name or service not known",
                               "getaddrinfo", "ssl", "certificate")):
        if is_local:
            return ApiErrorInfo("unreachable",
                "Can't reach LM Studio — start it and load a model (or run a cloud model).",
                retryable=True)
        return ApiErrorInfo("unreachable",
            "Can't reach the cloud endpoint — check your internet and the LOCAL_API_BASE URL.",
            retryable=True)

    # --- auth ---
    if code in (401, 403) or "api key" in text or "unauthorized" in text or "invalid_api_key" in text:
        return ApiErrorInfo("auth",
            "Authentication failed — check LOCAL_API_KEY for this endpoint." if not is_local
            else "Auth rejected by the local server (unusual for LM Studio — check its config).",
            retryable=False)

    # --- rate limit / quota ---
    if code == 429 or "rate limit" in text or "quota" in text or "resource_exhausted" in text or "too many requests" in text:
        return ApiErrorInfo("rate_limit",
            "Rate-limited / quota hit — wait a moment and retry, or switch model/provider.",
            retryable=True)

    # --- context overflow ---
    if ("context" in text and ("exceed" in text or "too long" in text or "maximum" in text)) \
            or "n_keep" in text or "prompt is too long" in text or "context_length_exceeded" in text:
        return ApiErrorInfo("context_overflow",
            "Prompt overflowed the model's context — /compact or load a larger context, then retry.",
            retryable=True)

    # --- model not loaded / not found ---
    if code == 404 or "model not found" in text or "no model" in text or "not loaded" in text \
            or "does not exist" in text or "model_not_found" in text:
        return ApiErrorInfo("no_model",
            "Load a model in LM Studio, then resend." if is_local
            else "Model id not found for this provider — check LOCAL_MODEL / use /model.",
            retryable=False)

    # --- malformed request (a param the endpoint rejects) ---
    if code in (400, 422) and any(s in text for s in ("unknown name", "invalid argument",
                                                       "unsupported", "does not support",
                                                       "unexpected", "invalid_request")):
        return ApiErrorInfo("bad_request",
            "The endpoint rejected a request parameter — likely a model/endpoint quirk.",
            retryable=False)

    # --- server-side blowups (the run.txt 500 cascade) ---
    if code in (500, 502, 503, 504) or "internal server error" in text or "<!doctype html" in text:
        return ApiErrorInfo("server_error",
            "The model server errored (often a too-long prompt or a wedged model). "
            "Try /compact or reload the model in LM Studio, then resend." if is_local
            else "The provider had a server error — retry shortly; if it persists, switch model.",
            retryable=True)

    # --- timeout ---
    if code == 408 or "timeout" in text or "timed out" in text:
        return ApiErrorInfo("timeout",
            "The request timed out — the model may be slow or stuck; retry, or pick a faster model.",
            retryable=True)

    return ApiErrorInfo("unknown",
        f"Unexpected error talking to {here}. Your message is preserved — fix it and resend.",
        retryable=True)
