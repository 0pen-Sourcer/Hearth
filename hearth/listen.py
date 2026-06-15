"""J.A.R.V.I.S. voice input (STT).

Built on faster-whisper — CTranslate2 backend, real-time on CPU, ~150MB
for the base.en model (auto-downloaded on first use via HuggingFace).

One-shot mode:
    text = listen.listen_once()        # record until silence, transcribe

Continuous mode:
    listen.start_continuous(callback)  # background thread
    listen.stop_continuous()

Continuous mode is interrupt-aware:
    - While Jarvis is speaking (voice.is_speaking()), the listener watches
      mic RMS. If sustained speech is detected → voice.stop() is called to
      mute TTS, then we record/transcribe the new input.
    - Between turns, listener waits for any speech then records.

The first call lazily loads the model. Module is import-safe even when
faster-whisper isn't installed — is_available() returns False.

ONE-TIME SETUP:
    pip install faster-whisper sounddevice numpy
"""

from __future__ import annotations

import os
import re
import time
import threading
from queue import Queue, Empty
from typing import Optional, Callable

# Env-var-based suppression of huggingface_hub warnings lives in hearth/__init__.py
# (must run before any HF import, which means before listen.py is imported).
# Kept here as a no-op fallback in case someone imports listen.py directly.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from .tools import WORKSPACE
from . import voice as _voice

VOICES_DIR = os.path.join(WORKSPACE, "voices")
# faster-whisper model size: base.en (~150MB) is the sweet spot for speed/quality
# on CPU. Override via env if user has a beefier setup.
MODEL_SIZE = os.environ.get("JARVIS_STT_MODEL", "base.en")
COMPUTE_TYPE = os.environ.get("JARVIS_STT_COMPUTE", "int8")  # CPU-friendly
DEVICE = os.environ.get("JARVIS_STT_DEVICE", "cpu")  # "cuda" if you want GPU
SAMPLE_RATE = 16000  # Whisper input rate

# Tunable thresholds — RMS-based VAD. Works without webrtcvad.
SPEECH_RMS = float(os.environ.get("JARVIS_STT_THRESHOLD", "0.012"))
SILENCE_TAIL_S = float(os.environ.get("JARVIS_STT_SILENCE_TAIL", "1.2"))
MAX_UTTERANCE_S = float(os.environ.get("JARVIS_STT_MAX_UTTERANCE", "30"))
MIN_SPEECH_S = 0.4  # ignore very short blips

# Optional wake-word filter for continuous mode. If set, `/listen on` only
# triggers a turn when the user opens with this phrase. Empty = no filter
# (current behavior — every speech burst goes through).
#   JARVIS_WAKE_WORD="jarvis"
#   JARVIS_WAKE_WORD="hey jarvis"
WAKE_WORD = os.environ.get("JARVIS_WAKE_WORD", "").strip().lower()


_PUNCT_STRIP = re.compile(r"^[^\w]+|[^\w]+$")


def _strip_wake_word(text: str, wake: str) -> Optional[str]:
    """If `text` STARTS with the wake word (position 0, punctuation-tolerant),
    return the text with the wake-word prefix removed. Otherwise return None
    (caller should drop the utterance — not addressed to Jarvis).

    Position-0 matching only: 'the jarvis system is cool' does NOT match
    wake='jarvis', because Jarvis is being talked about, not addressed."""
    raw_tokens = text.split()
    tokens = [_PUNCT_STRIP.sub("", t).lower() for t in raw_tokens]
    wake_tokens = wake.lower().split()
    n = len(wake_tokens)
    if n == 0 or len(tokens) < n:
        return None
    if tokens[:n] != wake_tokens:
        return None
    return " ".join(raw_tokens[n:]).strip(",.!?:; ").strip()

_model = None  # faster_whisper.WhisperModel
_load_lock = threading.Lock()
_last_load_error: Optional[str] = None

_listening = False
_listener_thread: Optional[threading.Thread] = None
_text_queue: "Queue[str]" = Queue()


