"""How long a turn waited on the person: an OK for a step, an answer to its question.

A turn's duration is the agent's working time. Every brain times its turn from start
to end and says so on "done"; the time the turn sat waiting on the person is taken off
there, once, where the turn's events are written (the window, the terminal, a chat app),
so the window, the terminal and the phone all show the same number.
"""
from __future__ import annotations

import inspect
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict


class Waited:
    def __init__(self) -> None:
        self.seconds = 0.0
        self._lock = threading.Lock()

    @contextmanager
    def on_person(self):
        start = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self.seconds += time.monotonic() - start

    def fix(self, kind: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """A "done" event's duration without the time spent waiting on the person."""
        ms = fields.get("duration_ms")
        if kind != "done" or not isinstance(ms, (int, float)) or not self.seconds:
            return fields
        return dict(fields, duration_ms=max(0, int(ms - self.seconds * 1000)))

    def timing(self, check: Callable) -> Callable:
        """The same permission check, its wait counted. Works for plain and async checks."""
        if inspect.iscoroutinefunction(check):
            async def timed_async(*a, **k):
                with self.on_person():
                    return await check(*a, **k)
            return timed_async

        def timed(*a, **k):
            with self.on_person():
                return check(*a, **k)
        return timed
