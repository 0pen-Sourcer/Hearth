"""Open a PyWebView window pointed at an existing Hearth server URL.

Used by `hearth.tray` to spawn the desktop window in its own process —
pywebview.start() must run on the main thread, which conflicts with
pystray's blocking event loop. Splitting into a subprocess is the
clean fix.

Standalone usage:
    python -m hearth.desktop_attach --url http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from typing import Optional


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hearth.desktop_attach")
    # Default to a running tray's backend — makes direct double-click of
    # Hearth-window.exe Just Work if the tray is already running on the
    # default port. Tray normally passes --url explicitly anyway.
    parser.add_argument("--url", default="http://127.0.0.1:8765/",
                        help="Backend URL to attach to. Default: localhost:8765.")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=840)
    args = parser.parse_args(argv)

    try:
        import webview  # type: ignore
    except ImportError:
        webbrowser.open(args.url)
        return 0

    # PyWebView on Windows REQUIRES an .ico file for the window icon
    # (PNG raises System.ArgumentException). Prefer .ico, fall back to .png
    # (which works on Linux), else no icon at all.
    here = os.path.dirname(os.path.abspath(__file__))
    asset_dir = os.path.join(os.path.dirname(here), "assets")
    # In PyInstaller bundles, _MEIPASS holds the unpacked tree
    bundle_assets = os.path.join(getattr(sys, "_MEIPASS", ""), "assets")
    icon_path = None
    for candidate in (
        os.path.join(asset_dir, "icon.ico"),
        os.path.join(bundle_assets, "icon.ico"),
        os.path.join(asset_dir, "icon.png"),
        os.path.join(bundle_assets, "icon.png"),
    ):
        if candidate and os.path.isfile(candidate):
            icon_path = candidate
            break
    kwargs = dict(
        width=args.width, height=args.height, min_size=(900, 600),
        background_color="#0a0a0c",
        text_select=True, confirm_close=False,
    )
    def _browser_fallback(err: Exception) -> int:
        # The native window backend failed — most often the pythonnet/.NET
        # loader ("Failed to resolve Python.Runtime.Loader.Initialize") or a
        # missing WebView2 runtime on a fresh machine. The web UI is identical,
        # so open it in the default browser instead of hard-crashing. The tray
        # process keeps serving it at args.url.
        print(f"[desktop] native window unavailable ({type(err).__name__}: {err}); "
              f"opening the web UI in your browser instead: {args.url}")
        try:
            webbrowser.open(args.url)
        except Exception:
            pass
        return 0

    webview.create_window("Hearth", url=args.url, **kwargs)
    try:
        if icon_path:
            webview.start(gui=None, debug=False, icon=icon_path)
        else:
            webview.start(gui=None, debug=False)
    except TypeError:
        # Old pywebview without the icon kwarg — retry once without it.
        try:
            webview.start(gui=None, debug=False)
        except Exception as e:
            return _browser_fallback(e)
    except Exception as e:
        return _browser_fallback(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
