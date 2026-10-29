"""When the agent may call the person on the phone app by itself (Inglen Plus).

Never by default: each call runs the brain. With "Let <name> call me" on, it
calls only for the reasons switched on (a reminder coming due, background work
finishing or needing them, a question that has waited), never in quiet hours,
one ring at a time, and not again soon after a call they declined or missed.

A ring is wanted wherever the reason happens (reminders and jobs in the
window's process, questions in the phone bridge); `request` checks the rules
and hands the ring to the phone bridge through the live bus, which rings the
paired phones (hearth/relay_bridge.py).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

DEFAULTS: Dict[str, Any] = {
    "call_me": False,              # the master switch: off until they turn it on
    "call_me_reminders": True,
    "call_me_jobs": True,
    "call_me_questions": True,
    "call_me_quiet": True,         # no calls between these times
    "call_me_quiet_from": "22:00",
    "call_me_quiet_to": "08:00",
    "call_me_wait_min": 3,         # how long a question waits for an answer before it calls
}
REASONS = {"reminder": "call_me_reminders", "job": "call_me_jobs", "question": "call_me_questions"}
WAITS = (1, 2, 3, 5, 10, 15)
RING_S = 45                        # unanswered this long, a ring becomes a missed-call notification
GAP_S = 300                        # after a declined or missed call, this long before another
AT_PC_S = 120                      # the PC used this recently: they are at it, so no call


def _settings() -> Dict[str, Any]:
    try:
        from .tools import WORKSPACE
        with open(os.path.join(WORKSPACE, "settings.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def rules(s: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The call-me settings, with the defaults for any not saved."""
    s = _settings() if s is None else s
    r = {k: s.get(k, v) for k, v in DEFAULTS.items()}
    for k in ("call_me", "call_me_reminders", "call_me_jobs", "call_me_questions", "call_me_quiet"):
        r[k] = bool(r[k])
    try:
        r["call_me_wait_min"] = max(1, min(60, int(r["call_me_wait_min"])))
    except (TypeError, ValueError):
        r["call_me_wait_min"] = DEFAULTS["call_me_wait_min"]
    r["agent_name"] = str(s.get("agent_name") or "").strip()
    return r


def agent_name(r: Optional[Dict[str, Any]] = None) -> str:
    name = (r or rules()).get("agent_name") or ""
    if not name:
        try:
            from . import persona
            name = persona.NAME
        except Exception:
            name = ""
    return name or "JARVIS"


