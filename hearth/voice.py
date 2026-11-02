"""Local text-to-speech.

Primary engine:  Kokoro (~82M params, ONNX, sounds genuinely human, real-time
                 on CPU, zero VRAM impact, so it never competes with the LLM for
                 the GPU).
Fallback engine: Piper (smaller, faster, a touch more robotic).

Both are 100% local, free, and offline.

ONE-TIME SETUP (Kokoro path, recommended):
    pip install kokoro-onnx sounddevice numpy

    Then download the model + voices to ~/Jarvis/voices/:
      - kokoro-v1.0.fp16.onnx      (~180MB, release model-files-v1.0)
      - voices-v1.0.bin            (~28MB)
    From: https://huggingface.co/hexgrad/Kokoro-82M (ONNX exports), or:
        https://github.com/thewh1teagle/kokoro-onnx/releases

FALLBACK (Piper):
    pip install piper-tts sounddevice numpy
    Drop any voice .onnx + .json into ~/Jarvis/voices/
    From: https://huggingface.co/rhasspy/piper-voices

Voice picks:
  Kokoro: am_adam (US male), am_michael, bm_george (UK male), bf_emma
  Piper:  en_GB-alan-medium, en_US-ryan-high

Module is import-safe even with no deps installed — speak() returns 'no_voice'
and the rest of Hearth keeps working silently.
"""

from __future__ import annotations

import os
import re
from . import env as _env
import sys
import threading
import time
import wave
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tools import WORKSPACE

VOICES_DIR = os.path.join(WORKSPACE, "voices")
os.makedirs(VOICES_DIR, exist_ok=True)

# Default voice id for Kokoro — am_michael is calm and neutral.
DEFAULT_KOKORO_VOICE = _env.get("VOICE", "am_michael")
# 1.0 = Kokoro's natural rate. Voiceover testing kept this at 1.0 (anything
# higher starts to sound rushed during long answers). Override per session
# with /voice speed <n> or set HEARTH_VOICE_SPEED before launch.
DEFAULT_SPEED = float(_env.get("VOICE_SPEED", "1.0"))

_engine: Optional[Tuple[str, object]] = None  # ("kokoro" | "piper", obj)
_lock = threading.Lock()
# Speaking state lives on a Speaker (below): this PC's speakers have one, and
# a phone call gets its own, so neither cuts or deafens the other. These names
# stay for the code that reads the PC's own.

# How long after TTS stops the mic stays untrusted. Both capture paths (the
# realtime recorder and the CLI push-to-talk loop) read this one value, so the
# tail can be tuned once instead of drifting apart across three files.
ECHO_TAIL_S: float = float(os.environ.get("HEARTH_VOICE_ECHO_TAIL_S", "1.8") or "1.8")


def half_duplex_enabled() -> bool:
    """Hold the mic closed while Hearth speaks. On speakers the mic hears the
    reply and would answer itself, so this is the deterministic stop for that
    loop. On a headset it should be off, so barge-in works. Env wins over the
    saved setting; unreadable settings mean full duplex."""
    v = os.environ.get("HEARTH_VOICE_HALF_DUPLEX")
    if v is not None:
        return v.strip().lower() not in ("0", "false", "no", "off")
    try:
        from .web import _load_settings
        return bool(_load_settings().get("voice_half_duplex"))
    except Exception:
        return False


def note_spoken(text: str) -> None:
    """Record a line the assistant is about to speak, for echo discrimination."""
    DEFAULT.note_spoken(text)


def recently_spoke_text(within_s: float = 6.0) -> str:
    """Text the assistant spoke in the last `within_s` seconds, joined + lowercased.
    Empty if nothing recent."""
    return DEFAULT.recently_spoke_text(within_s)


def is_speaking() -> bool:
    """True while a speak() call is producing audio. listen.py gates STT on
    this so the mic doesn't transcribe the assistant's own voice through the speakers."""
    return DEFAULT.is_speaking()


def seconds_since_spoke() -> float:
    """Seconds since the last TTS chunk finished playing; inf if never spoken.
    Used to extend the post-TTS cooldown so the speaker buffer tail doesn't
    trigger a phantom utterance."""
    return DEFAULT.seconds_since_spoke()


def _mark_speaking_start() -> None:
    DEFAULT.mark_start()


def _mark_speaking_end() -> None:
    DEFAULT.mark_end()


# Last HARD load error — surfaced in status() so we don't silently report
# "engine: null" when the actual issue is, e.g., a missing file format.
_last_load_error: Optional[str] = None
# Last SOFT degradation warning — engine still loaded fine but with a
# downgrade the user should know about (e.g. CUDA requested but onnxruntime-gpu
# isn't installed → fell back to CPU). GUI can render this as a yellow toast
# with an "install" hint, without making it look like TTS is broken.
_last_load_warning: Optional[str] = None
# Last failure from inside the speaking threads. Synthesis and playback run
# behind a queue, so speak() has already answered "ok" by the time either can
# fail, and both loops caught everything and carried on. The result was the
# oldest complaint about voice: pressing speak does nothing, says nothing, and
# leaves nothing anywhere to read. Recorded with the moment it happened so a
# caller can tell a fresh failure from one left over from an earlier turn.
_last_speak_error: Optional[str] = None
_last_speak_error_at: float = 0.0


def note_speak_error(where: str, exc: BaseException) -> None:
    global _last_speak_error, _last_speak_error_at
    _last_speak_error = f"{where}: {type(exc).__name__}: {exc}"
    _last_speak_error_at = time.time()
    try:
        print(f"[voice] {_last_speak_error}", file=sys.stderr, flush=True)
    except Exception:
        pass


def last_speak_error() -> dict:
    return {"error": _last_speak_error, "at": _last_speak_error_at}


def _find(*candidates: str) -> Optional[str]:
    for fn in os.listdir(VOICES_DIR) if os.path.isdir(VOICES_DIR) else []:
        if fn.endswith(".part"):
            continue                    # a download that never finished is not the file
        for cand in candidates:
            if cand in fn.lower():
                return os.path.join(VOICES_DIR, fn)
    return None


# Far below their real sizes (about 177 MB and 28 MB): under this a file is a
# cut-off download or what an antivirus left of it.
_MIN_BYTES = {"kokoro": 50 * 1024 * 1024, "voices": 5 * 1024 * 1024}
_DAMAGE_WORDS = ("protobuf", "corrupt", "unexpected end", "not a valid", "pickle", "bad magic",
                 "truncat", "eoferror", "badzipfile", "cannot reshape")


def drop_damaged_models(error: str = "") -> List[str]:
    """Remove voice files that can't be what they should be, so they read as
    missing and get fetched again. A file is damaged when it is far too small,
    or when loading failed with an error that points at the file itself."""
    gone = []
    blames_file = any(w in (error or "").lower() for w in _DAMAGE_WORDS)
    for key, floor in _MIN_BYTES.items():
        path = _find(key)
        if not path:
            continue
        try:
            small = os.path.getsize(path) < floor
        except OSError:
            small = True
        if small or blames_file:
            try:
                os.remove(path)
                gone.append(os.path.basename(path))
            except OSError:
                pass
    return gone


