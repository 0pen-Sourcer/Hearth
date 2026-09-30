"""The terminal following a brain change made in the window.

When settings.json changed and the endpoint stayed the same, the terminal
asked the server which model it serves and took the first answer. A local
server answers with what it has loaded; a cloud provider answers with its
whole catalog. So any settings write on a cloud brain, the terminal's own
/model included, swapped to whichever model the provider listed first.

    .\\.venv\\Scripts\\python.exe tests\\test_cli_brain_sync.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout

WS = tempfile.mkdtemp(prefix="hearth-sync-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["JARVIS_NO_ONBOARDING"] = "1"
os.environ.pop("LOCAL_API_BASE", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import hearth_cli as C                                               # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


asked = []
C._BRAIN_FROM_ENV = False
C._probe_live_model = lambda url, key="": asked.append(url) or "grok-2-vision-1212"


def sync(url, provider, model, current):
    C.LOCAL_API_BASE = url
    C._read_settings_endpoint = lambda: (url, "k", model, provider)
    cli = object.__new__(C.JarvisCLI)
    cli.current_model = current
    cli._settings_mtime = None
    with open(os.path.join(WS, "settings.json"), "w") as f:
        f.write("{}")
    os.utime(os.path.join(WS, "settings.json"), (time.time(), time.time()))
    with redirect_stdout(io.StringIO()):
        cli._sync_brain_from_settings()
    return cli.current_model


got = sync("https://api.x.ai/v1", "grok", "grok-4.3", "grok-4.3")
check(f"on a cloud brain the saved model stays, not the catalog's first entry ({got})",
      got == "grok-4.3" and not asked)
asked.clear()
got = sync("http://localhost:1234/v1", "builtin", "old-name", "old-name")
check(f"on a local server the loaded model is still read off the server ({got})",
      got == "grok-2-vision-1212" and asked)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all brain sync checks passed")
