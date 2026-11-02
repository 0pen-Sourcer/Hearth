"""First-run answers, applied the same way from the window and the terminal.

The answers have to land where the model reads them on every turn, which is
profile.md (who the person is and how they want replies) and rules.md (what to
avoid). Voice and approval choices are settings the rest of Hearth reads. Each
answer owns one profile line, so answering again replaces it. The language
answer is both: listening's setting and the profile line replies follow.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional

# The tone choices both setups offer, and how each reads to the model.
TONES = {
    "casual": "Casual, like a friend. Match their register, slang included.",
    "formal": "Formal and efficient, like a good butler.",
    "brief": "As few words as possible. Answer first, no preamble.",
}
TONE_LABELS = {
    "": "Default: clear, a little dry",
    "casual": "Casual: talk like a friend",
    "formal": "Formal: efficient, butler-like",
    "brief": "Brief: minimum words",
}
AUDIO = ("headphones", "speakers")

# Languages listening knows, as Settings lists them (code: name).
LANGUAGES = {
    "en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "ru": "Russian", "ar": "Arabic", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "it": "Italian",
    "tr": "Turkish", "id": "Indonesian", "vi": "Vietnamese", "bn": "Bengali", "ta": "Tamil", "te": "Telugu",
    "mr": "Marathi", "ur": "Urdu",
}
# The first letter of a voice id for each language that has voices.
_VOICE_LETTER = {"hi": "h", "es": "e", "fr": "f", "it": "i", "pt": "p", "ja": "j", "zh": "z"}


def language_code(answer: str) -> Optional[str]:
    """'hindi', 'Hi' or 'hi' -> 'hi'; None when it isn't a language listening knows."""
    a = (answer or "").strip().lower()
    if a in LANGUAGES:
        return a
    return next((c for c, n in LANGUAGES.items() if n.lower() == a or (len(a) >= 3 and n.lower().startswith(a))), None)


def voice_for(lang: str, current: str = "") -> Optional[str]:
    """A voice that speaks `lang`, of the same kind as the current one, or None when
    no voice speaks it (or the current one already does)."""
    letter = _VOICE_LETTER.get(lang)
    if not letter or (current or "")[:1] == letter:
        return None
    from .voice import KOKORO_VOICES
    kind = (current or "af")[1:2]
    same = [v for v in KOKORO_VOICES if v[:1] == letter and v[1:2] == kind]
    return (same or [v for v in KOKORO_VOICES if v[:1] == letter] or [None])[0]


def _set_profile_field(prefix: str, value: Optional[str]) -> None:
    """Replace the profile line starting with `prefix` (or drop it if empty)."""
    from . import memory
    existing = memory.read_profile()
    lines = [l.strip() for l in existing.splitlines()
             if l.strip().startswith("-") and not l.strip()[1:].strip().startswith(prefix)]
    if value:
        lines.append(f"- {prefix} {value}")
    if not lines:
        # Nothing left: no file means no prompt overhead at all.
        try:
            if os.path.isfile(memory.PROFILE_PATH):
                os.remove(memory.PROFILE_PATH)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(memory.PROFILE_PATH), exist_ok=True)
    with open(memory.PROFILE_PATH, "w", encoding="utf-8") as f:
        f.write(memory._PROFILE_HEADER + "\n\n" + "\n".join(lines) + "\n")


def tone_for(answer: str) -> str:
    """Map a free answer onto one of TONES ('' for the default). The terminal
    used to take a 1-5 bluntness scale, so those still mean something."""
    a = (answer or "").strip().lower()
    if a in TONES or a == "":
        return a
    scale = {"1": "formal", "2": "formal", "3": "", "4": "casual", "5": "brief"}
    if a in scale:
        return scale[a]
    for key in TONES:
        if a.startswith(key[:4]):
            return key
    return a                       # their own words, kept as written


def apply(answers: Dict[str, str]) -> Dict[str, object]:
    """Apply what the person answered. Missing keys are left alone; an empty
    string clears that answer. Returns the settings it saved."""
    a = {k: (v.strip() if isinstance(v, str) else v) for k, v in (answers or {}).items()}
    if "name" in a:
        _set_profile_field("Name:", f"call them {a['name']}." if a["name"] else None)
    if "role" in a:
        _set_profile_field("Work:", a["role"] or None)
    if "tone" in a:
        t = tone_for(a["tone"] or "")
        _set_profile_field("Tone:", TONES.get(t, t) or None)
    if "browser" in a:
        b = a["browser"] or ""
        prof = a.get("profile") or ""
        _set_profile_field("Browser:", (f"open links in {b}" + (f", profile '{prof}'" if prof else "") + ".")
                           if b else None)
    if a.get("avoid") and a["avoid"].lower() not in ("none", "n/a", "nothing", "no"):
        from . import memory
        memory.append_rule(f"Avoid: {a['avoid']}.")

    # Set up on this release: its What's new is not news.
    from .whats_new import mark_seen
    settings: Dict[str, object] = {"onboarded": True, **mark_seen()}
    if a.get("audio") in AUDIO:
        # On speakers the mic hears the reply, so it stays closed while Hearth
        # talks. On headphones it stays open so you can cut in.
        settings["voice_half_duplex"] = a["audio"] == "speakers"
    if a.get("approval_mode"):
        from .tools import APPROVAL_MODES
        if a["approval_mode"] in APPROVAL_MODES:
            settings["approval_mode"] = a["approval_mode"]
    if a.get("agent_name"):
        settings["agent_name"] = a["agent_name"]
    for key in ("ui_theme", "ui_accent", "ui_mode", "voice_name", "voice_speed"):
        if a.get(key):
            settings[key] = a[key]
    if "language" in a and isinstance(a["language"], str) and re.fullmatch(r"[a-z]{0,3}", a["language"]):
        # The language spoken to it: listening uses it (a multilingual model for
        # anything but English); blank means work it out for each utterance.
        # Replies follow it through the profile; English and blank need no line.
        settings["stt_language"] = a["language"]
        name = LANGUAGES.get(a["language"])
        _set_profile_field("Language:", f"reply in {name} unless they write in another language."
                           if name and a["language"] != "en" else None)
    if "install_count" in a:
        settings["install_count"] = a["install_count"] in (True, "yes", "on")   # opt-in: anything else is no
    if "voice" in a:
        on = a["voice"] in (True, "full", "yes", "on")
        settings["voice_tts"] = on
        settings["voice_stt"] = on
    from .web import _save_settings
    _save_settings(settings)
    return settings
