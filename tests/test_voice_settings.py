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

# Hindi named beside an English-only size used to be heard as English; the
# same size's multilingual model listens instead.
for _mdl, _lang, _want in (("base.en", "", "en"), ("base.en", "hi", "hi"),
                           ("large-v3", "", None), ("large-v3", "hi", "hi")):
    os.environ["JARVIS_STT_MODEL"] = _mdl
    os.environ["JARVIS_STT_LANGUAGE"] = _lang
    check(f"push-to-talk on {_mdl} asked for {_lang!r} transcribes as {_want!r}",
          _listen._transcribe_language() == _want)
    check(f"voice mode agrees for {_mdl} asked for {_lang!r}",
          _rt._language() == (_want or ""))
# A Hindi voice picked with listening left on its default heard Hindi as English words.
os.environ["JARVIS_STT_MODEL"] = "base.en"
os.environ["JARVIS_STT_LANGUAGE"] = ""
os.environ["HEARTH_VOICE"] = "hf_alpha"
check("with no listening language set, a Hindi voice listens for Hindi or English, on the multilingual model",
      _listen._languages() == ["hi", "en"] and _listen._transcribe_language() is None
      and _listen.model_size() == "base" and _rt._language() == "")

# English said with a Hindi voice picked came back as Devanagari nonsense: forced
# into Hindi. Detected as Hindi or English it stays; detected as something it
# can't be, it is heard again in the likelier of the two.
class _Info:
    def __init__(self, lang, probs):
        self.language, self.all_language_probs = lang, probs
check("English detected stays English", _listen._pick_language(_Info("en", [("en", .8)]), ["hi", "en"]) is None)
check("Hindi detected stays Hindi", _listen._pick_language(_Info("hi", [("hi", .8)]), ["hi", "en"]) is None)
check("a mumble detected as Bengali is heard again as the likelier of Hindi or English",
      _listen._pick_language(_Info("bn", [("bn", .4), ("en", .3), ("hi", .2)]), ["hi", "en"]) == "en")
check("with no language to go by, whatever it detects stands", _listen._pick_language(_Info("bn", []), []) is None)
_asked = []


class _Whisper:
    def transcribe(self, audio, language=None, **k):
        _asked.append(language)
        said = {None: "च्णिव रिए लिए", "en": "check if the relay works"}.get(language, "?")
        return iter([type("S", (), {"text": said})()]), _Info(language or "bn", [("bn", .4), ("en", .35), ("hi", .1)])


_real_load = _listen._try_load_model
_listen._try_load_model = lambda: _Whisper()
os.environ["HEARTH_VOICE"] = "hf_alpha"
_heard = _listen.transcribe("note.webm")
check(f"a voice note detected as a language it can't be is heard again in English ({_heard!r}, asked {_asked})",
      _heard == "check if the relay works" and _asked == [None, "en"])
_listen._try_load_model = _real_load
os.environ.pop("HEARTH_VOICE", None)
os.environ["HEARTH_VOICE"] = "am_michael"
check("an English voice keeps English", _listen._transcribe_language() == "en" and _rt._language() == "en")
os.environ.pop("HEARTH_VOICE", None)
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

# Voice files that are damaged read as missing, so they get fetched again.
import tempfile as _tf                                                # noqa: E402
from hearth import voice as _v, listen as _l                          # noqa: E402
_real_dir = _v.VOICES_DIR
_v.VOICES_DIR = _tf.mkdtemp(prefix="hearth-voices-")
open(os.path.join(_v.VOICES_DIR, "kokoro-v1.0.onnx.part"), "wb").write(b"0" * 4096)
check("a download that never finished is not taken for the voice model", _v._find("kokoro") is None)
open(os.path.join(_v.VOICES_DIR, "kokoro-v1.0.onnx"), "wb").write(b"0" * 4096)
open(os.path.join(_v.VOICES_DIR, "voices-v1.0.bin"), "wb").write(b"0" * 4096)
check("files cut short count as present until they are looked at", _v.models_present())
check("a cut-off voice model is removed", sorted(_v.drop_damaged_models()) == ["kokoro-v1.0.onnx", "voices-v1.0.bin"]
      and not _v.models_present())
_v._MIN_BYTES = {"kokoro": 10, "voices": 10}
open(os.path.join(_v.VOICES_DIR, "kokoro-v1.0.onnx"), "wb").write(b"0" * 4096)
open(os.path.join(_v.VOICES_DIR, "voices-v1.0.bin"), "wb").write(b"0" * 4096)
check("whole files are left alone when the failure isn't about them",
      _v.drop_damaged_models("CUDAExecutionProvider is not available") == [] and _v.models_present())
check("and removed when the failure is about the file",
      len(_v.drop_damaged_models("InvalidProtobuf: [ONNXRuntimeError] Protobuf parsing failed")) == 2)
