"""Real-time voice loop — silero VAD + streaming faster-whisper + Kokoro TTS.

The big upgrade over `hearth.listen` (energy-gated, blocks until silence):

  - **silero VAD** (neural, ~1MB) endpoints way faster than energy thresholding,
    handles room noise and music in the background without false triggers.
  - **Streaming transcription**: faster-whisper runs on rolling 200ms windows
    while you're still talking, so we can show live captions and start the
    LLM the instant you finish — no "press release" or fixed-silence wait.
  - **Sentence-streamed TTS**: as the LLM yields tokens we accumulate to
    sentence boundaries and dispatch each to Kokoro immediately, so the user
    hears the first sentence while the model is still generating the rest.

Import is safe even without RealtimeSTT installed — `is_available()` returns
False and the rest of Hearth keeps working with the legacy `hearth.listen`
fallback.

Environment:
  HEARTH_REALTIME_VOICE=1   force-enable (default: auto if RealtimeSTT importable)
  HEARTH_REALTIME_MODEL     faster-whisper size (default: tiny.en for speed)
  HEARTH_REALTIME_LANG      language hint (default: en)
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, Optional

from . import voice as _tts

_REALTIME_AVAILABLE: Optional[bool] = None
_recorder = None
_recorder_lock = threading.Lock()
_listening = False
_listen_thread: Optional[threading.Thread] = None
_stop_flag = threading.Event()

# Caption callback — UI sets this to get live partial transcripts.
_caption_cb: Optional[Callable[[str], None]] = None
# Barge-in callback — UI sets this to be notified the instant the user
# starts speaking (silero VAD start). Lets the GUI kill TTS + abort the
# LLM stream so a phone-call-like interrupt works.
_barge_cb: Optional[Callable[[], None]] = None


def is_available() -> bool:
    """True if RealtimeSTT + silero-vad can be imported. Cached after first probe."""
    global _REALTIME_AVAILABLE
    if _REALTIME_AVAILABLE is not None:
        return _REALTIME_AVAILABLE
    try:
        import RealtimeSTT  # noqa: F401
        _REALTIME_AVAILABLE = True
    except Exception:
        _REALTIME_AVAILABLE = False
    return _REALTIME_AVAILABLE


def status() -> dict:
    return {
        "available": is_available(),
        "listening": _listening,
        "model": os.environ.get("HEARTH_REALTIME_MODEL", "tiny.en"),
        "engine": "RealtimeSTT (silero VAD + faster-whisper, streaming)",
    }


def _build_recorder():
    """Lazily build the AudioToTextRecorder with ChatGPT-voice-mode tuning.

    Endpoint detection at 0.3s (snappy), live partials every 100ms (visible
    feedback while user is mid-sentence), silero VAD threshold tight enough
    to ignore ambient noise but not so tight that a soft-spoken user gets
    cut off. Mic stays open continuously — when TTS plays, partials are
    suppressed at the callback layer instead of pausing the recorder
    (a paused recorder takes ~200ms to spin back up).
    """
    # Pre-trust the silero VAD repo so torch.hub doesn't ask
    # "trust this repo? (y/N)" on the console at startup and block
    # voice mode forever (CLI question never gets an answer in a
    # GUI-spawned subprocess). Idempotent — sets a flag in
    # ~/.cache/torch/hub/trusted_list.
    try:
        import torch  # type: ignore
        torch.hub.set_dir(os.path.expanduser(os.environ.get(
            "TORCH_HOME", "~/.cache/torch")) + "/hub")
        try:
            torch.hub._validate_not_a_forked_repo = lambda *a, **k: True  # silent override
        except Exception:
            pass
        try:
            torch.hub.load("snakers4/silero-vad", "silero_vad",
                            trust_repo=True, force_reload=False, verbose=False)
        except Exception:
            pass  # repo cached / offline / non-fatal
    except ImportError:
        pass

    from RealtimeSTT import AudioToTextRecorder

    model = os.environ.get("HEARTH_REALTIME_MODEL", "tiny.en")
    lang = os.environ.get("HEARTH_REALTIME_LANG", "en")

    def _on_realtime_update(text: str) -> None:
        # Live partial transcript while user speaks. Drop captions arriving
        # mid-TTS so we never caption the assistant's own voice through the
        # speakers — they're noise, not signal.
        if _tts.is_speaking():
            return
        cb = _caption_cb
        if cb is not None and text:
            try:
                cb(text)
            except Exception:
                pass

    def _on_vad_start() -> None:
        # User just started speaking. If TTS is mid-sentence, that's a
        # barge-in — fire it immediately so the assistant shuts up + the
        # in-flight LLM call gets aborted.
        if _tts.is_speaking():
            cb = _barge_cb
            if cb is not None:
                try: cb()
                except Exception: pass

    rec = AudioToTextRecorder(
        model=model,
        language=lang,
        # silero VAD (neural). 0.35 is the sensitivity sweet spot - low enough
        # to catch a soft user, high enough to ignore desk-fan rumble.
        silero_sensitivity=0.35,
        silero_use_onnx=True,
        webrtc_sensitivity=2,
        # SNAP. 0.3s silence after end-of-speech = endpoint. ChatGPT feels
        # this fast because it's around the same value.
        post_speech_silence_duration=0.3,
        # Neural end-of-speech detection — webrtc misses silence on a noisy mic,
        # so rec.text() never endpoints.
        silero_deactivity_detection=True,
        min_length_of_recording=0.25,
        min_gap_between_recordings=0.05,
        # Live partials every 100ms - what makes the caption stream feel
        # instant. Don't go lower; the model runs out of audio to chew.
        enable_realtime_transcription=True,
        realtime_processing_pause=0.1,
        realtime_model_type=model,
        on_realtime_transcription_update=_on_realtime_update,
        # Barge-in: VAD says user started speaking → kill TTS NOW.
        on_vad_detect_start=_on_vad_start,
        # Quiet.
        spinner=False,
        level=40,
        use_microphone=True,
        # Honor the user's mic pick (same setting as the CLI loop). None lets
        # RealtimeSTT/PortAudio use the OS default.
        input_device_index=_mic_index(),
    )
    return rec


def _mic_index():
    try:
        from .listen import input_device_index
        return input_device_index()
    except Exception:
        return None


def reset_recorder():
    """Drop the cached recorder so the next voice session rebuilds it — e.g.
    after the user picks a different mic. A live session keeps its recorder
    until it ends; the change lands on the next start."""
    global _recorder
    with _recorder_lock:
        _recorder = None


def _get_recorder():
    global _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = _build_recorder()
        return _recorder


def set_caption_callback(cb: Optional[Callable[[str], None]]) -> None:
    """Register a function to receive live partial transcripts.
    Called frequently (~10/sec) while the user is speaking."""
    global _caption_cb
    _caption_cb = cb


def set_barge_callback(cb: Optional[Callable[[], None]]) -> None:
    """Register a function to fire the instant silero VAD says user is
    speaking. Used by the GUI to kill TTS + abort the LLM stream for
    snappy, phone-call-like barge-in."""
    global _barge_cb
    _barge_cb = cb


def _continuous_loop(on_utterance: Callable[[str], None]) -> None:
    """Block until stop_continuous() — feed each finalized utterance to the callback."""
    global _listening
    try:
        rec = _get_recorder()
    except Exception as e:
        print(f"[hearth.realtime_voice] recorder init failed: {e}")
        _listening = False
        return

    _listening = True
    try:
        while not _stop_flag.is_set():
            try:
                # Block until silero detects an endpoint and faster-whisper
                # returns the finalized text. ~200-400ms after you stop talking.
                text = rec.text()
                # Diagnostic: proves whether VAD is actually endpointing (a final
                # utterance) vs blocking forever. If you SEE this line in the
                # terminal after you stop talking, the final fired.
                print(f"[hearth.realtime_voice] FINAL utterance -> {text!r}", flush=True)
            except Exception as e:
                print(f"[hearth.realtime_voice] rec.text() error: {e}", flush=True)
                time.sleep(0.2)
                continue
            if _stop_flag.is_set():
                break
            if not text:
                continue
            text = text.strip()
            if not text:
                continue
            # Don't dispatch our own TTS playback as user input
            if _tts.is_speaking() or _tts.seconds_since_spoke() < 0.5:
                continue
            try:
                on_utterance(text)
            except Exception as e:
                print(f"[hearth.realtime_voice] on_utterance error: {e}")
    finally:
        _listening = False


def start_continuous(on_utterance: Callable[[str], None]) -> str:
    """Begin continuous listening. Idempotent — second call is a no-op."""
    global _listen_thread
    if not is_available():
        return "RealtimeSTT not installed — falling back to hearth.listen"
    if _listening:
        return "Realtime voice already listening"
    _stop_flag.clear()
    _listen_thread = threading.Thread(
        target=_continuous_loop, args=(on_utterance,), daemon=True,
    )
    _listen_thread.start()
    return "Realtime voice listening"


def stop_continuous() -> str:
    """Halt the listening loop. Safe to call when not running."""
    global _recorder
    _stop_flag.set()
    if _recorder is not None:
        try:
            _recorder.stop()
        except Exception:
            pass
    return "Realtime voice stopped"


def is_listening() -> bool:
    return _listening


# ----------------------------------------------------------------------------
# Sentence-streaming TTS helper
# ----------------------------------------------------------------------------

_SENT_END = re.compile(r'([.!?]+["\'\)\]]*|\n\n)\s')


def stream_speak(text_chunks):
    """Take an iterable of text chunks (e.g. LLM streaming deltas) and dispatch
    each completed sentence to TTS as soon as it's available — so the user
    hears the first sentence while the model is still generating the rest.

    Returns the final concatenated text.
    """
    buf = ""
    final = ""
    for chunk in text_chunks:
        if not chunk:
            continue
        buf += chunk
        final += chunk
        # Look for sentence boundaries in the buffer.
        while True:
            m = _SENT_END.search(buf)
            if not m:
                break
            end = m.end()
            sentence = buf[:end].strip()
            buf = buf[end:]
            if sentence:
                _tts.speak(sentence, blocking=False)
    # Flush whatever's left.
    tail = buf.strip()
    if tail:
        _tts.speak(tail, blocking=False)
    return final
