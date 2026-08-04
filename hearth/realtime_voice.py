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
_ready_cb: Optional[Callable[[], None]] = None  # fires once the STT model is loaded
# Echo guard: speech must sustain this long before it counts as a barge, so a
# blip of the assistant's own voice bleeding through the speakers (VAD start then a
# quick VAD stop cancels the timer) doesn't self-interrupt. Needed now that a
# barge stops TTS directly server-side. 250ms rejects echo while staying
# responsive; 0 disables it (fine on an echo-cancelled headset).
# How long speech must sustain before it counts as a barge (not a stray echo
# blip of the assistant's own voice). Lower = TTS cuts on your first word instead of
# mid-sentence; higher = safer against speaker->mic bleed. Set to 0 for an
# instant cut (fine on a headset). 150ms is snappy but still eats a single frame.
_barge_guard_ms: int = int(os.environ.get("HEARTH_BARGE_GUARD_MS", "150") or "150")
_barge_timer: Optional[threading.Timer] = None
_barge_grace_until: float = 0.0  # capture user speech (skip echo-tail) until this time
_barge_fired: bool = False  # a real barge fired this segment (vs an echo blip)
_BARGE_DEBUG: bool = os.environ.get("HEARTH_BARGE_DEBUG", "1") not in ("0", "", "false")


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

    def _fire_barge() -> None:
        # Real barge confirmed (speech sustained past the guard). Keep the
        # capture-grace open — never shrink it, the interrupting sentence may
        # still be running — and mark it fired so _on_speech_stop knows this was
        # a true interrupt, not an echo blip to discard.
        global _barge_grace_until, _barge_fired
        _barge_fired = True
        _barge_grace_until = max(_barge_grace_until, time.time() + 8.0)
        if _BARGE_DEBUG:
            print("[barge] FIRING -> stop TTS", flush=True)
        cb = _barge_cb
        if cb is not None:
            try: cb()
            except Exception: pass

    def _on_speech_start() -> None:
        # REAL speech onset. RealtimeSTT fires on_recording_start the instant it
        # begins RECORDING because it heard your voice — that happens WHILE you
        # talk, on the worker thread. (on_vad_detect_start, which this used to
        # hang off, only fires when the recorder ENTERS a listen cycle, before
        # you speak, so mid-TTS it fired too late and the words got dropped.)
        # If TTS is mid-sentence, this is a barge: open the capture-grace NOW so
        # the interrupting words survive the echo-drop in the loop, and arm the
        # guard timer to stop TTS on your first word.
        if not _tts.is_speaking():
            return
        global _barge_timer, _barge_grace_until, _barge_fired
        if _BARGE_DEBUG:
            print("[barge] speech_start over TTS -> arming", flush=True)
        _barge_fired = False
        _barge_grace_until = time.time() + 8.0
        if _barge_guard_ms > 0:
            # Debounce: only barge if speech sustains past the guard window, so a
            # transient echo of Hearth's own voice doesn't self-interrupt.
            _barge_timer = threading.Timer(_barge_guard_ms / 1000.0, _fire_barge)
            _barge_timer.daemon = True
            _barge_timer.start()
        else:
            _fire_barge()

    def _on_speech_stop() -> None:
        # Recording ended. If the guard timer never fired, the speech was shorter
        # than the guard — a blip (cough / echo tick), not an interrupt — so
        # cancel it and close the capture-grace so stray audio can't ride it into
        # a spurious turn. A real barge (guard already fired) keeps the grace open
        # for the rest of the interrupting sentence, still being finalized.
        global _barge_timer, _barge_grace_until
        pending = _barge_timer is not None
        if pending:
            try: _barge_timer.cancel()
            except Exception: pass
            _barge_timer = None
        if pending and not _barge_fired:
            _barge_grace_until = 0.0

    def _on_vad_start() -> None:
        # Recorder entered its listen cycle ("speak now"). This is NOT speech
        # onset — that's _on_speech_start via on_recording_start — it fires at
        # cycle begin, before you talk. Use it only to drive the HUD to
        # "listening" when the assistant isn't the one holding the floor.
        if _BARGE_DEBUG:
            print(f"[barge] listen-cycle speaking={_tts.is_speaking()}", flush=True)
        if not _tts.is_speaking():
            try: _tts.set_voice_state("listening")
            except Exception: pass

    rec = AudioToTextRecorder(
        model=model,
        language=lang,
        # silero VAD (neural). 0.35 is the sensitivity sweet spot - low enough
        # to catch a soft user, high enough to ignore desk-fan rumble.
        silero_sensitivity=0.35,
        silero_use_onnx=True,
        webrtc_sensitivity=2,
        # 0.5s silence after end-of-speech = endpoint. Was 0.3s, which chopped a
        # normal sentence into a fragment per breath; the browser then stitches
        # fragments within a short gap, so this only needs to be long enough that
        # a mid-sentence pause doesn't fire, without feeling laggy.
        post_speech_silence_duration=0.5,
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
        # Barge-in hooks. on_recording_start fires at REAL speech onset (the
        # instant the recorder hears your voice and starts capturing), which is
        # what a barge needs — NOT on_vad_detect_start, which only marks the
        # beginning of a listen cycle before you talk. on_vad_detect_start is
        # kept purely for the "listening" HUD state.
        on_vad_detect_start=_on_vad_start,
        on_recording_start=_on_speech_start,
        on_recording_stop=_on_speech_stop,
        # Quiet.
        spinner=False,
        level=40,
        use_microphone=True,
        # Honor the user's mic pick (same setting as the CLI loop). None lets
        # RealtimeSTT/PortAudio use the OS default.
        input_device_index=_mic_index(),
    )
    return rec