def set_model(size: str) -> str:
    """Switch the STT (Whisper) model at runtime. Resets the cached model so
    the new one loads on the next /listen. Common sizes: tiny(.en), base(.en),
    small(.en), medium(.en), large-v3, distil-small.en. Returns a status line."""
    global MODEL_SIZE, _model
    size = (size or "").strip()
    if not size:
        return f"STT model is '{MODEL_SIZE}'. Set one: tiny.en / base.en / small.en / medium.en / large-v3 (bigger = slower + more accurate; downloads on next /listen)."
    with _load_lock:
        MODEL_SIZE = size
        _model = None  # force reload with the new size on next use
    return f"STT model -> '{size}'. It'll download (if new) on your next /listen."


def set_device(device: str) -> str:
    """Switch the STT device (cpu / cuda) at runtime. Resets the cached model
    so the next transcribe rebuilds on the new device. Called by the GUI when
    the user flips the 'Where to run voice' dropdown — without this, the
    setting flips in the env but the in-process WhisperModel stays pinned
    to whatever device it loaded on at import."""
    global DEVICE, _model
    device = (device or "cpu").strip().lower()
    if device not in ("cpu", "cuda"):
        return f"STT device must be 'cpu' or 'cuda' (got {device!r})"
    with _load_lock:
        DEVICE = device
        os.environ["JARVIS_STT_DEVICE"] = device
        _model = None
    return f"STT device -> {device}"


def cuda_available() -> bool:
    """True only if faster-whisper can ACTUALLY run on GPU right now — i.e. the
    GPU build of ctranslate2 is installed AND a CUDA device is visible. A plain
    CPU ctranslate2 wheel reports 0 devices even on a machine with an RTX card,
    so this reflects 'usable today', not 'has a GPU'."""
    try:
        import ctranslate2  # type: ignore
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _try_load_model():
    global _model, _last_load_error, DEVICE, COMPUTE_TYPE
    if _model is not None:
        return _model
    with _load_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            _last_load_error = f"faster-whisper not installed: {e}"
            return None
        # Store models inside the workspace so the user's HF cache stays out of
        # their home dir clutter. Falls back to default cache.
        download_root = os.path.join(VOICES_DIR, "whisper")
        os.makedirs(download_root, exist_ok=True)
        # Device pick: an explicit JARVIS_STT_DEVICE wins; otherwise auto-detect
        # CUDA so a GPU box isn't silently stuck on slow CPU whisper (the ~10s
        # latency). GPU → float16 (fast); CPU → int8 (CPU-friendly).
        _explicit = os.environ.get("JARVIS_STT_DEVICE")
        if _explicit:
            _device_now = _explicit.strip().lower()
        elif cuda_available():
            _device_now = "cuda"
        else:
            _device_now = "cpu"
        _compute = "float16" if _device_now == "cuda" else COMPUTE_TYPE
        try:
            _model = WhisperModel(MODEL_SIZE, device=_device_now,
                                  compute_type=_compute, download_root=download_root)
            DEVICE, COMPUTE_TYPE = _device_now, _compute
            _last_load_error = None
        except Exception as e:
            # GPU load can fail mid-flight (missing cuDNN, VRAM pressure). Fall
            # back to CPU rather than leaving voice dead.
            if _device_now == "cuda":
                try:
                    _model = WhisperModel(MODEL_SIZE, device="cpu",
                                          compute_type="int8", download_root=download_root)
                    DEVICE, COMPUTE_TYPE = "cpu", "int8"
                    _last_load_error = (f"GPU whisper failed ({type(e).__name__}); "
                                        f"fell back to CPU.")
                    return _model
                except Exception as e2:
                    e = e2
            _last_load_error = f"whisper load failed: {type(e).__name__}: {e}"
            return None
    return _model


