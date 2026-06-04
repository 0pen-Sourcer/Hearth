"""Hearth as a native desktop app.

Wraps the same HTTP backend (hearth.web) in a PyWebView window using the
OS's native webview (Edge WebView2 on Windows). Looks and feels like a
real app, not a browser tab — but reuses every line of UI code.

Run:
    python -m hearth.desktop                     # native window
    python -m hearth.desktop --browser           # fallback: open in browser
    python -m hearth.desktop --port 8888 --width 1400 --height 900

If pywebview isn't installed, the script tells you how to fix it AND falls
back to the browser path so you're not stuck.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from typing import Optional

from . import web as web_backend
from . import singleton


def _free_port(start: int = 8765, tries: int = 12) -> int:
    """Walk forward from `start` until we find a free localhost port."""
    for off in range(tries):
        port = start + off
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start  # try anyway; user will see the error


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.desktop",
        description="Hearth desktop app (native window via PyWebView).",
    )
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP backend port. 0 = auto-pick (default).")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=840)
    parser.add_argument("--browser", action="store_true",
                        help="Open in your default browser instead of a native window.")
    parser.add_argument("--no-open", action="store_true",
                        help="Start the server but don't open anything.")
    args = parser.parse_args(argv)

    # Single-instance check — if another Hearth is already running, surface
    # its window and exit. The user clicking the exe 5 times no longer spawns
    # 5 tray icons + 5 backends fighting over ports.
    if not args.port:
        primary, _existing_port = singleton.acquire_or_defer(singleton.DEFAULT_PORT)
        if not primary:
            singleton.announce_secondary_and_exit()

    port = args.port or _free_port(singleton.DEFAULT_PORT)
    server = web_backend.serve(host="127.0.0.1", port=port)
    url = f"http://127.0.0.1:{port}/"

    print(f"\n  Hearth desktop")
    print(f"  Backend:  {url}")
    print(f"  LM Studio: {web_backend.LOCAL_API_BASE}")
    print(f"  Workspace: {web_backend.WORKSPACE}")
    print(f"  Ctrl-C to stop.\n")

    # Give the server a moment to bind
    time.sleep(0.25)

    if args.no_open:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        server.shutdown()
        return 0

    if args.browser:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\n[hearth.desktop] stopping.")
            server.shutdown()
        return 0

    # Native window path
    try:
        import webview  # pywebview
    except ImportError:
        print("[hearth.desktop] pywebview not installed.", file=sys.stderr)
        print("[hearth.desktop] Run:  pip install pywebview", file=sys.stderr)
        print("[hearth.desktop] Falling back to your default browser.\n", file=sys.stderr)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.shutdown()
        return 1

    # Window background — matches the UI's --bg-0 (cool dark)
    win = webview.create_window(
        "Hearth",
        url=url,
        width=args.width,
        height=args.height,
        min_size=(900, 600),
        background_color="#0a0a0c",
        text_select=True,
        confirm_close=False,
    )
    # Hand the window object to the backend so /api/focus can surface it
    # when a second launch attempt asks us to.
    try:
        web_backend.set_window_ref(win)
    except Exception:
        pass

    def _bye() -> None:
        # Always kill the builtin llama.cpp child so port 1234 is freed,
        # whether we exit cleanly or via Ctrl-C.
        try:
            from . import llmserver
            llmserver.stop_builtin()
        except Exception:
            pass
        try: server.shutdown()
        except Exception: pass

    def _shutdown_on_close() -> None:
        try:
            webview.start(gui=None, debug=False)
        finally:
            _bye()

    try:
        _shutdown_on_close()
    except KeyboardInterrupt:
        _bye()
    return 0


if __name__ == "__main__":
    sys.exit(main())
