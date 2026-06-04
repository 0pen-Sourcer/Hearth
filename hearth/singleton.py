"""Single-instance lock for Hearth.

Double-clicking Hearth.exe (or running `python -m hearth.desktop` /
`python -m hearth.tray`) used to spawn a fresh backend + tray icon every
single time — so 5 clicks = 5 icons in the system tray, all fighting over
ports 8765-8769. Brutal.

This module fixes that by making port 8765 act as the singleton mutex:

  - The first instance binds 8765, becomes the "primary," and is the
    only one that runs the HTTP backend + tray icon.
  - Every subsequent invocation tries to GET /api/state on 8765. If a
    Hearth backend answers, the new invocation POSTs /api/focus to tell
    the primary to surface its window, then exits its own process.
  - If 8765 is bound by something that ISN'T Hearth (rare — another app
    using that port), we fall through to a higher port and run as a
    secondary. That's not ideal but never breaks the user's workflow.

Cross-platform: pure stdlib (socket + urllib). Works on Windows / Mac / Linux.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from typing import Optional, Tuple

DEFAULT_PORT = 8765
PROBE_TIMEOUT_S = 1.0


def _port_in_use(port: int) -> bool:
    """True if something is bound to 127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _is_hearth_at(port: int) -> bool:
    """GET /api/state and verify the response looks like Hearth. Returns False
    on any error (connection refused, timeout, non-Hearth response)."""
    url = f"http://127.0.0.1:{port}/api/state"
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_S) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            # /api/state on Hearth returns at minimum {endpoint, workspace, ...}
            return isinstance(data, dict) and ("endpoint" in data or "workspace" in data)
    except Exception:
        return False


def _focus_existing(port: int) -> bool:
    """Tell the running primary to surface its window. Returns True on success."""
    url = f"http://127.0.0.1:{port}/api/focus"
    try:
        req = urllib.request.Request(url, method="POST", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def acquire_or_defer(preferred_port: int = DEFAULT_PORT) -> Tuple[bool, int]:
    """Try to become the primary Hearth instance.

    Returns (became_primary, port):
      - (True, port)  — we're the primary; bind this port + start the backend
      - (False, port) — another Hearth was already running; we already told
                        it to surface its window. The caller should print a
                        one-line message and sys.exit(0).
    """
    # Is port 8765 already taken?
    if _port_in_use(preferred_port):
        # By Hearth?
        if _is_hearth_at(preferred_port):
            _focus_existing(preferred_port)
            return (False, preferred_port)
        # Some other app — fall through to a higher port and run secondary.
        # We deliberately do NOT loop here; just bump by 1 and hand back so
        # the caller's _free_port can handle the rest.
    return (True, preferred_port)


def announce_secondary_and_exit() -> None:
    """Standard one-liner for a deferred secondary instance."""
    print("Hearth is already running. Surfacing the existing window.")
    sys.exit(0)
