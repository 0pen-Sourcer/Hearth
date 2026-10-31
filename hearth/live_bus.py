"""What changed, said the moment it changes, to every surface that is open.

The window used to look for a phone's messages every 5 s and for a changed
brain every 12 s, and the phone was never told about anything changed on the
PC. Now the window's server keeps one stream of events (GET /api/events, server-
sent events), and everything that changes something says so here:

  {"kind": "chat", "chat": id}                    a chat's messages or title changed
  {"kind": "settings", "keys": [...]}             settings changed (the brain, approval, thinking...)
  {"kind": "turn", "chat": id, "from": surface, "turn": id, "type": ..., ...}
                                                  a reply on its way: steps, the text so far, the end

The window listens and redraws at once; the phone bridge listens and passes each
on to the phones. A process other than the window's (a bridge, the terminal)
hands its events to the window over HTTP (POST /api/events), so one stream
carries them all.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from typing import Any, Dict, List, Optional

_SUBS: List["queue.Queue[Dict[str, Any]]"] = []
_LOCK = threading.Lock()
IN_WINDOW = False                      # set by web.py: this process serves /api/events


def subscribe() -> "queue.Queue[Dict[str, Any]]":
    q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=500)
    with _LOCK:
        _SUBS.append(q)
    return q


def unsubscribe(q) -> None:
    with _LOCK:
        if q in _SUBS:
            _SUBS.remove(q)


def _local(ev: Dict[str, Any]) -> None:
    with _LOCK:
        subs = list(_SUBS)
    for q in subs:
        try:
            q.put_nowait(ev)
        except queue.Full:                 # a listener that stopped reading loses old events, not the stream
            try:
                q.get_nowait()
                q.put_nowait(ev)
            except Exception:
                pass


_OUT: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1000)
_SENDER: Optional[threading.Thread] = None


def _window_file() -> str:
    from .tools import WORKSPACE
    return os.path.join(WORKSPACE, "window.json")


def announce_window(url: str) -> None:
    """The window says where it answers, for a terminal started on its own (which has
    no HEARTH_WINDOW_URL) to send its turns to and follow the others'."""
    try:
        path = _window_file()
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"url": url.rstrip("/"), "pid": os.getpid()}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def window_url() -> str:
    """Where the window answers: given by the window to the processes it starts, or
    what it announced in the workspace. '' when there is no window."""
    url = (os.environ.get("HEARTH_WINDOW_URL") or "").rstrip("/")
    if url:
        return url
    try:
        with open(_window_file(), encoding="utf-8") as f:
            return str((json.load(f) or {}).get("url") or "").rstrip("/")
    except (OSError, ValueError):
        return ""


def _send_loop() -> None:
    import urllib.request
    while True:
        ev = _OUT.get()
        url = window_url()
        if not url:
            continue
        try:
            req = urllib.request.Request(url + "/api/events", method="POST", data=json.dumps(ev).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass


def publish(ev: Dict[str, Any]) -> None:
    """Say that something changed. Never blocks and never fails the caller."""
    if not ev:
        return
    try:
        if IN_WINDOW:
            _local(ev)
            return
        if not window_url():
            return
        global _SENDER
        if _SENDER is None or not _SENDER.is_alive():
            _SENDER = threading.Thread(target=_send_loop, daemon=True, name="live-bus-send")
            _SENDER.start()
        _OUT.put_nowait(ev)
    except Exception:
        pass


def turn_event(chat: Optional[str], frm: str, turn: str, kind: str, f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One event of a reply in progress, said the same way whichever surface runs it.
    None for the engine's own bookkeeping, which no other surface needs."""
    if not chat:
        return None
    base = {"kind": "turn", "chat": chat, "from": frm, "turn": turn}
    if kind == "start":
        return dict(base, type="start", text=str(f.get("text") or "")[:20000])
    if kind == "assistant_chunk":
        return dict(base, type="chunk", text=str(f.get("content") or ""))
    if kind in ("thinking_chunk", "think"):
        return dict(base, type="think", text=str(f.get("content") or f.get("text") or ""))
    if kind == "tool_call" and f.get("name"):
        from .step_words import words
        from .channel_core import detail_of
        w = f.get("words") if isinstance(f.get("words"), dict) else words(f["name"], f.get("args"), str(f.get("says") or ""))
        return dict(base, type="step", words=w.get("done") or "", doing=w.get("doing") or "", tool=f["name"],
                    cmd=detail_of(f.get("args")))
    if kind == "tool_result":
        from .channel_core import out_of
        return dict(base, type="step_out", out=out_of(f.get("content")))
    if kind == "assistant":
        return dict(base, type="said", text=str(f.get("content") or ""))
    if kind == "error":
        return dict(base, type="error", text=str(f.get("message") or ""))
    if kind in ("done", "cancelled"):
        return dict(base, type="end")
    return None


def listen(url: str, on_event, stop=lambda: False) -> None:
    """Follow the window's stream (for a process that isn't the window), calling
    on_event for each event, reconnecting until stop() says so."""
    import time
    import urllib.request
    while not stop():
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/events", timeout=40) as r:
                for raw in r:
                    if stop():
                        return
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data:"):
                        try:
                            on_event(json.loads(line[5:].strip()))
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(2)
