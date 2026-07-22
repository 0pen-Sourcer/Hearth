"""J.A.R.V.I.S. voice — local TTS.

Primary engine:  Kokoro (~82M params, ONNX, sounds genuinely human, real-time
                 on CPU, zero VRAM impact — won't fight your 10B LLM for the 5060).
Fallback engine: Piper (smaller, faster, a touch more robotic).

Both are 100% local, free, and offline.

ONE-TIME SETUP (Kokoro path, recommended):
    pip install kokoro-onnx sounddevice numpy

    Then download the model + voices to ~/Jarvis/voices/:
      - kokoro-v1.0.onnx           (~80MB)
      - voices-v1.0.bin            (~20MB)
    From: https://huggingface.co/hexgrad/Kokoro-82M (ONNX exports), or:
        https://github.com/thewh1teagle/kokoro-onnx/releases

FALLBACK (Piper):
    pip install piper-tts sounddevice numpy
    Drop any voice .onnx + .json into ~/Jarvis/voices/
    From: https://huggingface.co/rhasspy/piper-voices

Voice picks for Jarvis vibes:
  Kokoro: am_adam (US male), am_michael, bm_george (UK male), bf_emma
  Piper:  en_GB-alan-medium, en_US-ryan-high

Module is import-safe even with no deps installed — speak() returns 'no_voice'
and jarvis.py keeps working silently.
"""

from __future__ import annotations

import os
import threading
import time
import wave
from io import BytesIO
from typing import Callable, Optional, Tuple

from .tools import WORKSPACE

VOICES_DIR = os.path.join(WORKSPACE, "voices")
os.makedirs(VOICES_DIR, exist_ok=True)

# Default voice id for Kokoro — am_michael is calm and Jarvis-leaning.
DEFAULT_KOKORO_VOICE = os.environ.get("JARVIS_VOICE", "am_michael")
# 1.0 = Kokoro's natural rate. Voiceover testing kept this at 1.0 (anything
# higher starts to sound rushed during long answers). Override per session
# with /voice speed <n> or set JARVIS_VOICE_SPEED before launch.
DEFAULT_SPEED = float(os.environ.get("JARVIS_VOICE_SPEED", "1.0"))

_engine: Optional[Tuple[str, object]] = None  # ("kokoro" | "piper", obj)
_lock = threading.Lock()
# Set when caller wants the in-flight speech aborted (new turn arrived).
_abort = threading.Event()

# TTS playback state - so listen.py can mute the mic while Jarvis speaks
# and avoid feedback-looping on its own voice through the speakers. Use a
# COUNTER (not a bool) so streaming sentence-by-sentence play stays True for
# the entire speak() call across multiple _play() chunks.
_speaking_count = 0
_last_spoke_at = 0.0
_speaking_state_lock = threading.Lock()


def is_speaking() -> bool:
    """True while a speak() call is producing audio. listen.py gates STT on
    this so the mic doesn't transcribe Jarvis's own voice through the speakers."""
    return _speaking_count > 0


def seconds_since_spoke() -> float:
    """Seconds since the last TTS chunk finished playing; inf if never spoken.
    Used to extend the post-TTS cooldown so the speaker buffer tail doesn't
    trigger a phantom utterance."""
    if _last_spoke_at == 0.0:
        return float("inf")
    return max(0.0, time.time() - _last_spoke_at)


def _mark_speaking_start() -> None:
    global _speaking_count
    with _speaking_state_lock:
        _speaking_count += 1
    set_voice_state("speaking")


def _mark_speaking_end() -> None:
    global _speaking_count, _last_spoke_at
    with _speaking_state_lock:
        _speaking_count = max(0, _speaking_count - 1)
        _last_spoke_at = time.time()
        _still = _speaking_count > 0
    # Only fall back to idle when the LAST overlapping utterance finished.
    if not _still:
        set_voice_state("idle")
# Last HARD load error — surfaced in status() so we don't silently report
# "engine: null" when the actual issue is, e.g., a missing file format.
_last_load_error: Optional[str] = None
# Last SOFT degradation warning — engine still loaded fine but with a
# downgrade the user should know about (e.g. CUDA requested but onnxruntime-gpu
# isn't installed → fell back to CPU). GUI can render this as a yellow toast
# with an "install" hint, without making it look like TTS is broken.
_last_load_warning: Optional[str] = None


def _find(*candidates: str) -> Optional[str]:
    for fn in os.listdir(VOICES_DIR) if os.path.isdir(VOICES_DIR) else []:
        for cand in candidates:
            if cand in fn.lower():
                return os.path.join(VOICES_DIR, fn)
    return None


# Same release the installer pulls from. Auto-fetched on first TTS use if the
# files aren't already on disk (set JARVIS_NO_AUTODOWNLOAD=1 to opt out).
KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/latest/download/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/latest/download/voices-v1.0.bin"


