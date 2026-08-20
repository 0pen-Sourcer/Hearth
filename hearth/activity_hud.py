"""Activity HUD — a small always-on-top overlay that shows what JARVIS is doing
on the real desktop during computer-use.

When the agent drives the mouse/keyboard, the app it's controlling is focused,
not Hearth, so the chat window can't tell the user "I'm clicking Login" — they'd
just see the cursor move on its own. This floats a compact pill over everything,
top-center, with a pulsing dot and a one-line status ("Looking at the screen",
"Clicking Sign in", "Typing..."). It never takes focus and is click-through on
Windows, so it can't intercept the very clicks JARVIS is making underneath it.

Runs its own tk thread; show()/hide() are queued so any thread can call them
safely. Any failure disables the HUD permanently and is swallowed — the overlay
is cosmetic and must never break a tool call.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Optional

_thread: Optional[threading.Thread] = None
_q: "queue.Queue[tuple]" = queue.Queue()
_started = False
_disabled = False  # set True on any fatal error; never retried after that
_lock = threading.Lock()

# Auto-hide the pill this many seconds after the last activity() call, so it
# doesn't linger after the agent is done touching the desktop.
_IDLE_HIDE_S = 4.0

# Which tools count as "on the desktop" — used by callers to decide whether to
# surface the HUD at all. Kept here so tools.py and web.py agree on the set.
DESKTOP_TOOLS = {
    "computer_click", "computer_type", "computer_key", "computer_scroll",
    "computer_drag", "computer_move", "computer_screen",
    "desktop_click", "desktop_type", "desktop_snapshot",
    "smart_click", "focus_window", "screenshot", "view_image",
}

# Short, human phrasing per tool. The label the model passes (a button name, a
# window title) is appended when present.
_VERB = {
    "computer_click": "Clicking",
    "desktop_click": "Clicking",
    "smart_click": "Clicking",
    "computer_type": "Typing",
    "desktop_type": "Typing",
    "computer_key": "Pressing",
    "computer_scroll": "Scrolling",
    "computer_drag": "Dragging",
    "computer_move": "Moving to",
    "focus_window": "Switching to",
    "computer_screen": "Looking at the screen",
    "screenshot": "Looking at the screen",
    "desktop_snapshot": "Reading the screen",
    "view_image": "Looking",
}


def _enabled() -> bool:
    """HUD is on unless explicitly disabled, and only where a desktop exists.

    Off automatically for the phone/chat bridges and any headless batch run —
    there's no one at the screen to see it, and tk has no display to draw on.
    """
    v = os.environ.get("JARVIS_ACTIVITY_HUD")
    if v is not None:
        return v.strip().lower() not in ("0", "false", "no", "off")
    # A bridge / headless-batch context sets this; skip the overlay there.
    if os.environ.get("JARVIS_NO_GUI") == "1":
        return False
    try:
        from .web import _load_settings
        s = _load_settings()
        if "activity_hud" in s:
            return bool(s.get("activity_hud"))
    except Exception:
        pass
    return True


def label_for(name: str, args: Optional[dict]) -> str:
    """Build the one-line status for a desktop tool call."""
    verb = _VERB.get(name, "Working")
    hint = ""
    if isinstance(args, dict):
        for k in ("label", "target", "name", "window", "title", "key", "keys"):
            val = args.get(k)
            if isinstance(val, str) and val.strip():
                hint = val.strip()
                break
        if not hint and name in ("computer_type", "desktop_type"):
            txt = args.get("text")
            if isinstance(txt, str) and txt.strip():
                snip = txt.strip().replace("\n", " ")
                hint = (snip[:24] + "…") if len(snip) > 24 else snip
    if hint and verb.endswith(("ing", "to")):
        return f"{verb} {hint}"
    return verb


# ---------------------------------------------------------------------------
# tk side (all of this runs on _thread only)
# ---------------------------------------------------------------------------
def _run_loop():
    global _disabled
    try:
        import tkinter as tk
    except Exception:
        _disabled = True
        return

    try:
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.0)  # start invisible, fade in
        except Exception:
            pass

        BG = "#141118"
        FG = "#f4ecff"
        ACCENT = "#c9a86a"
        frame = tk.Frame(root, bg=BG, highlightthickness=1,
                         highlightbackground="#2c2634")
        frame.pack(fill="both", expand=True)
        dot = tk.Canvas(frame, width=14, height=14, bg=BG, highlightthickness=0)
        dot.pack(side="left", padx=(12, 6), pady=8)
        _dot_id = dot.create_oval(3, 3, 11, 11, fill=ACCENT, outline="")
        lbl = tk.Label(frame, text="", bg=BG, fg=FG,
                       font=("Segoe UI", 10, "normal"), padx=2)
        lbl.pack(side="left", padx=(0, 14), pady=8)

        state = {"visible": False, "last": 0.0, "alpha": 0.0,
                 "target_alpha": 0.0, "pulse": 0.0}

        def _place():
            root.update_idletasks()
            w = frame.winfo_reqwidth()
            h = frame.winfo_reqheight()
            sw = root.winfo_screenwidth()
            x = int((sw - w) / 2)
            y = 24
            root.geometry(f"{w}x{h}+{x}+{y}")

        def _make_clickthrough():
            # Windows: add WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE so
            # the overlay never eats a click and never steals foreground focus
            # from the app JARVIS is driving.
            if os.name != "nt":
                return
            try:
                import ctypes
                from ctypes import wintypes
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                if not hwnd:
                    hwnd = root.winfo_id()
                GWL_EXSTYLE = -20
                WS_EX_LAYERED = 0x00080000
                WS_EX_TRANSPARENT = 0x00000020
                WS_EX_NOACTIVATE = 0x08000000
                WS_EX_TOOLWINDOW = 0x00000080
                gwl = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, GWL_EXSTYLE,
                    gwl | WS_EX_LAYERED | WS_EX_TRANSPARENT
                    | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            except Exception:
                pass

        _clickthrough_done = {"v": False}

        def _pump():
            # Drain queued commands.
            try:
                while True:
                    cmd, payload = _q.get_nowait()
                    if cmd == "show":
                        lbl.config(text=payload or "Working")
                        state["last"] = time.time()
                        state["target_alpha"] = 0.94
                        if not state["visible"]:
                            state["visible"] = True
                            root.deiconify()
                            _place()
                            if not _clickthrough_done["v"]:
                                _make_clickthrough()
                                _clickthrough_done["v"] = True
                        else:
                            _place()
                    elif cmd == "hide":
                        state["target_alpha"] = 0.0
                    elif cmd == "quit":
                        root.destroy()
                        return
            except queue.Empty:
                pass

            # Idle auto-hide.
            if state["visible"] and state["target_alpha"] > 0 \
                    and (time.time() - state["last"]) > _IDLE_HIDE_S:
                state["target_alpha"] = 0.0

            # Fade toward target.
            a = state["alpha"]
            ta = state["target_alpha"]
            if abs(a - ta) > 0.01:
                a += (ta - a) * 0.28
                state["alpha"] = a
                try:
                    root.attributes("-alpha", max(0.0, min(0.94, a)))
                except Exception:
                    pass
                if ta == 0.0 and a < 0.03 and state["visible"]:
                    state["visible"] = False
                    root.withdraw()

            # Pulse the dot while visible.
            if state["visible"] and ta > 0:
                state["pulse"] = (state["pulse"] + 0.14) % (2 * 3.14159)
                import math
                s = 0.5 + 0.5 * math.sin(state["pulse"])
                r = 3 + int(s * 2)
                cx = cy = 7
                dot.coords(_dot_id, cx - r, cy - r, cx + r, cy + r)

            root.after(33, _pump)

        root.after(33, _pump)
        root.mainloop()
    except Exception:
        _disabled = True


def _ensure_started():
    global _thread, _started
    if _disabled or _started or not _enabled():
        return
    with _lock:
        if _started or _disabled:
            return
        try:
            _thread = threading.Thread(target=_run_loop, name="activity-hud",
                                       daemon=True)
            _thread.start()
            _started = True
        except Exception:
            pass


# ---------------------------------------------------------------------------
# public API (call from any thread)
# ---------------------------------------------------------------------------
def activity(text: str) -> None:
    """Show/refresh the overlay with a one-line status. No-op if disabled."""
    if _disabled or not _enabled():
        return
    _ensure_started()
    try:
        _q.put_nowait(("show", str(text)))
    except Exception:
        pass


def for_tool(name: str, args: Optional[dict] = None) -> None:
    """Convenience: surface the HUD for a desktop tool call by name."""
    if name not in DESKTOP_TOOLS:
        return
    activity(label_for(name, args))


def hide() -> None:
    """Begin fading the overlay out."""
    if _disabled or not _started:
        return
    try:
        _q.put_nowait(("hide", None))
    except Exception:
        pass