def is_available() -> bool:
    try:
        import faster_whisper  # type: ignore  # noqa: F401
        import sounddevice    # type: ignore  # noqa: F401
        import numpy          # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> dict:
    have_fw = False
    have_sd = False
    have_np = False
    try:
        import faster_whisper  # type: ignore  # noqa: F401
        have_fw = True
    except ImportError:
        pass
    try:
        import sounddevice  # type: ignore  # noqa: F401
        have_sd = True
    except ImportError:
        pass
    try:
        import numpy  # type: ignore  # noqa: F401
        have_np = True
    except ImportError:
        pass
    return {
        "model_size": MODEL_SIZE,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "cuda_available": cuda_available(),
        "faster_whisper_installed": have_fw,
        "sounddevice_installed": have_sd,
        "numpy_installed": have_np,
        "model_loaded": _model is not None,
        "listening": _listening,
        "ready": have_fw and have_sd and have_np,
        "last_load_error": _last_load_error,
    }


def _record_until_silence(max_seconds: float = MAX_UTTERANCE_S):
    """Block-record audio from default mic until SILENCE_TAIL_S of quiet
    follows at least MIN_SPEECH_S of speech. Returns float32 mono numpy
    array at SAMPLE_RATE, or None on error / no-speech-detected."""
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np         # type: ignore
    except ImportError:
        return None

    block_s = 0.05  # 50ms blocks
    block_n = int(SAMPLE_RATE * block_s)
    buf: list = []
    speech_since = -1.0
    last_speech = -1.0
    t0 = time.time()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=block_n) as stream:
        while time.time() - t0 < max_seconds:
            data, _overflow = stream.read(block_n)
            mono = data[:, 0] if data.ndim > 1 else data
            buf.append(mono.copy())
            rms = float((mono ** 2).mean() ** 0.5)
            now = time.time()
            if rms > SPEECH_RMS:
                if speech_since < 0:
                    speech_since = now
                last_speech = now
            else:
                if speech_since > 0 and (now - last_speech) > SILENCE_TAIL_S:
                    # Silence after some speech — done
                    break

    if speech_since < 0 or (last_speech - speech_since) < MIN_SPEECH_S:
        return None
    audio = np.concatenate(buf)
    return audio


def transcribe(audio) -> str:
    """Run the model on a float32 numpy audio array at SAMPLE_RATE.
    Returns the transcribed text (best segment). Empty string on failure."""
    model = _try_load_model()
    if model is None:
        return ""
    try:
        segments, _info = model.transcribe(
            audio,
            language="en",
            beam_size=1,            # fast - boost to 5 for accuracy
            # Silero VAD (ships with faster-whisper, no extra dep) drops
            # speech-free chunks AND tightens utterance edges - much smarter
            # than our RMS-based pre-trim. Critical for the noisy-room case
            # where RMS thresholds fail. Enabled with conservative params so
            # short conversational pauses don't fragment the transcript.
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 400,
                "speech_pad_ms":           250,
            },
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception:
        return ""


def listen_once(max_seconds: float = MAX_UTTERANCE_S) -> str:
    """Block until the user speaks + falls silent, then transcribe.
    Returns the transcript or '' if nothing was heard / model unavailable."""
    if not is_available():
        return ""
    audio = _record_until_silence(max_seconds=max_seconds)
    if audio is None:
        return ""
    return transcribe(audio)


