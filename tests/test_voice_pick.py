"""The saved voice is the one that speaks, however Hearth was started.

The speech module read the voice and speed once, when first imported; the
window imports it before applying the saved settings, so after a restart
Settings said Sky while Michael spoke.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_pick.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-voicepick-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
for k in ("HEARTH_VOICE", "JARVIS_VOICE", "HEARTH_VOICE_SPEED", "JARVIS_VOICE_SPEED"):
    os.environ.pop(k, None)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "voice_name": "af_sky", "voice_speed": 1.3}, f)
sys.path.insert(0, ROOT)

from hearth import voice  # noqa: E402  imported first, as the window does
from hearth import web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


web._load_settings()
picked = voice.voice_for("Hello there.", "")
st = voice.status() if hasattr(voice, "status") else {}
check(f"after a start the saved voice speaks ({picked})", picked == "af_sky")
check(f"and at the saved speed ({st.get('default_speed')})", st.get("default_speed") == 1.3)
voice.set_default_voice("am_michael")
check("a voice picked while running takes over", voice.voice_for("Hello there.", "") == "am_michael")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice pick checks passed")