def _minute(hhmm: Any) -> Optional[int]:
    try:
        h, m = str(hhmm).strip().split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def quiet_now(r: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Inside quiet hours. A span that passes midnight (22:00 to 08:00) counts on both sides of it."""
    if not r.get("call_me_quiet"):
        return False
    a, b = _minute(r.get("call_me_quiet_from")), _minute(r.get("call_me_quiet_to"))
    if a is None or b is None or a == b:
        return False
    now = now or datetime.now()
    t = now.hour * 60 + now.minute
    return a <= t < b if a < b else (t >= a or t < b)


def may_call(reason: str, r: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Whether a call for this reason may ring now, and if not, why not:
    'off' (calls aren't allowed at all), 'reason' (not for this), 'quiet', 'unknown'."""
    r = rules() if r is None else r
    if not r.get("call_me"):
        return False, "off"
    key = REASONS.get(reason)
    if key is None:
        return False, "unknown"
    if not r.get(key):
        return False, "reason"
    if quiet_now(r, now):
        return False, "quiet"
    return True, ""


def question_due(asked_at: float, r: Optional[Dict[str, Any]] = None, now_ts: Optional[float] = None,
                 now: Optional[datetime] = None) -> bool:
    """A question still unanswered after the set wait, with calls allowed for questions now."""
    r = rules() if r is None else r
    ok, _ = may_call("question", r, now)
    return ok and ((now_ts if now_ts is not None else time.time()) - asked_at) >= r["call_me_wait_min"] * 60


def ask_wait(base: float, r: Optional[Dict[str, Any]] = None) -> float:
    """How long a question waits for its answer: long enough to be called about it and answer in the call."""
    r = rules() if r is None else r
    if r.get("call_me") and r.get("call_me_questions"):
        return max(base, r["call_me_wait_min"] * 60 + RING_S + 120)
    return base


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


def opener(reason: str, why: str) -> str:
    """The first thing said when they answer: why it called."""
    why = _clip(why, 240).rstrip(".")
    if reason == "reminder":
        return f"Hi, it's about your reminder: {why}."
    if reason == "question":
        return "Hi, I need your answer on something."
    return f"Hi, I'm calling because {why[:1].lower() + why[1:]}." if why else "Hi, something on your PC needs you."


def at_pc(idle: Optional[float] = None) -> bool:
    """Someone used this PC's keyboard or mouse in the last two minutes."""
    if idle is None:
        try:
            from .reminders import idle_seconds
            idle = idle_seconds()
        except Exception:
            idle = None
    return idle is not None and idle < AT_PC_S


def want(reason: str, why: str, chat: str = "", ask: str = "", tap: bool = False, key: str = "",
         r: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
         idle: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The ring this would make, or None when the rules say no call (or they are at the PC)."""
    r = rules() if r is None else r
    ok, _ = may_call(reason, r, now)
    if not ok or at_pc(idle):
        return None
    return {"kind": "call_me", "reason": reason, "why": _clip(why, 160), "say": opener(reason, why),
            "c": str(chat or ""), "ask": str(ask or ""), "tap": bool(tap), "question": _clip(why, 300) if ask else "",
            "key": key or f"{reason}:{chat}:{ask}:{_clip(why, 80)}"}


def request(reason: str, why: str, chat: str = "", ask: str = "", tap: bool = False, key: str = "") -> bool:
    """Ring the paired phones for this, when the rules allow it. Never raises."""
    try:
        ev = want(reason, why, chat=chat, ask=ask, tap=tap, key=key)
        if ev is None:
            return False
        from . import live_bus
        live_bus.publish(ev)
        return True
    except Exception:
        return False


class Rings:
    """One ring at a time; the same thing rings once; a gap after a declined or missed one."""

    def __init__(self):
        self.lock = threading.Lock()
        self.now: Optional[Dict[str, Any]] = None     # the ring going on
        self.seen: Dict[str, float] = {}               # key -> when it rang
        self.quiet_until = 0.0

    def start(self, ev: Dict[str, Any], rid: str, at: Optional[float] = None) -> Optional[Dict[str, Any]]:
        at = time.time() if at is None else at
        with self.lock:
            if self.now is not None or at < self.quiet_until:
                return None
            key = str(ev.get("key") or "")
            if key and key in self.seen:
                return None
            if key:
                self.seen[key] = at
                if len(self.seen) > 300:
                    for k in sorted(self.seen, key=self.seen.get)[:100]:
                        self.seen.pop(k, None)
            self.now = dict(ev, id=rid, at=at)
            return self.now

    def take(self, rid: str) -> Optional[Dict[str, Any]]:
        """Answered: the ring it was, and it stops."""
        with self.lock:
            ring = self.now if self.now and self.now.get("id") == rid else None
            if ring is not None:
                self.now = None
            return ring

    def drop(self, rid: str, at: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Declined or missed: it stops, and nothing rings for a while."""
        with self.lock:
            ring = self.now if self.now and self.now.get("id") == rid else None
            if ring is not None:
                self.now = None
                self.quiet_until = (time.time() if at is None else at) + GAP_S
            return ring


def view(r: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The settings as the phone shows them."""
    r = rules() if r is None else r
    return {"on": r["call_me"], "name": agent_name(r),
            "reasons": [k for k, v in REASONS.items() if r.get(v)],
            "quiet": f"{r['call_me_quiet_from']}-{r['call_me_quiet_to']}" if r["call_me_quiet"] else "",
            "wait_min": r["call_me_wait_min"]}