# A voice download that was cut off carries on from where it stopped.
import http.server, threading                                        # noqa: E402,E401
_BODY = bytes(range(256)) * 400                                       # 102,400 bytes
_asked = []


class _Files(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        _asked.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        body = _BODY[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_dest = os.path.join(_v.VOICES_DIR, "voices-v1.0.bin")
open(_dest + ".part", "wb").write(_BODY[:40000])
_v._download(f"http://127.0.0.1:{_srv.server_address[1]}/voices-v1.0.bin", _dest)
check(f"it asks only for what is missing and ends with the whole file {_asked}",
      _asked == ["bytes=40000-"] and open(_dest, "rb").read() == _BODY and not os.path.exists(_dest + ".part"))
_stop = [False]


def _cancel_after_some(done, total):
    _stop[0] = done > 20000


os.remove(_dest)
try:
    _v._download(f"http://127.0.0.1:{_srv.server_address[1]}/voices-v1.0.bin", _dest,
                 progress=_cancel_after_some, should_cancel=lambda: _stop[0])
    _cancelled = False
except _v.DownloadCancelled:
    _cancelled = True
check("a cancelled download keeps what it had, to carry on from",
      _cancelled and os.path.getsize(_dest + ".part") > 0 and not os.path.exists(_dest))
_srv.shutdown()
_v.VOICES_DIR = _real_dir
_real_ldir, _real_size = _l.VOICES_DIR, _l.MODEL_SIZE
_l.VOICES_DIR, _l.MODEL_SIZE = _tf.mkdtemp(prefix="hearth-whisper-"), "base.en"
_snap = os.path.join(_l.VOICES_DIR, "whisper", "models--Systran--faster-whisper-base.en", "snapshots", "abc")
os.makedirs(_snap)
open(os.path.join(_snap, "model.bin"), "wb").write(b"junk")
os.makedirs(os.path.join(_l.VOICES_DIR, "whisper", "models--Systran--faster-whisper-small.en"))
check("the listening model on disk is found", _l.model_present())
check("damaged, it is removed, and another size is left alone",
      _l._drop_model_files() and not _l.model_present()
      and os.path.isdir(os.path.join(_l.VOICES_DIR, "whisper", "models--Systran--faster-whisper-small.en")))
_l.VOICES_DIR, _l.MODEL_SIZE = _real_ldir, _real_size

# Hindi went unheard: an English-only model was picked and the language choice
# was hidden beside it. Naming another language listens with the same size's
# multilingual model.
_old = (os.environ.get("HEARTH_STT_MODEL"), os.environ.get("HEARTH_STT_LANGUAGE"))
os.environ["HEARTH_STT_MODEL"], os.environ["HEARTH_STT_LANGUAGE"] = "small.en", "hi"
check("Hindi named beside an English-only model listens with the multilingual one, in Hindi",
      _l.model_size() == "small" and _l._transcribe_language() == "hi")
os.environ["HEARTH_STT_LANGUAGE"] = "en"
check("English stays on the English-only model", _l.model_size() == "small.en" and _l._transcribe_language() == "en")
for _k, _v in zip(("HEARTH_STT_MODEL", "HEARTH_STT_LANGUAGE"), _old):
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

# A reply in Hindi was read aloud by the English voice, which can't say it.
from hearth import voice as _v  # noqa: E402
check("a sentence in Devanagari is read by a Hindi voice of the same kind",
      _v.voice_for("नमस्ते, मैं जार्विस हूँ", "am_michael") == "hm_omega"
      and _v.voice_for("नमस्ते", "af_heart") == "hf_alpha")
check("Japanese and Mandarin likewise, and English keeps the chosen voice",
      _v.voice_for("こんにちは", "am_michael")[:1] == "j" and _v.voice_for("你好", "af_heart")[:1] == "z"
      and _v.voice_for("Hello there", "am_michael") == "am_michael")
# Spanish shares English's alphabet, so with Spanish set for listening a
# Spanish reply still went to the English voice and was read with English sounds.
os.environ["JARVIS_STT_LANGUAGE"] = "es"
_es = _v.voice_for("Hola, ¿cómo estás? Ya está hecho.", "af_heart")
_kept = _v.voice_for("Hola", "em_alex")
os.environ["JARVIS_STT_LANGUAGE"] = ""
check(f"with Spanish set for listening, a reply is read by a Spanish voice of the same kind {_es}",
      _es[:2] == "ef" and _kept == "em_alex")
check("with nothing set, the chosen voice reads Latin-alphabet text, whatever was said last",
      _v.voice_for("Hola, ¿cómo estás?", "af_heart") == "af_heart")
os.environ.pop("JARVIS_STT_LANGUAGE", None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice settings checks passed")
