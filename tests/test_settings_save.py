"""Saving a setting leaves the loaded speech engine alone.

Every save, whatever it changed, dropped the TTS engine, so the next spoken
reply first reloaded the whole speech model. Only moving it to another device
needs that.

Runs in a throwaway workspace.

    .\\.venv\\Scripts\\python.exe tests\\test_settings_save.py
"""
from __future__ import annotations

import os
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="hearth-settings-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import web                                               # noqa: E402

if not os.path.abspath(web.SETTINGS_PATH).lower().startswith(os.path.abspath(WS).lower()):
    sys.exit(f"refusing to run: settings resolved to {web.SETTINGS_PATH}")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


reloads = []
if web._voice is None:
    sys.exit("voice module unavailable")
web._voice.reload = lambda: reloads.append(1)

web._save_settings({"theme": "dark"})
web._save_settings({"voice_speed": 1.2})
web._save_settings({"voice_name": "af_heart"})
check(f"saving unrelated or live-applied settings keeps the engine loaded "
      f"({len(reloads)} reloads)", reloads == [])
web._save_settings({"tts_device": "cuda"})
check("moving speech to another device reloads it", len(reloads) == 1)
web._save_settings({"tts_device": "cuda"})
check("saving the same device again does not", len(reloads) == 1)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all settings save checks passed")
