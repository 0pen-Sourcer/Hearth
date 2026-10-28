"""Which slot of Hearth's own llama.cpp server each conversation uses.

The server keeps one KV cache per slot. Left to choose (id_slot -1), it gives a
request the idle slot whose cached prompt shares the most with it, else the one
used longest ago, so a helper could land on the main chat's slot and push the
chat's cache out to RAM; the next message then waits while it is read back in.
Here the main chat keeps slot 0 and each helper keeps a slot of its own while
it works (and the same one again when it is free), so every conversation's
cache stays where it was. With more helpers than slots, the extra ones are
left for the server to place.

Only Hearth's own llama-server is pinned: LM Studio, Ollama, the Python wheel
and the cloud get nothing extra in their requests.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

MAIN = "main"
_LOCK = threading.Lock()
# port -> {slot: who holds it now}
_HELD: Dict[int, Dict[int, str]] = {}
# port -> {slot: (who had it last, when it was let go)}
_LAST: Dict[int, Dict[int, tuple]] = {}


def _server(url: str) -> Optional[Dict[str, Any]]:
    """{port, slots, main} for a Hearth llama-server at this address, else None."""
    try:
        from . import llmserver as _ls
    except Exception:
        return None
    port = _ls._port_of(url or "")
    if port is None:
        return None
    info = _ls.ours_at(url)
    if info and info.get("engine") == "native":
        return {"port": port, "slots": int(info.get("slots") or 1), "main": True}
    hs = _ls._helper.get("info") or {}
    p = _ls._helper.get("proc")
    if p is not None and p.poll() is None and port == _ls.HELPER_PORT:
        return {"port": port, "slots": int(hs.get("slots") or 1), "main": False}
    return None


def pin(url: str, who: str) -> Dict[str, Any]:
    """The request fields that keep `who` on its own slot at `url` ({} for none).
    `who` is MAIN for the main chat, else a helper's id."""
    srv = _server(url)
    if not srv or srv["slots"] < 2:
        return {}
    port, n = srv["port"], srv["slots"]
    if who == MAIN:
        return {"id_slot": 0} if srv["main"] else {}
    first = 1 if srv["main"] else 0              # slot 0 of the main server is the chat's
    with _LOCK:
        held = _HELD.setdefault(port, {})
        for s, w in held.items():
            if w == who:
                return {"id_slot": s}
        free = [s for s in range(first, n) if s not in held]
        if not free:
            return {}
        last = _LAST.get(port, {})
        mine = [s for s in free if last.get(s, ("", 0))[0] == who]
        # Its own slot again if it is free, else the one let go longest ago.
        s = mine[0] if mine else min(free, key=lambda k: last.get(k, ("", 0))[1])
        held[s] = who
        return {"id_slot": s}


def release(who: str) -> None:
    """A helper finished: its slot is free, and remembered as its own."""
    with _LOCK:
        for port, held in _HELD.items():
            for s, w in list(held.items()):
                if w == who:
                    del held[s]
                    _LAST.setdefault(port, {})[s] = (who, time.time())


def held() -> Dict[int, Dict[int, str]]:
    with _LOCK:
        return {p: dict(h) for p, h in _HELD.items()}