_mic_warning = ""  # surfaced to the UI when a picked mic couldn't be opened


def _mic_index():
    """The user's chosen mic index, but ONLY if it can actually be opened.

    A Bluetooth headset (boAt, AirPods, etc.) enumerates its mic even when its
    HFP capture endpoint won't open, and handing that index to RealtimeSTT makes
    it retry 'Selected device validation failed' forever with no way out. So we
    probe the device first; if it fails we fall back to the OS default and leave
    a message the voice UI can show, instead of a silent dead mic."""
    global _mic_warning
    _mic_warning = ""
    try:
        from .listen import input_device_index
        idx = input_device_index()
    except Exception:
        return None
    if idx is None:
        return None
    try:
        import sounddevice as sd
        # check_input_settings is too optimistic (it passed a Bluetooth HFP mic
        # that RealtimeSTT then couldn't open), so actually START a stream for a
        # moment. If the device can't be opened for capture, this throws here
        # instead of inside RealtimeSTT's forever-retry loop.
        with sd.InputStream(device=idx, channels=1, samplerate=16000,
                            blocksize=1024):
            pass
        return idx
    except Exception as e:
        _mic_warning = ("Your selected mic couldn't be opened (Bluetooth mics "
                        "often can't), so Hearth is using the system default. "
                        "Pick a different mic in Settings > Voice if it can't "
                        "hear you.")
        try:
            print(f"[voice] mic index {idx} failed to open ({e}); using default",
                  flush=True)
        except Exception:
            pass
        return None


def mic_warning() -> str:
    return _mic_warning


def _looks_like_echo(text: str) -> bool:
    """True if `text` is mostly words the assistant just spoke — i.e. the mic picked up
    the assistant's own voice off the speakers rather than a real interrupt. Lets a
    speaker user barge in without the assistant transcribing itself back into an
    endless loop. A headset never trips this: what the user says doesn't match
    what was just spoken, so the overlap stays low."""
    try:
        spoken = _tts.recently_spoke_text()
    except Exception:
        return False
    if not spoken:
        return False
    words = re.findall(r"[a-z0-9']+", text.lower())
    if len(words) < 2:
        return False
    kws = set(re.findall(r"[a-z0-9']+", spoken))
    if not kws:
        return False
    hits = sum(1 for w in words if w in kws)
    return hits / len(words) >= 0.65


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


def set_ready_callback(cb: Optional[Callable[[], None]]) -> None:
    """Register a function to fire once the STT model has finished loading and
    the recorder is actually listening (start_continuous returns before that)."""
    global _ready_cb
    _ready_cb = cb


def _continuous_loop(on_utterance: Callable[[str], None]) -> None:
    """Block until stop_continuous() — feed each finalized utterance to the callback."""
    global _listening, _barge_grace_until
    try:
        rec = _get_recorder()
    except Exception as e:
        print(f"[hearth.realtime_voice] recorder init failed: {e}")
        _listening = False
        return

    _listening = True
    # The recorder is now built (the STT model finished loading above). Signal
    # ready so the HUD leaves the "warming up" state — start_continuous returns
    # before this, so without it the UI shows "listening" while the model is
    # still loading and can't actually hear anything.
    cb = _ready_cb
    if cb is not None:
        try: cb()
        except Exception: pass
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
            # Don't dispatch our own TTS playback as user input. With speaker->mic
            # bleed (common on shared/virtual audio devices like Steam's), the mic
            # transcribes the assistant's own voice and feeds it back as a "user" turn.
            # Drop anything captured while speaking or in the ~1.2s tail after, AND
            # flush the recorder buffer so buffered echo doesn't carry into the
            # next turn (clear_audio_queue is the RealtimeSTT reset for exactly this).
            # During a barge grace the user is deliberately talking over the assistant,
            # so keep every word even though TTS may still be tailing off — this
            # is the interrupting message and it has to reach the LLM. Outside the
            # grace, drop anything captured while speaking or in the ~1.2s echo
            # tail after.
            if time.time() < _barge_grace_until:
                # A barge fired and grabbed this utterance — but on speakers the
                # "barge" can be the assistant's own voice bleeding into the mic. If the
                # words are mostly what the assistant just said, it's echo: drop it so it
                # isn't sent back as a user turn. Real interrupts (different words)
                # pass straight through. Either way, consume the grace.
                _barge_grace_until = 0.0
                if _looks_like_echo(text):
                    try: rec.clear_audio_queue()
                    except Exception: pass
                    continue
            elif _tts.is_speaking() or _tts.seconds_since_spoke() < 1.2:
                try: rec.clear_audio_queue()
                except Exception: pass
                continue
            # Utterance accepted → LLM is about to work: HUD shows "thinking"
            # (brisk pulse) until the first TTS chunk flips it to "speaking".
            try: _tts.set_voice_state("thinking")
            except Exception: pass
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
