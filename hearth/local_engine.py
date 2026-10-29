"""Where Inglen's own engine listens.

The built-in engine has its own port, so it never meets LM Studio (1234), Ollama
(11434) or a llama.cpp server (8080) on theirs; those are still found and used
when they are running (llmserver.KNOWN_LOCAL_SERVERS). Settings, Built-in server,
Port changes it (server_port); BUILTIN_PORT in the environment wins over both.
"""
from __future__ import annotations

DEFAULT_PORT = 8790
LEGACY_PORT = 1234          # the engine's port before it had its own (LM Studio's); a saved 1234 means "the default"


def builtin_port() -> int:
    from . import env as envmod
    try:
        port = int(envmod.get("BUILTIN_PORT", "") or 0)
    except ValueError:
        port = 0
    return port if 1024 <= port <= 65535 and port != LEGACY_PORT else DEFAULT_PORT


def helper_port() -> int:
    """The second model (for helpers) runs beside it, on the next port."""
    from . import env as envmod
    try:
        port = int(envmod.get("HELPER_PORT", "") or 0)
    except ValueError:
        port = 0
    return port if 1024 <= port <= 65535 and port != LEGACY_PORT + 1 else builtin_port() + 1


def local_base() -> str:
    """The local address a brain talks to unless told otherwise: Inglen's own engine."""
    return f"http://localhost:{builtin_port()}/v1"
