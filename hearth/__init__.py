"""Hearth — local-only tool layer for a personal AI assistant.

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
# "localhost" first resolves to ::1 on Windows, and LM Studio, llama-server and
# most local model servers listen on 127.0.0.1 only, so every request to them
# waited about two seconds for the IPv6 attempt to be refused. Answer the
# IPv4 address first for localhost; IPv6 is still tried after it.
# ---------------------------------------------------------------------------
import socket as _socket

_real_getaddrinfo = _socket.getaddrinfo


def _getaddrinfo_v4_first(host, *args, **kwargs):
    res = _real_getaddrinfo(host, *args, **kwargs)
    if isinstance(host, str) and host.lower() == "localhost":
        res = sorted(res, key=lambda r: r[0] != _socket.AF_INET)
    return res


if getattr(_socket.getaddrinfo, "__name__", "") != "_getaddrinfo_v4_first":
    _socket.getaddrinfo = _getaddrinfo_v4_first

# ---------------------------------------------------------------------------
# Undo an update that was cut off partway through, before anything else loads.
# A patch replaces these files one at a time; losing the process in the middle
# of that leaves two versions mixed together and the app does not come back up.
# The rollback inside apply_patch only covers a failure it lives to see, so the
# marker it leaves behind is checked here instead. Ordinary starts find nothing
# and pay one missing-file check for it.
# ---------------------------------------------------------------------------
try:
    from . import updater as _updater
    _updater.recover_interrupted_patch()
except Exception:
    pass

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

# ---------------------------------------------------------------------------
# Make CUDA runtime DLLs (cudart, cublas, nvrtc) shipped via pip wheels
# discoverable to llama-cpp-python's loader BEFORE anything imports llama_cpp.
# Without this, llama.dll fails with "could not find module" on Windows
# machines that don't have a system-wide CUDA toolkit. We ship the runtime
# via the nvidia-cuda-runtime-cu12 / nvidia-cublas-cu12 / nvidia-cuda-nvrtc-cu12
# wheels so users don't need to install CUDA themselves.
# ---------------------------------------------------------------------------
if _os.name == "nt":
    try:
        import sysconfig as _sysconfig
        _spkg = _sysconfig.get_paths().get("purelib") or _sysconfig.get_paths().get("platlib")
        if _spkg:
            for _sub in ("cuda_runtime", "cublas", "cuda_nvrtc", "cudnn"):
                _bin = _os.path.join(_spkg, "nvidia", _sub, "bin")
                if _os.path.isdir(_bin):
                    try:
                        _os.add_dll_directory(_bin)
                    except Exception:
                        pass
                    # Also prepend to PATH for child processes (llama_cpp.server subprocess)
                    if _bin not in _os.environ.get("PATH", ""):
                        _os.environ["PATH"] = _bin + _os.pathsep + _os.environ.get("PATH", "")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Where a new install keeps its workspace, and what the agent is called before
# the person names it. Inglen is the app's name and the default for both; an
# install from before (with ~/Jarvis) keeps its folder and its name.
# ---------------------------------------------------------------------------
def default_workspace() -> str:
    """~/Jarvis when an install already uses it, else ~/Inglen."""
    home = _os.path.expanduser("~")
    legacy = _os.path.join(home, "Jarvis")
    return legacy if _os.path.isdir(legacy) else _os.path.join(home, "Inglen")


def default_agent_name() -> str:
    """The agent's name until the person picks one: the app's name, "Inglen"; an
    install from before that never saved a name keeps the one it had."""
    from . import env as _envmod
    ws = (_envmod.get("WORKSPACE") or "").strip()
    folder = _os.path.basename(_os.path.normpath(ws or default_workspace()))
    return "JARVIS" if folder.lower() == "jarvis" else "Inglen"

from .tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    to_openai_tools,
    to_gemini_tools,
    to_claude_tools,
    tools_by_category,
    WORKSPACE,
    read_locked,
    LOGS_DIR,
    SHOTS_DIR,
    MEMORY_DIR,
    ACTIVITY_LOG,
    _log_activity,
    trim_to_budget,
    sanitize_tool_pairing,
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
    "read_locked",
    "LOGS_DIR",
    "SHOTS_DIR",
    "MEMORY_DIR",
    "ACTIVITY_LOG",
    "_log_activity",
    "trim_to_budget",
    "sanitize_tool_pairing",
    "compact_history",
    "estimate_tokens",
    "CHARS_PER_TOKEN",
    "set_runtime_info",
    "system_prompt",
    "NAME",
    "memory",
    "voice",
]