def _download(url: str, dest: str) -> None:
    """Fetch url -> dest atomically via a .part temp, with a progress line."""
    import urllib.request
    tmp = dest + ".part"

    def _hook(blocks, bs, total):
        if total > 0:
            pct = min(100, blocks * bs * 100 // total)
            print(f"\r   {os.path.basename(dest)}: {pct}%", end="", flush=True)

    try:
        urllib.request.urlretrieve(url, tmp, _hook)
        print()
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


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


def _ensure_kokoro_models() -> bool:
    """If the Kokoro model + voices aren't on disk, download them once.
    Returns True when both are present. Honors JARVIS_NO_AUTODOWNLOAD=1, and on a
    LITE build never silently pulls ~100MB — the user enables voice explicitly
    (GUI Downloads pane) so a slim/API install stays slim."""
    global _last_load_error
    if _find("kokoro") and _find("voices"):
        return True
    if os.environ.get("JARVIS_NO_AUTODOWNLOAD") == "1":
        _last_load_error = "kokoro models missing and auto-download disabled (JARVIS_NO_AUTODOWNLOAD=1)"
        return False
    if _is_lite_build():
        _last_load_error = ("voice models not bundled in the Lite build — turn on "
                            "voice in Settings → Voice to download them (~100MB).")
        return False
    os.makedirs(VOICES_DIR, exist_ok=True)
    try:
        if not _find("kokoro"):
            print("[voice] Kokoro TTS model missing - downloading once (~80MB)...")
            _download(KOKORO_MODEL_URL, os.path.join(VOICES_DIR, "kokoro-v1.0.onnx"))
        if not _find("voices"):
            print("[voice] Kokoro voices missing - downloading once (~20MB)...")
            _download(KOKORO_VOICES_URL, os.path.join(VOICES_DIR, "voices-v1.0.bin"))
    except Exception as e:
        _last_load_error = f"Kokoro auto-download failed: {type(e).__name__}: {e}"
        return False
    return bool(_find("kokoro") and _find("voices"))


def _try_kokoro():
    global _last_load_error
    if not _ensure_kokoro_models():
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
    tts_device = os.environ.get("JARVIS_TTS_DEVICE", "cpu").lower()
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
            return Kokoro(model, voices, providers=providers)  # type: ignore[call-arg]
        except TypeError:
            # Fallback: set provider preference via env var for onnxruntime
            os.environ["ORT_PROVIDERS"] = ",".join(providers)
            return Kokoro(model, voices)
    except Exception as e:
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


def _load_engine() -> Optional[Tuple[str, object]]:
    global _engine, _last_load_error, _last_load_warning
    if _engine is not None:
        return _engine
    _last_load_error = None
    # NOTE: don't reset _last_load_warning here — _try_kokoro may set it
    # mid-call to flag a soft degradation (e.g. CUDA->CPU fallback). We
    # only clear it on a successful engine load WITHOUT a warning being set.
    pre_warning = _last_load_warning
    _last_load_warning = None
    k = _try_kokoro()
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
    into the voices/ directory without restarting Jarvis."""
    global _engine, _last_load_error
    _engine = None
    _last_load_error = None


def set_default_voice(name: str) -> None:
    """Change the default voice id used for subsequent speak() calls."""
    global DEFAULT_KOKORO_VOICE
    DEFAULT_KOKORO_VOICE = (name or "").strip() or DEFAULT_KOKORO_VOICE


def set_speed(speed: float) -> float:
    """Change the default TTS playback rate for subsequent speak() calls.
    Clamped to a sane 0.5x-2.5x range. Returns the value actually set."""
    global DEFAULT_SPEED
    try:
        s = float(speed)
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    DEFAULT_SPEED = max(0.5, min(2.5, s))
    return DEFAULT_SPEED


# Built-in Kokoro voice catalog. Lets the model offer real options when the
# user wants to try something else.
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
]


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
    except ImportError:
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


_SENTENCE_END = ("."  , "!", "?", "\n")


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
    _abort.set()
    try:
        import sounddevice as sd  # type: ignore
        sd.stop()
    except Exception:
        pass


def reset_abort() -> None:
    """Clear the abort flag so the NEXT user turn's TTS can play. Called at the
    start of a fresh recording cycle (CLI mic toggle / GUI voiceCycle listening
    phase). Without this, the flag set by stop() would persist forever and
    silence the assistant on every subsequent turn."""
    _abort.clear()


def speak(text: str, blocking: bool = False,
          voice: Optional[str] = None,
          speed: Optional[float] = None) -> str:
    """Return: 'ok' | 'no_voice' | 'empty'. blocking=False runs in a daemon
    thread so the chat keeps flowing.

    Streams sentence-by-sentence so audio starts ~1 sentence after the
    text appears, instead of waiting for the whole reply to be synthesized."""
    text = _clean_for_tts(text or "")
    if not text:
        return "empty"
    if not is_available():
        return "no_voice"
    speed = speed if speed is not None else DEFAULT_SPEED

    def _run():
        # DO NOT clear _abort here. Multiple speak() calls fire in succession
        # during a streaming assistant turn (one per sentence chunk). If each
        # _run() clears the abort flag, then a barge-in mid-stream only kills
        # the CURRENT chunk - the next queued chunk clears the flag and plays
        # anyway, breaking barge-in. The flag is cleared explicitly by
        # reset_abort() at the start of a fresh turn (CLI mic toggle / GUI
        # voiceCycle entering 'listening' state).
        if _abort.is_set():
            return  # queued speak() arrived after a stop() - drop it
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
                            samples, sr = obj.create(  # type: ignore[attr-defined]
                                chunk,
                                voice=voice or DEFAULT_KOKORO_VOICE,
                                speed=speed,
                                lang="en-us",
                            )
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
                except Exception:
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
    except ImportError:
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
        "default_voice": DEFAULT_KOKORO_VOICE,
        "default_speed": DEFAULT_SPEED,
        "last_load_error": _last_load_error,
        # Soft warning — engine loaded but with a downgrade (e.g. CUDA->CPU
        # fallback). GUI should render this as a yellow toast w/ install hint
        # so the user doesn't think TTS is silently broken when speak() works
        # but their picked device wasn't honored.
        "last_load_warning": _last_load_warning,
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
