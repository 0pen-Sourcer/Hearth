"""First-run answers reach the model, from the window and the terminal alike.

The window saved the name, tone and browser to settings keys nothing read, so
the model never saw them; the terminal wrote them somewhere else again. Both
now go through one apply, which puts them in the profile the prompt carries
every turn. Answering again replaces an answer instead of stacking a second.

    .\\.venv\\Scripts\\python.exe tests\\test_onboarding.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import memory, web, persona, onboarding                 # noqa: E402

tmp = tempfile.mkdtemp(prefix="hearth_onboard_")
memory.PROFILE_PATH = os.path.join(tmp, "profile.md")
memory.RULES_PATH = os.path.join(tmp, "rules.md")
saved: dict = {}
web._save_settings = lambda d: saved.update(d) or saved

onboarding.apply({"name": "Sam", "role": "video editor", "tone": "casual",
                  "browser": "brave", "profile": "work", "audio": "speakers",
                  "approval_mode": "edits", "voice": "full"})
prompt = persona.system_prompt()
check("the name reaches the prompt", "call them Sam" in prompt)
check("the work reaches the prompt", "video editor" in prompt)
check("the tone reaches the prompt as an instruction", onboarding.TONES["casual"] in prompt)
check("the browser and profile reach the prompt", "brave" in prompt and "'work'" in prompt)
check("speakers keep the mic closed while Hearth talks", saved.get("voice_half_duplex") is True)
check("the approval mode is saved", saved.get("approval_mode") == "edits")
check("voice is switched on", saved.get("voice_tts") is True and saved.get("voice_stt") is True)
check("setup is marked done", saved.get("onboarded") is True)

onboarding.apply({"name": "Alex", "tone": "brief", "audio": "headphones"})
text = open(memory.PROFILE_PATH, encoding="utf-8").read()
check("answering again replaces the name", "Alex" in text and "Sam" not in text)
check("and the tone", onboarding.TONES["brief"] in text and onboarding.TONES["casual"] not in text)
check("answers not given again are kept", "video editor" in text)
check("headphones leave the mic open", saved.get("voice_half_duplex") is False)

onboarding.apply({"approval_mode": "everything"})
check("an unknown approval mode is ignored", saved.get("approval_mode") == "edits")
check("the terminal's old 1-5 bluntness still maps", onboarding.tone_for("5") == "brief")

for k in ("name", "role", "tone", "browser"):
    onboarding.apply({k: ""})
check("clearing every answer removes the profile", not os.path.exists(memory.PROFILE_PATH))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all onboarding checks passed")