# One fixed release, the same one install.ps1 pulls, each file checked against its sha256.
# Auto-fetched on first TTS use if the files aren't already on disk (set
# HEARTH_NO_AUTODOWNLOAD=1 to opt out). model-files-v1.0's fp16 model takes `tokens`;
# model-files-v1.1's takes `input_ids` with a float speed, which kokoro_onnx 0.5.0
# feeds as int32, so it fails on every sentence.
_KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_MODEL_FILE = "kokoro-v1.0.fp16.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"
KOKORO_MODEL_URL = _KOKORO_RELEASE + KOKORO_MODEL_FILE
KOKORO_VOICES_URL = _KOKORO_RELEASE + KOKORO_VOICES_FILE
KOKORO_SHA256 = {
    KOKORO_MODEL_URL: "c1610a859f3bdea01107e73e50100685af38fff88f5cd8e5c56df109ec880204",
    KOKORO_VOICES_URL: "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def _undrivable(k) -> bool:
    """True for a model whose inputs this kokoro_onnx sends with the wrong types."""
    try:
        return "input_ids" in [i.name for i in k.sess.get_inputs()]
    except Exception:
        return False


class DownloadCancelled(Exception):
    """Raised inside the fetch hook when a caller asks to stop."""


def _download(url: str, dest: str,
              progress: "Optional[Callable[[int, int], None]]" = None,
              should_cancel: "Optional[Callable[[], bool]]" = None) -> None:
    """Fetch url -> dest through a .part file. A download that was cut off,
    cancelled or left when the app closed is picked up from where it stopped,
    the same way model and engine downloads are.

    `progress(done_bytes, total_bytes)` also receives each update so a caller can
    show real size, speed and ETA rather than a bare percent. `should_cancel`
    is polled during the transfer so a download can be stopped from the UI."""
    from pathlib import Path
    from . import llmserver as _ls
    _last = [-1]

    def _on(done: int, total: int) -> None:
        if total > 0:
            pct = min(100, done * 100 // total)
            if pct != _last[0]:
                _last[0] = pct
                print(f"\r   {os.path.basename(dest)}: {pct}%", end="", flush=True)
        if progress:
            progress(done, total)

    try:
        _ls._download_with_resume(url, Path(dest), Path(dest + ".part"), on_progress=_on,
                                  ua="Inglen (voice files)", cancel=should_cancel,
                                  sha256=KOKORO_SHA256.get(url, ""))
        print()
    except _ls.DownloadCancelled:
        raise DownloadCancelled()


def models_present() -> bool:
    """True when the Kokoro model and voice bank are both on disk."""
    return bool(_find("kokoro") and _find("voices"))


def can_install_models() -> bool:
    """True when the model files are missing but fetchable. The Lite bundle
    ships the packages without the ~100MB of weights, so this is the normal
    state there and the GUI offers an install rather than reporting a fault."""
    if models_present():
        return False
    if _env.get("NO_AUTODOWNLOAD") == "1":
        return False
    try:
        import kokoro_onnx  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


def download_models(progress: "Optional[Callable[[str, int, int], None]]" = None,
                    should_cancel: "Optional[Callable[[], bool]]" = None) -> bool:
    """Fetch the Kokoro model and voice bank.

    `progress(step, done_bytes, total_bytes)` reports across BOTH files as one
    running total, so the downloads overlay shows a single coherent row with a
    real size, speed and ETA. Speaking used to pull these inline, which blocked
    the request for minutes with nothing on screen."""
    global _last_load_error
    if models_present():
        return True
    if _env.get("NO_AUTODOWNLOAD") == "1":
        _last_load_error = "auto-download disabled (HEARTH_NO_AUTODOWNLOAD=1)"
        return False
    os.makedirs(VOICES_DIR, exist_ok=True)
    jobs = []
    if not _find("kokoro"):
        jobs.append(("voice model", KOKORO_MODEL_URL, KOKORO_MODEL_FILE))
    if not _find("voices"):
        jobs.append(("voice bank", KOKORO_VOICES_URL, KOKORO_VOICES_FILE))
    # Sizes are unknown until each transfer starts, so carry the bytes already
    # finished and add the running file on top. The total grows as each file
    # reports in, which the overlay handles.
    carried = {"done": 0, "total": 0}
    try:
        for label, url, fname in jobs:
            def _p(done, total, _l=label):
                if progress:
                    progress(_l, carried["done"] + done, carried["total"] + total)
            _download(url, os.path.join(VOICES_DIR, fname), _p, should_cancel)
            path = os.path.join(VOICES_DIR, fname)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            carried["done"] += size
            carried["total"] += size
    except DownloadCancelled:
        _last_load_error = "voice file download cancelled"
        return False
    except Exception as e:
        _last_load_error = f"voice model download failed: {type(e).__name__}: {e}"
        return False
    ok = models_present()
    if not ok:
        _last_load_error = "voice model download finished but the files are not on disk"
    return ok


def _is_lite_build() -> bool:
    """True on a packaged LITE bundle (slim/API build). Detected by the absence
    of the bundled llama.cpp lib that only the Full build ships — so we don't need
    a separate marker. Running from source is never 'lite'."""
    import sys
    if not getattr(sys, "frozen", False):
        return False
    try:
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return not os.path.exists(os.path.join(base, "llama_cpp", "lib", "llama.dll"))
    except Exception:
        return False


def _ensure_kokoro_models(allow_download: bool = False) -> bool:
    """True when the Kokoro model + voices are on disk.

    allow_download defaults to FALSE so a mere status probe (is_available(),
    the CLI banner, the GUI voice pill) NEVER pulls ~100MB. From a packaged
    Windows build the models are bundled so this is moot, but from source
    nothing is bundled, and booting used to download the whole engine just to
    answer "is voice available?". Only an actual speak() passes allow_download
    =True, so the download happens when the user genuinely uses voice, not on
    launch. Honors HEARTH_NO_AUTODOWNLOAD=1 and never downloads on LITE."""
    global _last_load_error
    if _find("kokoro") and _find("voices"):
        return True
    if not allow_download:
        _last_load_error = "voice models not downloaded yet (use voice once to fetch them)"
        return False
    if _env.get("NO_AUTODOWNLOAD") == "1":
        _last_load_error = "kokoro models missing and auto-download disabled (HEARTH_NO_AUTODOWNLOAD=1)"
        return False
    os.makedirs(VOICES_DIR, exist_ok=True)
    try:
        if not _find("kokoro"):
            print("[voice] Kokoro TTS model missing - downloading once (~180MB)...")
            _download(KOKORO_MODEL_URL, os.path.join(VOICES_DIR, KOKORO_MODEL_FILE))
        if not _find("voices"):
            print("[voice] Kokoro voices missing - downloading once (~28MB)...")
            _download(KOKORO_VOICES_URL, os.path.join(VOICES_DIR, KOKORO_VOICES_FILE))
    except Exception as e:
        _last_load_error = f"Kokoro auto-download failed: {type(e).__name__}: {e}"
        return False
    return bool(_find("kokoro") and _find("voices"))


_healing = [False]            # one re-fetch per failure, never a loop


def _try_kokoro(allow_download: bool = False):
    global _last_load_error
    if not _ensure_kokoro_models(allow_download=allow_download):
        if not _last_load_error:
            _last_load_error = "kokoro: model/voices not found"
        return None
    model = _find("kokoro") if os.path.isdir(VOICES_DIR) else None
    voices = _find("voices") if os.path.isdir(VOICES_DIR) else None
    if not (model and voices and model.endswith(".onnx")):
        _last_load_error = (
            f"kokoro: model.onnx={model!r}, voices={voices!r} (need both, "
            f"with the .onnx file containing 'kokoro' in its name)"
        )
        return None
    try:
        from kokoro_onnx import Kokoro  # type: ignore
    except ImportError as e:
        _last_load_error = f"kokoro_onnx not installed: {e}"
        return None
    # Honor TTS device preference: cuda / dml / cpu (default cpu).
    # CRITICAL: gracefully degrade to CPU if the requested provider isn't
    # installed. The base `onnxruntime` pip wheel ships only CPU+Azure;
    # `onnxruntime-gpu` is a separate install for CUDA, and `onnxruntime-directml`
    # for DML. Without this check, kokoro_onnx silently produces zero-audio
    # frames when a missing provider is requested — looks like "TTS broken"
    # but is really "wrong wheel installed". The fallback exposes the real
    # state via `last_load_error` so the GUI can prompt the user to install
    # the right wheel (or just stay on CPU, which is plenty fast for Kokoro).
    tts_device = _env.get("TTS_DEVICE", "cpu").lower()
    try:
        import onnxruntime as _ort  # type: ignore
        available = set(_ort.get_available_providers())
    except Exception:
        available = {"CPUExecutionProvider"}
    requested = {
        "cuda": "CUDAExecutionProvider",
        "gpu":  "CUDAExecutionProvider",
        "dml":  "DmlExecutionProvider",
        "cpu":  "CPUExecutionProvider",
    }.get(tts_device, "CPUExecutionProvider")
    if requested != "CPUExecutionProvider" and requested not in available:
        # Soft degradation — engine still loads on CPU, but tell the user so
        # they don't think TTS is broken / can install the right wheel.
        global _last_load_warning
        wheel = "onnxruntime-gpu" if "CUDA" in requested else "onnxruntime-directml"
        _last_load_warning = (
            f"TTS device '{tts_device}' requested but the '{requested}' provider "
            f"isn't installed. Falling back to CPU. To enable {tts_device.upper()}, "
            f"install: pip install {wheel}"
        )
        tts_device = "cpu"
        requested = "CPUExecutionProvider"
    providers = [requested] if requested == "CPUExecutionProvider" else [requested, "CPUExecutionProvider"]
    os.environ.setdefault("ORT_TENSORRT_FP16_ENABLE", "1")
    try:
        # Newer kokoro_onnx accepts a `providers=` kw on init; older versions
        # silently ignore extra kwargs. Try both shapes.
        try:
            k = Kokoro(model, voices, providers=providers)  # type: ignore[call-arg]
        except TypeError:
            # Fallback: set provider preference via env var for onnxruntime
            os.environ["ORT_PROVIDERS"] = ",".join(providers)
            k = Kokoro(model, voices)
        if not _undrivable(k):
            return k
        # A model from a release this kokoro_onnx can't drive: swap it for the pinned one.
        del k
        try:
            os.remove(model)
        except OSError:
            pass
        if allow_download and not _healing[0]:
            _healing[0] = True
            try:
                return _try_kokoro(allow_download=True)
            finally:
                _healing[0] = False
        _last_load_error = "The voice model on this PC doesn't fit this version, so I removed it. Get it again from Settings, Voice."
        return None
    except Exception as e:
        # The files themselves may be the fault: half a download, or what an
        # antivirus left. They are removed so they read as missing, and fetched
        # again at once when speaking is what asked for them.
        if drop_damaged_models(f"{type(e).__name__}: {e}"):
            if allow_download and not _healing[0]:
                _healing[0] = True
                try:
                    return _try_kokoro(allow_download=True)
                finally:
                    _healing[0] = False
            _last_load_error = "The voice files on this PC were damaged, so I removed them. Get them again from Settings, Voice."
            return None
        _last_load_error = (
            f"Kokoro init failed: {type(e).__name__}: {e}. "
            f"(model={os.path.basename(model)}, voices={os.path.basename(voices)}, "
            f"device={tts_device})"
        )
        return None


def _try_piper():
    global _last_load_error
    if not os.path.isdir(VOICES_DIR):
        return None
    onnx = next(
        (
            os.path.join(VOICES_DIR, f)
            for f in os.listdir(VOICES_DIR)
            if f.endswith(".onnx") and "kokoro" not in f.lower()
        ),
        None,
    )
    if not onnx:
        return None
    try:
        from piper import PiperVoice  # type: ignore
    except ImportError as e:
        _last_load_error = f"piper not installed: {e}"
        return None
    try:
        return PiperVoice.load(onnx)
    except Exception as e:
        _last_load_error = f"Piper init failed: {type(e).__name__}: {e}"
        return None


def _load_engine(allow_download: bool = False) -> Optional[Tuple[str, object]]:
    global _engine, _last_load_error, _last_load_warning
    if _engine is not None:
        return _engine
    _last_load_error = None
    # NOTE: don't reset _last_load_warning here — _try_kokoro may set it
    # mid-call to flag a soft degradation (e.g. CUDA->CPU fallback). We
    # only clear it on a successful engine load WITHOUT a warning being set.
    pre_warning = _last_load_warning
    _last_load_warning = None
    k = _try_kokoro(allow_download=allow_download)
    if k is not None:
        _engine = ("kokoro", k)
        _last_load_error = None
        return _engine
    p = _try_piper()
    if p is not None:
        _engine = ("piper", p)
        _last_load_error = None
        return _engine
    # No engine — restore the prior warning in case it was relevant
    _last_load_warning = pre_warning
    return None


def reload() -> None:
    """Force re-detect the voice engine — call after dropping new files
    into the voices/ directory without restarting the app."""
    global _engine, _last_load_error
    _engine = None
    _last_load_error = None


# The voice and speed are read when speaking, not when this module is first
# imported: a surface that imports it before its settings are applied would
# otherwise keep the defaults for the whole session. A pick made while running wins.
_PICKED: Dict[str, Any] = {}


def current_voice() -> str:
    return _PICKED.get("voice") or (_env.get("VOICE", "") or "").strip() or "am_michael"


def current_speed() -> float:
    if "speed" in _PICKED:
        return _PICKED["speed"]
    try:
        return max(0.5, min(2.5, float(_env.get("VOICE_SPEED", "") or 1.0)))
    except ValueError:
        return 1.0


def set_default_voice(name: str) -> None:
    """Change the default voice id used for subsequent speak() calls."""
    global DEFAULT_KOKORO_VOICE
    if (name or "").strip():
        _PICKED["voice"] = name.strip()
    DEFAULT_KOKORO_VOICE = current_voice()


def set_speed(speed: float) -> float:
    """Change the default TTS playback rate for subsequent speak() calls.
    Clamped to a sane 0.5x-2.5x range. Returns the value actually set."""
    global DEFAULT_SPEED
    try:
        s = float(speed)
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    DEFAULT_SPEED = max(0.5, min(2.5, s))
    _PICKED["speed"] = DEFAULT_SPEED
    return DEFAULT_SPEED


# Built-in Kokoro voice catalog. The voice bank that ships alongside the model
# carries all of these, and only the English ones were listed, so the other
# eight languages were downloaded on every install and reachable by nobody.
KOKORO_VOICES = [
    # American male
    "am_michael", "am_adam", "am_eric", "am_echo", "am_fenrir",
    "am_liam", "am_onyx", "am_puck", "am_santa",
    # American female
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    # British male
    "bm_george", "bm_lewis", "bm_daniel", "bm_fable",
    # British female
    "bf_emma", "bf_isabella", "bf_alice", "bf_lily",
    # Spanish
    "ef_dora", "em_alex", "em_santa",
    # French
    "ff_siwis",
    # Hindi
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    # Italian
    "if_sara", "im_nicola",
    # Japanese
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    # Portuguese
    "pf_dora", "pm_alex", "pm_santa",
    # Mandarin
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
]

# A voice id begins with a letter naming its language, and the phonemizer needs
# that language to turn text into the sounds the voice was trained on. The code
# was fixed at English, so a Hindi voice was handed English phonemes and read
# the text as though it were English. The codes are the ones espeak answers to:
# Mandarin is "cmn" there, not "zh", which it rejects outright.
_VOICE_LANG = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}
# What to call each one where a person reads it.
VOICE_LANG_NAMES = {
    "en-us": "English (US)", "en-gb": "English (UK)", "es": "Spanish",
    "fr-fr": "French", "hi": "Hindi", "it": "Italian", "ja": "Japanese",
    "pt-br": "Portuguese", "cmn": "Mandarin",
}


def voice_language(voice: str) -> str:
    """The phonemizer language a voice id belongs to."""
    return _VOICE_LANG.get((voice or "")[:1].lower(), "en-us")


# Writing systems a voice can't read from another language. A Hindi reply is
# in Devanagari, which an English voice can only spell out as noise; such a
# sentence goes to a voice of that language, of the same kind as the chosen one.
_SCRIPTS = (("h", re.compile(r"[\u0900-\u097F]")),
            ("j", re.compile(r"[\u3040-\u30FF]")),
            ("z", re.compile(r"[\u4E00-\u9FFF]")))


# Spanish, French, Italian and Portuguese share the Latin alphabet with English,
# so the text alone can't say which voice reads it; an English voice read a
# Spanish reply with English sounds. When one of them is the language set for
# listening, a reply goes to a voice of that language. It follows the setting,
# never a guess from one utterance: detection on a few spoken words is unreliable.
_LATIN_VOICE = {"es": "e", "fr": "f", "it": "i", "pt": "p"}
_LATIN_ONLY = re.compile(r"^[\s\x00-\x7F\u00A0-\u024F\u2010-\u206F]*$")


def _same_kind(code: str, voice: str) -> str:
    kind = voice[1:2] if voice[1:2] in ("f", "m") else "f"
    same = [v for v in KOKORO_VOICES if v[:1] == code and v[1:2] == kind]
    return (same or [v for v in KOKORO_VOICES if v[:1] == code] or [voice])[0]


def voice_for(text: str, voice: str) -> str:
    """The voice to read this text with: the chosen one, unless the text is
    written in a script only another language's voices read, or the language
    set for listening shares English's alphabet and the chosen voice isn't of
    that language."""
    voice = voice or current_voice()
    for code, pat in _SCRIPTS:
        if pat.search(text or ""):
            return voice if voice[:1] == code else _same_kind(code, voice)
    code = _LATIN_VOICE.get((_env.get("STT_LANGUAGE", "") or "").strip().lower()[:2])
    if code and voice[:1] != code and _LATIN_ONLY.match(text or ""):
        return _same_kind(code, voice)
    return voice


# A sentence ends at any of these; the voice is chosen again at each one.
# Latin stops count only before a space, so 3.5 and e.g. stay whole.
_SPLIT = re.compile(r"(?<=[.!?\u2026])(?=\s)|(?<=[\n\u0964\u0965\u3002\uFF01\uFF1F\u061F])")
_KANA = _SCRIPTS[1][1]


def voice_runs(text: str, voice: str) -> List[Tuple[str, str]]:
    """[(text, voice)]: each sentence with the voice that reads it, sentences
    next to each other that share a voice joined. A reply that is English with
    a Hindi line in it is read in the chosen voice, then a Hindi voice, then
    the chosen voice again. In Japanese, a sentence of kanji alone is still
    Japanese when the reply has kana elsewhere."""
    voice = voice or current_voice()
    japanese = bool(_KANA.search(text or ""))
    runs: List[Tuple[str, str]] = []
    for part in _SPLIT.split(text or ""):
        if not part.strip():
            continue
        v = voice_for(part, voice)
        if japanese and v[:1] == "z" and voice[:1] != "z":
            v = voice if voice[:1] == "j" else _same_kind("j", voice)
        if runs and runs[-1][1] == v:
            runs[-1] = (runs[-1][0] + part, v)
        else:
            runs.append((part, v))
    return [(t.strip(), v) for t, v in runs]


def _kokoro_say(obj, text: str, voice, speed):
    """(float samples, sample rate) for text that may change language between
    sentences: each run in its own voice, joined end to end."""
    import numpy as np  # type: ignore
    pieces, sr = [], 24000
    for part, v in voice_runs(text, voice):
        samples, sr = obj.create(part, voice=v, speed=speed, lang=voice_language(v))  # type: ignore[attr-defined]
        pieces.append(np.asarray(samples))
    if not pieces:
        return np.zeros(0, dtype=np.float32), sr
    return (pieces[0] if len(pieces) == 1 else np.concatenate(pieces)), sr


# What a voice says when it is tried: a line worth hearing, from that
# language's own writers, in the voice's language so it can say it.
SAMPLES = {
    "e": [("Caminante, no hay camino, se hace camino al andar.", "Antonio Machado")],
    "f": [("On ne voit bien qu'avec le cœur. L'essentiel est invisible pour les yeux.", "Saint-Exupéry")],
    "h": [("काल करे सो आज कर, आज करे सो अब।", "Kabir"),
          ("धीरे-धीरे रे मना, धीरे सब कुछ होय। माली सींचे सौ घड़ा, ऋतु आए फल होय।", "Kabir"),
          ("बुरा जो देखन मैं चला, बुरा न मिलिया कोय। जो दिल खोजा आपना, मुझसा बुरा न कोय।", "Kabir"),
          ("ऐसी बानी बोलिए, मन का आपा खोय। औरन को शीतल करे, आपहु शीतल होय।", "Kabir"),
          ("मन के हारे हार है, मन के जीते जीत।", "Kabir"),
          ("रहिमन धागा प्रेम का, मत तोड़ो चटकाय। टूटे से फिर ना मिले, मिले गाँठ परि जाय।", "Rahim"),
          ("रहिमन देखि बड़ेन को, लघु न दीजिए डारि। जहाँ काम आवे सुई, कहा करे तरवारि।", "Rahim"),
          ("तुलसी इस संसार में, भाँति भाँति के लोग। सबसे हिल मिल चालिए, नदी नाव संजोग।", "Tulsidas")],
    "i": [("Fatti non foste a viver come bruti, ma per seguir virtute e canoscenza.", "Dante")],
    "j": [("千里の道も一歩から。", "a Japanese proverb")],
    "p": [("Tudo vale a pena se a alma não é pequena.", "Fernando Pessoa")],
    "z": [("千里之行，始于足下。", "Laozi")],
    "": [("To strive, to seek, to find, and not to yield.", "Tennyson"),
         ("A journey of a thousand miles begins with a single step.", "Lao Tzu"),
         ("We know what we are, but know not what we may be.", "Shakespeare"),
         ("Forever is composed of nows.", "Emily Dickinson"),
         ("Keep your face always toward the sunshine, and shadows will fall behind you.", "Walt Whitman"),
         ("You have power over your mind, not outside events. Realize this, and you will find strength.",
          "Marcus Aurelius"),
         ("Do not go where the path may lead. Go instead where there is no path, and leave a trail.", "Emerson"),
         ("I am not afraid of storms, for I am learning how to sail my ship.", "Louisa May Alcott"),
         ("It is never too late to be what you might have been.", "George Eliot"),
         ("Luck is what happens when preparation meets opportunity.", "Seneca")],
}
_sample_at = [int(time.time()) % 10]


def sample_line(voice: str = "") -> Dict[str, str]:
    """{"text", "by"}: a different line each time, in the voice's language."""
    lines = SAMPLES.get((voice or "")[:1].lower()) or SAMPLES[""]
    _sample_at[0] += 1
    text, by = lines[_sample_at[0] % len(lines)]
    return {"text": text, "by": by}


def list_voices() -> List[str]:
    return list(KOKORO_VOICES)


def is_available() -> bool:
    return _load_engine() is not None


# Amplitude sink — the GUI voice overlay registers one to drive its dot grid
# from real voice loudness instead of a canned wave. Called ~25x/sec during TTS.
_level_sink: Optional[Callable[[float], None]] = None


def set_level_sink(cb: Optional[Callable[[float], None]]) -> None:
    global _level_sink
    _level_sink = cb


# The microphone's level while voice mode listens, so both the window and the
# overlay move when you talk, not only when Hearth does.
_mic_level: float = 0.0
_mic_level_ts: float = 0.0
_mic_sink: Optional[Callable[[float], None]] = None
_mic_sent: float = 0.0


def set_mic_sink(cb: Optional[Callable[[float], None]]) -> None:
    global _mic_sink
    _mic_sink = cb


def set_mic_level(v: float) -> None:
    """Record the mic's level (0..1) and pass it on about 15 times a second."""
    global _mic_level, _mic_level_ts, _mic_sent
    _mic_level = 0.6 * _mic_level + 0.4 * max(0.0, min(1.0, v))
    _mic_level_ts = time.time()
    sink = _mic_sink
    if sink and _mic_level_ts - _mic_sent > 0.066:
        _mic_sent = _mic_level_ts
        try:
            sink(round(_mic_level, 3))
        except Exception:
            pass


def current_mic_level() -> float:
    """Latest mic level, or 0.0 when nothing has been heard for a moment."""
    return _mic_level if time.time() - _mic_level_ts < 0.3 else 0.0


# Most-recent TTS amplitude, pollable (decays to 0 when idle).
_current_level: float = 0.0
_current_level_ts: float = 0.0

# Voice-loop state for the desktop HUD: idle/listening/thinking/speaking.
_voice_state: str = "idle"


def set_voice_state(s: str) -> None:
    global _voice_state
    _voice_state = s if s in ("idle", "listening", "thinking", "speaking") else "idle"


def voice_state() -> str:
    return _voice_state


def note_level(v: float) -> None:
    """The loudness of the voice playing somewhere else (the window's voice mode), for the HUD."""
    global _current_level, _current_level_ts
    _current_level = 0.62 * _current_level + 0.38 * max(0.0, min(1.0, float(v)))
    _current_level_ts = time.time()


def current_level() -> float:
    """Latest real TTS amplitude 0..1, or 0.0 if nothing has played for ~150ms."""
    if time.time() - _current_level_ts > 0.15:
        return 0.0
    return _current_level


def _emit_levels(arr, sample_rate: int) -> None:
    # RMS envelope; updates current_level() and forwards to the sink if present.
    try:
        import numpy as np
        a = arr.astype("float32") / 32768.0
        win = max(1, int(sample_rate * 0.04))
        # Raw per-40ms RMS swings between loud vowels and near-silent consonants
        # 25x/sec — driving the HUD with it made the dot grid strobe. Gain 2.6
        # (was 3.4) so it doesn't slam to 1.0 and flatten; the running smooth
        # below turns phoneme jitter into a word-level breathing envelope.
        levels = [min(1.0, float(np.sqrt(np.mean(seg * seg))) * 2.6)
                  for seg in (a[i:i + win] for i in range(0, len(a), win)) if len(seg)]
        sink = _level_sink

        def _run():
            global _current_level, _current_level_ts
            env = 0.0
            for lv in levels:
                # Exponential smooth (~word cadence) so the HUD breathes, not strobes.
                env = env * 0.62 + lv * 0.38
                _current_level = env
                _current_level_ts = time.time()
                if sink is not None:
                    try:
                        sink(env)
                    except Exception:
                        pass
                time.sleep(0.04)
            _current_level = 0.0
            _current_level_ts = time.time()
            if sink is not None:
                try:
                    sink(0.0)
                except Exception:
                    pass
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass


def _play(audio_bytes_or_array, sample_rate: int) -> None:
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np  # type: ignore
    except (ImportError, OSError):            # OSError: no PortAudio library on this system
        return
    if isinstance(audio_bytes_or_array, (bytes, bytearray)):
        arr = np.frombuffer(audio_bytes_or_array, dtype=np.int16)
    else:
        arr = audio_bytes_or_array
        if hasattr(arr, "dtype") and arr.dtype.kind == "f":
            import numpy as np  # type: ignore
            arr = (arr * 32767).clip(-32768, 32767).astype(np.int16)
    _emit_levels(arr, sample_rate)
    sd.play(arr, sample_rate)
    sd.wait()


# Where a chunk may end. The Latin marks alone meant a reply in a script that
# ends its sentences differently never found a boundary, so the whole reply went
# to the synthesizer as one piece and playback only began once all of it was
# rendered. Devanagari danda, the ideographic full stop and its comma, the
# fullwidth marks, the Arabic question mark, and an ellipsis.
_SENTENCE_END = (".", "!", "?", "\n",
                 "।", "॥",          # । ॥
                 "。", "、",          # 。 、
                 "！", "？",          # ！ ？
                 "؟",                    # ؟
                 "…")                    # …


def _clean_for_tts(text: str) -> str:
    """Strip markdown / paths / huge digit runs / other things that sound
    awful when Kokoro tries to pronounce them.

    Specifically:
      - Removes fenced code blocks (``` ... ```)
      - Removes inline code (`x`) — pronounces as if literal
      - Strips bold/italic markers (`**`, `__`, `_`, `*`)
      - Strips headings (`### Foo` -> `Foo`)
      - Strips list markers (`- `, `* `, `1. `)
      - Strips block-quote markers (`> `)
      - Replaces 8+ digit numeric runs with "the file" (timestamps, hashes)
      - Collapses 3+ newlines to a single pause
    """
    import re as _re
    if not text:
        return ""
    s = text
    # Drop fenced code blocks entirely (sound like garbage)
    s = _re.sub(r"```[\s\S]*?```", " ", s)
    # Tool-call payloads. Some models emit their calls as text rather than as a
    # structured field, so the arguments survive into the spoken text and the
    # user hears things like 'query: ... limit: 6' read out mid-sentence.
    s = _re.sub(r"<tool_call>[\s\S]*?</tool_call>", " ", s, flags=_re.I)
    s = _re.sub(r"<\|?tool_call\|?>[\s\S]*?(<\|?/?tool_call\|?>|$)", " ", s, flags=_re.I)
    # A lone tag can survive the pair-strip above when the opening was in a
    # structured field and only the close leaked into the text. Kokoro reads the
    # leftover "</tool_call>" aloud as "slash tool call", so drop any bare
    # tool-call tag and pipe-delimited channel markers (<|channel|>, etc.).
    s = _re.sub(r"<\s*/?\s*\|?\s*tool[_ ]?call\s*\|?\s*/?\s*>", " ", s, flags=_re.I)
    s = _re.sub(r"<\|[^|>]*\|>", " ", s)
    s = _re.sub(r"<｜[^｜>]*｜>", " ", s)
    # A flat JSON object with a quoted key, i.e. the shape of tool arguments.
    # Kept deliberately narrow so ordinary prose with braces survives.
    s = _re.sub(r'\{\s*"[^"{}]+"\s*:[^{}]*\}', " ", s)
    # Inline code — KEEP the content, just strip the backticks. So `'Hello'`
    # or `path` becomes 'Hello' or path. Only drop content if it looks like
    # a Windows path or a long token (>20 chars, no spaces).
    def _inline_code(m):
        inner = m.group(1)
        if _re.match(r"^[A-Za-z]:[\\/]", inner) or (len(inner) > 20 and " " not in inner):
            return " "
        return inner
    s = _re.sub(r"`([^`\n]+)`", _inline_code, s)
    # Tables: the rule under the header goes, each row is read as its cells in
    # turn, and a pipe left in a piece cut mid-row is a pause.
    s = _re.sub(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", "", s, flags=_re.M)
    s = _re.sub(r"^[ \t]*\|(.*?)\|?[ \t]*$",
                lambda m: ", ".join(c.strip() for c in m.group(1).split("|") if c.strip()) + ".", s, flags=_re.M)
    s = _re.sub(r"[ \t]*\|[ \t]*", ", ", s)
    s = _re.sub(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$", "", s, flags=_re.M)      # horizontal rules
    s = _re.sub(r"-{3,}", " ", s)
    s = _re.sub(r"^[ \t]*[•●▪◦][ \t]*", "", s, flags=_re.M)
    s = _re.sub(r"[ \t]*[→⇒➜][ \t]*", ", ", s)
    # Headings: `### Foo` → `Foo`
    s = _re.sub(r"^\s{0,3}#{1,6}\s+", "", s, flags=_re.M)
    # Block quotes
    s = _re.sub(r"^\s{0,3}>\s?", "", s, flags=_re.M)
    # List markers
    s = _re.sub(r"^\s{0,3}[\-\*\+]\s+", "", s, flags=_re.M)
    s = _re.sub(r"^\s{0,3}\d+\.\s+", "", s, flags=_re.M)
    # Bold/italic markers
    s = _re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", s)
    s = _re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", s)
    # Stray markdown chars
    s = s.replace("**", "").replace("__", "")
    s = _re.sub(r"(?<!\w)[*_~](?!\w)", "", s)
    # Markdown links: [text](url) → text
    s = _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # Windows-style paths — replace with "a file"
    s = _re.sub(r"[A-Za-z]:[\\/][\w\.\-\\\/ ]+", "a file", s)
    # Long numeric runs (timestamps, hashes) — replace with "the file"
    s = _re.sub(r"\d{8,}", "the file", s)
    # Strip emojis + pictographs + variation selectors / ZWJ — Kokoro either
    # mispronounces them or emits a weird noise; nobody wants "fire emoji" read
    # aloud. Covers emoticons, symbols/pictographs, transport, supplemental,
    # dingbats, flags.
    s = _re.sub(
        "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
        "\U00002B00-\U00002BFF\U0001F900-\U0001F9FF️‍♀♂]",
        "", s)
    # Em-dash / en-dash → comma (more natural pause)
    s = s.replace("—", ", ").replace("–", ", ")
    # Collapse repeated newlines
    s = _re.sub(r"\n{2,}", ". ", s)
    s = _re.sub(r"\s{2,}", " ", s)
    # What the steps above leave around a mark they took out: "word ." and "..".
    s = _re.sub(r"[ \t]+([.,!?;:])", r"\1", s)
    s = _re.sub(r"(?<![.!?])([.!?])\.(?!\.)", r"\1", s)
    s = _re.sub(r"^[.,;:\s]+", "", s)
    return s.strip()


def _chunk_sentences(text: str, min_chars: int = 60):
    """Yield speakable chunks. We split at sentence boundaries so the first
    chunk starts playing fast, but never below ~min_chars to avoid choppy
    one-word audio."""
    buf: List[str] = []
    chars = 0
    for ch in text:
        buf.append(ch)
        chars += 1
        if ch in _SENTENCE_END and chars >= min_chars:
            yield "".join(buf).strip()
            buf, chars = [], 0
    if buf:
        tail = "".join(buf).strip()
        if tail:
            yield tail


def stop() -> None:
    """Cut the currently-speaking turn AND drop any queued sentence chunks
    waiting on the lock. Called when a new user message arrives (or on
    barge-in) so audio doesn't trail into the next response."""
    DEFAULT.stop()


def reset_abort() -> None:
    """Clear the abort flag so the NEXT user turn's TTS can play. Called at the
    start of a fresh recording cycle (CLI mic toggle / GUI voiceCycle listening
    phase). Without this, the flag set by stop() would persist forever and
    silence the assistant on every subsequent turn."""
    DEFAULT.reset_abort()


# ---- Gapless TTS pipeline ---------------------------------------------------
# One synth thread per speaker stays AHEAD of playback filling a queue, and one
# play thread writes chunks into a single continuous output with no gap between
# them, so sentences run together with no seam. Set HEARTH_TTS_GAPLESS=0 to fall
# back to synthesizing and playing one chunk at a time.
import queue as _queue

_TTS_GAPLESS = _env.get("TTS_GAPLESS", "1") != "0"
_synth_engine_lock = threading.Lock()   # one synthesis at a time across speakers


def _to_int16(samples):
    import numpy as np  # type: ignore
    arr = samples
    if hasattr(arr, "dtype") and arr.dtype.kind == "f":
        arr = (arr * 32767).clip(-32768, 32767).astype(np.int16)
    return arr


def _synth_chunk(kind: str, obj, chunk: str, voice, speed):
    """(int16 samples, sample rate) for one chunk of text."""
    if kind == "kokoro":
        samples, sr = _kokoro_say(obj, chunk, voice, speed)
        return _to_int16(samples), sr
    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        obj.synthesize(chunk, wav)  # type: ignore[attr-defined]
    buf.seek(0)
    with wave.open(buf, "rb") as wav:
        sr = wav.getframerate()
        import numpy as np  # type: ignore
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16), sr


class Speaker:
    """Where the assistant's voice goes, with its own speaking state.

    With no sink it plays on this PC's speakers (the window, the terminal,
    voice mode). With a sink, every frame of audio goes to it instead, paced to
    real time (`lead` seconds ahead) so is_speaking() follows what the far end
    is playing and a stop still catches audio not sent yet. A sink is a
    callable taking (int16 samples, sample rate); it may have flush(), called
    when speech is cut."""

    FRAME_S = 0.06

    def __init__(self, sink: Optional[Callable] = None, lead: float = 0.3, first_chunk: int = 60):
        self.sink = sink
        self.lead = lead
        self.first_chunk = first_chunk
        self.abort = threading.Event()
        self._count = 0
        self._last = 0.0
        self._state_lock = threading.Lock()
        self._log: "list[tuple[float, str]]" = []
        self._log_lock = threading.Lock()
        self.synth_q: "_queue.Queue" = _queue.Queue()
        self.audio_q: "_queue.Queue" = _queue.Queue(maxsize=6)
        self._pipe_lock = threading.Lock()
        self._threads: "list[threading.Thread]" = []
        self._synthing = False
        self._pending = 0           # texts handed to speak() whose synthesis hasn't finished

    # ---- state
    def busy(self) -> bool:
        """Anything still to be heard: text waiting, being made into audio, or playing."""
        return (self._synthing or self._pending > 0 or not self.synth_q.empty() or not self.audio_q.empty()
                or self.is_speaking())

    def note_spoken(self, text: str) -> None:
        if not text:
            return
        with self._log_lock:
            self._log.append((time.time(), text.lower()))
            if len(self._log) > 40:
                del self._log[:-40]

    def recently_spoke_text(self, within_s: float = 6.0) -> str:
        cutoff = time.time() - within_s
        with self._log_lock:
            return " ".join(t for (ts, t) in self._log if ts >= cutoff)

    def is_speaking(self) -> bool:
        return self._count > 0

    def seconds_since_spoke(self) -> float:
        if self._last == 0.0:
            return float("inf")
        return max(0.0, time.time() - self._last)

    def mark_start(self) -> None:
        with self._state_lock:
            self._count += 1
        if self.sink is None:
            set_voice_state("speaking")

    def mark_end(self) -> None:
        with self._state_lock:
            self._count = max(0, self._count - 1)
            self._last = time.time()
            still = self._count > 0
        if not still and self.sink is None:
            set_voice_state("idle")

    # ---- speaking
    def speak(self, text: str, blocking: bool = False, voice: Optional[str] = None,
              speed: Optional[float] = None) -> str:
        """'ok' | 'no_voice' | 'empty'."""
        text = _clean_for_tts(text or "")
        if not text:
            return "empty"
        if _load_engine(allow_download=True) is None:
            return "no_voice"
        speed = speed if speed is not None else current_speed()
        self.note_spoken(text)
        if not _TTS_GAPLESS and self.sink is None:
            return _speak_legacy(text, blocking, voice, speed)
        if self.abort.is_set():
            return "ok"  # a stop() landed; don't queue into a cut turn
        self._ensure_pipeline()
        with self._state_lock:
            self._pending += 1
        self.synth_q.put((text, voice, speed))
        if blocking:
            # Done once nothing is waiting, being made or playing, seen a few times running:
            # a chunk passes from the play queue to playing between two looks.
            idle = 0
            while not self.abort.is_set() and idle < 3:
                idle = 0 if self.busy() else idle + 1
                time.sleep(0.05)
        return "ok"

    def stop(self) -> None:
        self.abort.set()
        self._drain()
        if self.sink is None:
            try:
                import sounddevice as sd  # type: ignore
                sd.stop()
            except Exception:
                pass
        else:
            flush = getattr(self.sink, "flush", None)
            if callable(flush):
                try:
                    flush()
                except Exception:
                    pass
        # Idle at once, so the echo tail counts from the cut, not from the play loop's next wake.
        if self.is_speaking():
            with self._state_lock:
                self._count = 0
                self._last = time.time()
            if self.sink is None:
                set_voice_state("idle")

    def reset_abort(self) -> None:
        self.abort.clear()

    # ---- pipeline
    def _drain(self) -> None:
        for q in (self.synth_q, self.audio_q):
            try:
                while True:
                    q.get_nowait()
                    if q is self.synth_q:
                        with self._state_lock:
                            self._pending = max(0, self._pending - 1)
            except _queue.Empty:
                pass

    def _ensure_pipeline(self) -> None:
        with self._pipe_lock:
            if self._threads and all(t.is_alive() for t in self._threads):
                return
            play = self._play_local if self.sink is None else self._play_remote
            self._threads = [threading.Thread(target=self._synth_loop, daemon=True, name="hearth-tts-synth"),
                             threading.Thread(target=play, daemon=True, name="hearth-tts-play")]
            for t in self._threads:
                t.start()

    def _synth_loop(self) -> None:
        while True:
            text, voice, speed = self.synth_q.get()
            self._synthing = True
            try:
                self._synth_one(text, voice, speed)
            finally:
                self._synthing = False
                with self._state_lock:
                    self._pending = max(0, self._pending - 1)

    def _synth_one(self, text: str, voice, speed) -> None:
        if self.abort.is_set():
            return
        engine = _load_engine()
        if engine is None:
            return
        kind, obj = engine
        if kind not in ("kokoro", "piper"):
            return
        try:
            # A call starts talking at the first clause; the PC waits for a fuller sentence.
            for chunk in _chunk_sentences(text, min_chars=self.first_chunk):
                if self.abort.is_set():
                    break
                with _synth_engine_lock:
                    arr, sr = _synth_chunk(kind, obj, chunk, voice, speed)
                # Blocks while the play queue is full: synthesis stays just ahead of playback.
                while not self.abort.is_set():
                    try:
                        self.audio_q.put((arr, sr), timeout=0.2)
                        break
                    except _queue.Full:
                        continue
        except Exception as e:
            note_speak_error("synthesis", e)

    def _play_local(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
        except (ImportError, OSError) as e:
            note_speak_error("playback device", e)
            return
        stream = None
        cur_sr = None
        playing = False
        FRAME = 2400  # ~0.1s at 24k: small enough that barge-in cuts fast
        while True:
            try:
                item = self.audio_q.get(timeout=0.4)
            except _queue.Empty:
                # Idle: close the device and drop back to 'idle' so the dot grid
                # settles and the mic isn't blocked by a held output stream.
                if stream is not None:
                    try: stream.stop(); stream.close()
                    except Exception: pass
                    stream, cur_sr = None, None
                if playing:
                    playing = False
                    self.mark_end()
                continue
            arr, sr = item
            if self.abort.is_set():
                continue  # drain leftovers after a barge-in without playing them
            try:
                if stream is None or cur_sr != sr:
                    if stream is not None:
                        try: stream.stop(); stream.close()
                        except Exception: pass
                    stream = sd.OutputStream(samplerate=sr, channels=1, dtype="int16")
                    stream.start()
                    cur_sr = sr
                if not playing:
                    playing = True
                    self.mark_start()
                _emit_levels(arr, sr)
                # Small frames, so a barge-in stops within ~0.1s instead of after the sentence.
                view = arr.reshape(-1, 1)
                for off in range(0, len(view), FRAME):
                    if self.abort.is_set():
                        try: stream.abort()
                        except Exception: pass
                        stream, cur_sr = None, None
                        if playing:
                            playing = False
                            self.mark_end()
                        break
                    stream.write(view[off:off + FRAME])
            except Exception:
                try:
                    if stream is not None: stream.close()
                except Exception: pass
                stream, cur_sr = None, None

    def _play_remote(self) -> None:
        playing = False
        start = 0.0          # when the far end began playing this stretch
        sent = 0.0           # seconds of audio handed to it since then
        while True:
            try:
                arr, sr = self.audio_q.get(timeout=0.1)
            except _queue.Empty:
                # Speaking lasts until the far end has had time to play what it was sent.
                if playing and time.time() >= start + sent:
                    playing = False
                    self.mark_end()
                continue
            if self.abort.is_set():
                continue
            if not playing:
                playing, start, sent = True, time.time(), 0.0
                self.mark_start()
            step = max(1, int(sr * self.FRAME_S))
            for off in range(0, len(arr), step):
                if self.abort.is_set():
                    break
                ahead = (start + sent) - time.time()
                if ahead > self.lead:
                    time.sleep(ahead - self.lead)
                if self.abort.is_set():
                    break
                frame = arr[off:off + step]
                try:
                    self.sink(frame, sr)
                except Exception as e:
                    note_speak_error("sending audio", e)
                    break
                sent += len(frame) / float(sr)
            if self.abort.is_set() and playing:
                playing = False
                self.mark_end()


# This PC's own speakers: everything that speaks here without saying otherwise.
DEFAULT = Speaker()
_abort = DEFAULT.abort


def speak(text: str, blocking: bool = False,
          voice: Optional[str] = None,
          speed: Optional[float] = None) -> str:
    """Return: 'ok' | 'no_voice' | 'empty'. blocking=False runs in a daemon
    thread so the chat keeps flowing. Plays on this PC's speakers."""
    return DEFAULT.speak(text, blocking=blocking, voice=voice, speed=speed)


def _speak_legacy(text, blocking, voice, speed) -> str:
    """Pre-pipeline path (HEARTH_TTS_GAPLESS=0): synth+play one chunk at a time."""
    def _run():
        if _abort.is_set():
            return
        _mark_speaking_start()
        try:
            with _lock:
                engine = _load_engine()
                if engine is None:
                    return
                kind, obj = engine
                try:
                    for chunk in _chunk_sentences(text):
                        if _abort.is_set():
                            return
                        if kind == "kokoro":
                            samples, sr = _kokoro_say(obj, chunk, voice, speed)
                            _play(samples, sr)
                        elif kind == "piper":
                            buf = BytesIO()
                            with wave.open(buf, "wb") as wav:
                                obj.synthesize(chunk, wav)  # type: ignore[attr-defined]
                            buf.seek(0)
                            with wave.open(buf, "rb") as wav:
                                sr = wav.getframerate()
                                frames = wav.readframes(wav.getnframes())
                            _play(frames, sr)
                except Exception as e:
                    note_speak_error("synthesis", e)
                    return
        finally:
            _mark_speaking_end()

    if blocking:
        _run()
        return "ok"
    threading.Thread(target=_run, daemon=True).start()
    return "ok"


def status() -> dict:
    have_kokoro = False
    have_piper = False
    have_sd = False
    try:
        import kokoro_onnx  # type: ignore  # noqa: F401
        have_kokoro = True
    except ImportError:
        pass
    try:
        import piper  # type: ignore  # noqa: F401
        have_piper = True
    except ImportError:
        pass
    try:
        import sounddevice  # type: ignore  # noqa: F401
        have_sd = True
    except (ImportError, OSError):
        pass
    engine = _load_engine()
    files = sorted(os.listdir(VOICES_DIR)) if os.path.isdir(VOICES_DIR) else []
    return {
        "voices_dir": VOICES_DIR,
        "voices_dir_files": files,
        "engine": engine[0] if engine else None,
        "kokoro_installed": have_kokoro,
        "piper_installed": have_piper,
        "sounddevice_installed": have_sd,
        "ready": engine is not None and have_sd,
        # Split "weights not fetched yet, offer a download" from "package
        # missing, nothing to offer". Without this the GUI showed one dead end
        # for both and the Lite build had no way to get voice at all.
        "models_present": models_present(),
        "can_install_models": can_install_models(),
        "default_voice": current_voice(),
        "default_speed": current_speed(),
        "last_load_error": _last_load_error,
        # Soft warning — engine loaded but with a downgrade (e.g. CUDA->CPU
        # fallback). GUI should render this as a yellow toast w/ install hint
        # so the user doesn't think TTS is silently broken when speak() works
        # but their picked device wasn't honored.
        "last_load_warning": _last_load_warning,
        # What went wrong behind the queue, if anything, and when.
        "last_speak_error": _last_speak_error,
        "last_speak_error_at": _last_speak_error_at,
        # ONNX providers actually available on this machine — so the GUI can
        # disable picks that need a wheel the user hasn't installed yet, and
        # render a download icon next to them.
        "onnx_providers_available": (lambda: __import__("onnxruntime").get_available_providers() if have_kokoro else [])(),
    }


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(status(), indent=2))
    if "--say" in sys.argv:
        i = sys.argv.index("--say")
        if i + 1 < len(sys.argv):
            print(speak(sys.argv[i + 1], blocking=True))