def _continuous_loop(on_utterance: Callable[[str], None]):
    """Background thread for continuous listening with TTS-interrupt.

    Behavior:
      - While TTS is playing, watch mic. If sustained speech appears,
        stop TTS and pivot into a full record-until-silence cycle.
      - When TTS isn't playing, just wait for a speech burst then record.
    """
    global _listening
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np         # type: ignore
    except ImportError:
        _listening = False
        return

    block_s = 0.05
    block_n = int(SAMPLE_RATE * block_s)
    speech_blocks_needed = 4  # ~200ms of speech before we decide it's real
    speech_run = 0

    # While TTS is playing, raise the trigger threshold so the mic doesn't
    # pick up speaker echo and feedback-loop on Jarvis's own voice. Tunable
    # via env var; default 5x makes normal-volume TTS not trigger but a
    # human speaking AT or louder than the speakers still interrupts.
    TTS_MULT = float(os.environ.get("JARVIS_STT_TTS_THRESHOLD_MULT", "5.0"))
    # Post-stop debounce: after we kill TTS, the speakers keep playing for
    # ~200-400ms (audio buffer). Wait that out before recording so we don't
    # transcribe Jarvis's own tail.
    POST_STOP_DEBOUNCE_S = float(os.environ.get("JARVIS_STT_POST_STOP_DEBOUNCE", "0.4"))
    # Hard-mute cooldown AFTER tts ends - the speaker buffer can keep playing
    # for ~0.5-1s and we don't want that to feed back as a phantom utterance.
    # The old guard tried to do this by raising the rms threshold, but the
    # is_speaking() check was silently throwing (function didn't exist) so the
    # threshold-raise never fired - hence the loop. With is_speaking() defined
    # now, we ALSO hard-skip transcription for this cooldown to be belt-and-
    # suspenders. Tunable via env.
    POST_TTS_COOLDOWN_S = float(os.environ.get("JARVIS_STT_POST_TTS_COOLDOWN", "1.0"))

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", blocksize=block_n) as stream:
            while _listening:
                try:
                    data, _ = stream.read(block_n)
                except Exception:
                    time.sleep(0.05)
                    continue

                # Hard-mute while Jarvis is speaking AND for a brief cooldown
                # after - kills the speaker-to-mic feedback loop deterministically.
                tts_active = False
                tts_recent = False
                try:
                    tts_active = _voice.is_speaking()
                    tts_recent = _voice.seconds_since_spoke() < POST_TTS_COOLDOWN_S
                except Exception:
                    pass
                if tts_active or tts_recent:
                    speech_run = 0  # reset so a partial run doesn't carry over
                    continue

                mono = data[:, 0] if data.ndim > 1 else data
                rms = float((mono ** 2).mean() ** 0.5)
                threshold = SPEECH_RMS  # tts is gated above; no need to raise here

                if rms > threshold:
                    speech_run += 1
                else:
                    speech_run = 0
                    continue

                if speech_run < speech_blocks_needed:
                    continue

                # We have sustained speech. If TTS is playing, kill it and
                # wait briefly for the speaker audio to die before recording.
                try:
                    if _voice.is_speaking():
                        _voice.stop()
                        time.sleep(POST_STOP_DEBOUNCE_S)
                except Exception:
                    pass

                # Now do a full record-until-silence cycle for the rest
                # of the utterance. (We've already captured ~200ms of it,
                # but re-recording is simpler than splicing buffers.)
                speech_run = 0
                audio = _record_until_silence(max_seconds=MAX_UTTERANCE_S)
                if audio is None or len(audio) < SAMPLE_RATE * MIN_SPEECH_S:
                    continue
                text = transcribe(audio)
                if text:
                    if WAKE_WORD:
                        stripped = _strip_wake_word(text, WAKE_WORD)
                        if stripped is None:
                            continue  # speech detected but not addressed to Jarvis
                        if not stripped:
                            continue  # wake word with nothing after — ignore
                        text = stripped
                    try:
                        on_utterance(text)
                    except Exception:
                        pass
    finally:
        _listening = False


def start_continuous(on_utterance: Callable[[str], None]) -> str:
    """Spawn the background listener. Returns a status string."""
    global _listening, _listener_thread
    if _listening:
        return "already listening"
    if not is_available():
        return "Error: faster-whisper or sounddevice not installed"
    # Warm the model up so the first transcription isn't slow
    if _try_load_model() is None:
        return f"Error: {_last_load_error}"
    _listening = True
    _listener_thread = threading.Thread(
        target=_continuous_loop, args=(on_utterance,), daemon=True
    )
    _listener_thread.start()
    return "listening"


def stop_continuous() -> str:
    global _listening
    _listening = False
    return "stopped"


def is_listening() -> bool:
    return _listening


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(status(), indent=2))
    if "--listen" in sys.argv:
        print("listening... say something then pause:")
        text = listen_once()
        print(f"heard: {text!r}")
