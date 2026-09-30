"""Checks that a saved voice setting reaches every surface that reads it.

The voice panes save to settings.json and the voice modules read environment
variables. Nothing binds the two, so a setting can be written, displayed as
selected, and quietly apply to one surface only.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_settings.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------- the realtime recorder's size
from hearth import realtime_voice as rv                             # noqa: E402

_saved = {k: os.environ.get(k) for k in ("HEARTH_REALTIME_MODEL", "JARVIS_STT_MODEL")}
try:
    for k in _saved:
        os.environ.pop(k, None)
    check("with nothing set it falls back to the fast model", rv._model_size() == "tiny.en")

    os.environ["JARVIS_STT_MODEL"] = "small.en"
    check("the saved whisper size reaches voice mode", rv._model_size() == "small.en")

    os.environ["HEARTH_REALTIME_MODEL"] = "medium.en"
    check("an explicit override still wins", rv._model_size() == "medium.en")

    os.environ["HEARTH_REALTIME_MODEL"] = "   "
    check("a blank override is not a choice", rv._model_size() == "small.en")
finally:
    for k, v in _saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v

# Nothing in the tree exports HEARTH_REALTIME_MODEL, which is why reading only
# it pinned voice mode to the fallback whatever the setting said.
_tree = []
for _dir, _sub, _files in os.walk(os.path.join(ROOT, "hearth")):
    for _f in _files:
        if _f.endswith((".py", ".html")):
            _tree.append(os.path.join(_dir, _f))
_tree.append(os.path.join(ROOT, "hearth_cli.py"))
_writers = [p for p in _tree
            if re.search(r'\[["\']HEARTH_REALTIME_MODEL["\']\]\s*=',
                         open(p, encoding="utf-8", errors="ignore").read())]
check("nothing sets the override, so the fallback chain is what runs", not _writers)

# ------------------------------------------------------- window and CLI agree
# Settings are exported through hearth.env (HEARTH_* with a JARVIS_* fallback).
_ENV = re.compile(r'_env\.set\(\s*["\']((?:STT|TTS|VOICE)[A-Z_]*)["\']')
gui = set(_ENV.findall(open(os.path.join(ROOT, "hearth", "web.py"), encoding="utf-8").read()))
cli_src = open(os.path.join(ROOT, "hearth_cli.py"), encoding="utf-8").read()
cli = set(_ENV.findall(cli_src)) | set(re.findall(r'"((?:STT|TTS|VOICE)[A-Z_]*)":\s*\(', cli_src))

check("the window does export voice settings", len(gui) >= 5)
missing = sorted(gui - cli)
check("the CLI applies every voice setting the window does"
      + (", missing " + ", ".join(missing) if missing else ""), not missing)

# ---------------------------------------------- speaking anything but English
# The voice bank that ships with the model holds nine languages. Listing only
# the English ones meant the rest were downloaded on every install and
# reachable by nobody, and the phonemizer was fixed at English, so a voice from
# another language would have been handed English sounds for its text.
from hearth import voice as _voice                                 # noqa: E402

_bank = set(_voice.KOKORO_VOICES)
for _v in ("hf_alpha", "hm_omega", "ef_dora", "ff_siwis", "if_sara",
           "jm_kumo", "pf_dora", "zf_xiaobei"):
    check(f"the catalog lists {_v}", _v in _bank)

for _v, _lang in (("am_michael", "en-us"), ("bm_george", "en-gb"),
                  ("ef_dora", "es"), ("ff_siwis", "fr-fr"), ("hf_alpha", "hi"),
                  ("if_sara", "it"), ("jm_kumo", "ja"), ("pf_dora", "pt-br"),
                  # espeak names Mandarin cmn and rejects zh outright.
                  ("zf_xiaobei", "cmn")):
    check(f"{_v} is phonemized as {_lang}", _voice.voice_language(_v) == _lang)

check("an empty voice id still resolves to something",
      _voice.voice_language("") == "en-us")
_src = open(os.path.join(ROOT, "hearth", "voice.py"), encoding="utf-8").read()
check("no call fixes the phonemizer at one language",
      'lang="en-us"' not in _src)

# A reply in a script with its own sentence mark found no boundary, so none of
# it played until all of it had been rendered.
for _mark, _label in (("।", "danda"), ("。", "ideographic stop"),
                      ("？", "fullwidth question"), ("؟", "arabic question")):
    check(f"a chunk may end on the {_label}", _mark in _voice._SENTENCE_END)

# ------------------------------------------------------ the language to hear
from hearth import listen as _listen                               # noqa: E402
from hearth import realtime_voice as _rt                           # noqa: E402

for _mdl, _lang, _want in (("base.en", "", "en"), ("base.en", "hi", "en"),
                           ("large-v3", "", None), ("large-v3", "hi", "hi")):
    os.environ["JARVIS_STT_MODEL"] = _mdl
    os.environ["JARVIS_STT_LANGUAGE"] = _lang
    check(f"push-to-talk on {_mdl} asked for {_lang!r} transcribes as {_want!r}",
          _listen._transcribe_language() == _want)
    check(f"voice mode agrees for {_mdl} asked for {_lang!r}",
          _rt._language() == (_want or ""))
os.environ.pop("JARVIS_STT_LANGUAGE", None)
os.environ.pop("JARVIS_STT_MODEL", None)

# ------------------------------------------- a failure behind the queue is seen
# Synthesis and playback run behind a queue, so speak() answers "ok" before
# either can fail, and both loops caught everything and carried on. Pressing
# speak did nothing, said nothing, and left nothing anywhere to read.
_vsrc = open(os.path.join(ROOT, "hearth", "voice.py"), encoding="utf-8").read()
check("the synthesis loop no longer swallows its failures whole",
      ("except Exception:" + chr(10) + " " * 12 + "continue") not in _vsrc)
check("a failure is recorded where a caller can find it",
      "note_speak_error" in _vsrc and _vsrc.count("note_speak_error(") >= 3)

_before = _voice.last_speak_error().get("at", 0)
_voice.note_speak_error("synthesis", ValueError("no such voice"))
_after = _voice.last_speak_error()
check("the recorded failure names what went wrong",
      "no such voice" in (_after.get("error") or ""))
check("and carries a time, so a stale one is not mistaken for a fresh one",
      _after.get("at", 0) > _before)
_st = _voice.status()
check("status reports it", _st.get("last_speak_error") == _after.get("error"))

_ui = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("the window asks whether a failure landed after the button was pressed",
      "speakErrorSince" in _ui and "last_speak_error_at" in _ui)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice settings checks passed")
