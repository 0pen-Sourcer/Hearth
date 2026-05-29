"""J.A.R.V.I.S. Brain — local-only tool layer for a personal AI assistant.

Designed for use with LM Studio / Ollama / any OpenAI-compatible local API.
No paid APIs, no cloud calls (except DuckDuckGo HTML for web search).
"""

# ---------------------------------------------------------------------------
# Quiet down huggingface_hub & related libs BEFORE anything imports them.
# faster-whisper pulls in hf_hub on first use, and hf_hub reads these env
# vars at import-time — so they must be set here, at the very top of the
# Hearth package, not inside listen.py (which is imported lazily later).
# ---------------------------------------------------------------------------
import os as _os
import warnings as _warnings
_os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
_os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
_os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
_os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
_os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
_warnings.filterwarnings("ignore", category=UserWarning, module="huggingface_hub.*")
_warnings.filterwarnings("ignore", category=FutureWarning, module="huggingface_hub.*")

# ---------------------------------------------------------------------------
# Use the OS certificate store for TLS (Windows/macOS) so cloud endpoints
# (Gemini, xAI/Grok, OpenAI, OpenRouter) work even behind a corporate or
# antivirus TLS-inspecting proxy whose root CA lives in the OS store but NOT
# in certifi's bundle. Without this, httpx (used by the openai SDK) can throw
# CERTIFICATE_VERIFY_FAILED on machines with such a proxy. Local http:// LM
# Studio is unaffected. Optional dependency — degrade gracefully if missing.
# ---------------------------------------------------------------------------
try:
    import truststore as _truststore
    _truststore.inject_into_ssl()
except Exception:
    pass

from .tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    to_openai_tools,
    to_gemini_tools,
    to_claude_tools,
    tools_by_category,
    WORKSPACE,
    SAFE_READ_ONLY,
    LOGS_DIR,
    SHOTS_DIR,
    MEMORY_DIR,
    ACTIVITY_LOG,
    _log_activity,
    trim_to_budget,
    compact_history,
    estimate_tokens,
    CHARS_PER_TOKEN,
    set_runtime_info,
)
from .persona import system_prompt, NAME
from . import memory, voice

__all__ = [
    "TOOL_DEFINITIONS",
    "execute_tool",
    "to_openai_tools",
    "to_gemini_tools",
    "to_claude_tools",
    "tools_by_category",
    "WORKSPACE",
    "SAFE_READ_ONLY",
    "LOGS_DIR",
    "SHOTS_DIR",
    "MEMORY_DIR",
    "ACTIVITY_LOG",
    "_log_activity",
    "trim_to_budget",
    "compact_history",
    "estimate_tokens",
    "CHARS_PER_TOKEN",
    "set_runtime_info",
    "system_prompt",
    "NAME",
    "memory",
    "voice",
]
