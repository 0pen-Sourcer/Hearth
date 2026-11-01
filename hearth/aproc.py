"""Ending an agent's process inside the event loop that started it."""
from __future__ import annotations

import asyncio
from typing import Any


async def reap(proc: Any, timeout: float = 10) -> None:
    """Stop `proc` if it still runs, wait for it and close its pipes now. A turn stopped
    partway (Stop, Ctrl+C, a timeout) otherwise leaves the agent running, and its pipes to
    the garbage collector after the loop has closed ("unclosed transport" at exit)."""
    if proc is None:
        return
    try:
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
    except Exception:
        pass
    if proc.returncode is None:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except (Exception, asyncio.CancelledError):
        pass
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        try:
            transport.close()
        except Exception:
            pass
