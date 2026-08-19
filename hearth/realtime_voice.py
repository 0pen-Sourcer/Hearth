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
_error_cb: Optional[Callable[[str], None]] = None  # fires if the recorder can't build
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

# Speaker-safe HALF-DUPLEX — OPT-IN, default OFF. On a headset (the common case)
# the mic can't hear the earpieces, so there's no echo and you WANT to interrupt
# by voice (barge-in). Forcing half-duplex on everyone broke that — you couldn't
# talk while Hearth spoke. The real self-loop cause (Hearth falling back to a
# 'Stereo Mix' system-audio device) is fixed separately in _mic_index, and the
# text-similarity echo guard catches speaker bleed. So barge stays ON by default;
# a speaker+mic user who still loops turns half-duplex on in Settings > Voice
# (or HEARTH_VOICE_HALF_DUPLEX=1), which holds the mic closed while Hearth speaks.
def _half_duplex() -> bool:
    v = os.environ.get("HEARTH_VOICE_HALF_DUPLEX")
    if v is not None:
        return v.strip().lower() not in ("0", "false", "no", "off")
    try:
        from .web import _load_settings
        return bool(_load_settings().get("voice_half_duplex"))
    except Exception:
        return False
# How long after TTS stops the mic stays deaf, to swallow the acoustic tail +
# device/transcription buffering that arrives late on speakers.
_ECHO_TAIL_S: float = float(os.environ.get("HEARTH_VOICE_ECHO_TAIL_S", "1.8") or "1.8")
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
    # This whole block is OPTIONAL — the recorder below runs silero via ONNX
    # (silero_use_onnx=True), which needs neither torch nor torch.hub. So ANY
    # failure here must be swallowed. The packaged build bundles torch but NOT
    # torch.hub, so `torch.hub.set_dir` raises AttributeError (not ImportError);
    # catching only ImportError let it escape and warm the recorder forever.
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
    except Exception:
        pass  # no torch / no torch.hub (packaged build) — ONNX silero covers it

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
        if _half_duplex():
            # Speaker-safe: never let audio-over-TTS arm a barge. On speakers that
            # "speech" is almost always Hearth's own voice bleeding back, and
            # arming a barge is what opened the grace window that let the echo
            # through as a user turn. No barge here → no self-loop.
            if _BARGE_DEBUG:
                print("[barge] speech_start over TTS ignored (half-duplex)", flush=True)
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


class NoUsableMic(RuntimeError):
    """Raised when there's no REAL, openable microphone. Voice refuses to start
    rather than fall back to a system-audio device and transcribe the speakers."""


def _probe_device(idx) -> bool:
    """True if the input device can actually be opened for capture. A Bluetooth
    HFP mic often enumerates but won't open, so we START a stream briefly instead
    of trusting check_input_settings."""
    try:
        import sounddevice as sd
        with sd.InputStream(device=idx, channels=1, samplerate=16000, blocksize=1024):
            pass
        return True
    except Exception:
        return False


def _device_name(idx) -> str:
    try:
        import sounddevice as sd
        return str(sd.query_devices(idx).get("name", ""))
    except Exception:
        return ""


def _mic_index():
    """Resolve the microphone to actually open — NEVER a system-audio/loopback
    device. This is the fix for 'I have no mic yet it transcribed my speakers':
    when a chosen mic can't open, the old code fell back to the OS default, which
    on many machines is a loopback capture of whatever the speakers play. Now we
    refuse that fallback and raise NoUsableMic so voice turns OFF with a clear
    message instead of looping on system audio."""
    global _mic_warning
    _mic_warning = ""
    from .listen import (input_device_index, is_system_audio_device,
                         list_input_devices)
    try:
        sel = input_device_index()
    except Exception:
        sel = None

    def _first_real_mic(skip=None):
        for d in list_input_devices():
            if d["index"] == skip or is_system_audio_device(d["name"]):
                continue
            if _probe_device(d["index"]):
                return d
        return None

    # 1) An explicitly-picked mic.
    if sel is not None:
        if is_system_audio_device(_device_name(sel)):
            raise NoUsableMic("The selected input is a system-audio (loopback) "
                              "device, not a microphone — it would transcribe your "
                              "speakers. Pick a real mic in Settings > Voice.")
        if _probe_device(sel):
            return sel
        # Picked mic won't open (Bluetooth HFP often won't). Fall back ONLY to a
        # real mic, never a loopback.
        alt = _first_real_mic(skip=sel)
        if alt is not None:
            _mic_warning = (f"Your selected mic couldn't open; using '{alt['name']}'. "
                            "Pick another in Settings > Voice if needed.")
            return alt["index"]
        raise NoUsableMic("Your microphone couldn't be opened and there's no other "
                          "real mic. Voice is off so Hearth doesn't transcribe your "
                          "speakers — reconnect the mic (or fix the Bluetooth headset) "
                          "and try again.")

    # 2) No explicit pick — use the OS default only if it's a real mic.
    try:
        import sounddevice as sd
        di = sd.query_devices(kind="input")
        dname = str(di.get("name", "")) if isinstance(di, dict) else ""
    except Exception:
        dname = ""
    if dname and not is_system_audio_device(dname):
        return None   # PortAudio default is a real mic
    alt = _first_real_mic()
    if alt is not None:
        if dname:
            _mic_warning = (f"The system default input is a system-audio device; "
                            f"using '{alt['name']}' so Hearth doesn't hear your speakers.")
        return alt["index"]
    raise NoUsableMic("No real microphone is available (the system default is a "
                      "system-audio device). Voice is off so Hearth won't transcribe "
                      "your speakers. Connect a mic and try again.")


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


def set_error_callback(cb: Optional[Callable[[str], None]]) -> None:
    """Register a function to fire with a message if the recorder can't build
    (missing voice dependency, model that won't load) so the UI can surface it
    instead of sitting on 'warming' until the watchdog trips."""
    global _error_cb
    _error_cb = cb


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
    except NoUsableMic as e:
        # No real mic — don't listen (would transcribe the speakers). Surface the
        # plain reason, not a scary "recorder failed" stack.
        print(f"[hearth.realtime_voice] {e}")
        _listening = False
        if _error_cb is not None:
            try: _error_cb(str(e))
            except Exception: pass
        return
    except Exception as e:
        print(f"[hearth.realtime_voice] recorder init failed: {e}")
        _listening = False
        if _error_cb is not None:
            try: _error_cb(f"Voice recorder failed to start: {e}")
            except Exception: pass
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
    # Discard whatever the recorder buffered BEFORE this session started. The mic
    # keeps capturing while the window is closed / during wake-word listening; without
    # this, the first rec.text() calls drain that backlog and the stray phrases get
    # concatenated into a single spurious user turn. Start every session on a clean mic.
    try:
        rec.clear_audio_queue()
    except Exception:
        pass
    _err_streak = 0   # consecutive rec.text() failures — a disconnected mic
    try:
        while not _stop_flag.is_set():
            try:
                # Block until silero detects an endpoint and faster-whisper
                # returns the finalized text. ~200-400ms after you stop talking.
                text = rec.text()
                _err_streak = 0
                # Diagnostic: proves whether VAD is actually endpointing (a final
                # utterance) vs blocking forever. If you SEE this line in the
                # terminal after you stop talking, the final fired.
                print(f"[hearth.realtime_voice] FINAL utterance -> {text!r}", flush=True)
            except Exception as e:
                # A mid-session mic disconnect (unplugging a Bluetooth headset) makes
                # the recorder spew 'Unanticipated host error' / 'device validation
                # failed' forever. Don't spin on it — after a short streak, stop voice
                # cleanly with a message instead of letting it silently recover onto
                # some other (maybe system-audio) device.
                _err_streak += 1
                print(f"[hearth.realtime_voice] rec.text() error ({_err_streak}): {e}", flush=True)
                if _err_streak >= 4:
                    print("[hearth.realtime_voice] mic looks disconnected — stopping voice", flush=True)
                    if _error_cb is not None:
                        try:
                            _error_cb("Your microphone disconnected, so voice mode stopped. "
                                      "Reconnect it and start voice again.")
                        except Exception:
                            pass
                    _stop_flag.set()
                    break
                time.sleep(0.3)
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
            if _half_duplex():
                # Speaker-safe: while Hearth is speaking, or within the echo tail
                # after, the mic is DEAF. Anything captured then is speaker bleed
                # (Hearth's own voice, a video, music) — never a user turn. This is
                # the hard stop against the self-reply loop on speakers. Flush the
                # buffer too so accumulated echo can't ride into the next turn.
                if _tts.is_speaking() or _tts.seconds_since_spoke() < _ECHO_TAIL_S:
                    try: rec.clear_audio_queue()
                    except Exception: pass
                    continue
            elif time.time() < _barge_grace_until:
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
            # Final net for split speaker/headphone setups: echo cancellation can't
            # cancel when playback and mic are on different devices, and the bleed
            # often arrives late enough (transcription lag + device buffering) to
            # slip past the timing gate above. If the words are mostly what the
            # assistant just said, it's that echo, not a real turn — drop it. A
            # headset user's words don't overlap, so they pass straight through.
            if _tts.seconds_since_spoke() < 6.0 and _looks_like_echo(text):
                try: rec.clear_audio_queue()
                except Exception: pass
                continue
            # Utterance accepted → LLM is about to work: HUD shows "thinking"
            # (brisk pulse) until the first TTS chunk flips it to "speaking".
            try: _tts.set_voice_state("thinking")
            except Exception: pass
            # Dispatch to the CURRENT sink — reopening the overlay / switching
            # chats rebinds this (below) without restarting the loop, so
            # utterances reach the live stream, not a dead one.
            try:
                (_on_utterance_ref or on_utterance)(text)
            except Exception as e:
                print(f"[hearth.realtime_voice] on_utterance error: {e}")
    finally:
        _listening = False


# The live utterance sink. start_continuous rebinds this on every (re)entry so a
# reopened overlay redirects to its NEW stream without restarting the recorder.
_on_utterance_ref: Optional[Callable[[str], None]] = None


def start_continuous(on_utterance: Callable[[str], None]) -> str:
    """Begin continuous listening. Rebindable — if the recorder is already warm
    (overlay reopened, chat switched) this repoints the sink and re-signals ready
    instead of starting a second loop, so the UI never sticks on 'warming up'."""
    global _listen_thread, _on_utterance_ref
    if not is_available():
        return "RealtimeSTT not installed — falling back to hearth.listen"
    _on_utterance_ref = on_utterance
    # Clear the stop flag FIRST. A previous close set it; without clearing, the
    # loop hits `if _stop_flag.is_set(): break` on the next utterance and DROPS
    # it — that's the "FINAL printed but nothing reached the LLM" after a reopen.
    _stop_flag.clear()
    # Rebind onto a loop that's GENUINELY still alive (chat switch / instant
    # reopen on a warm recorder). Guard on thread liveness AND a live recorder so
    # a stale _listening flag can never leave us signalling ready with no loop
    # actually running (which reads as "stuck on warming").
    if (_listening and _listen_thread is not None
            and _listen_thread.is_alive() and _recorder is not None):
        rc = _ready_cb
        if rc is not None:
            try: rc()
            except Exception: pass
        return "Realtime voice already listening"
    _listen_thread = threading.Thread(
        target=_continuous_loop, args=(on_utterance,), daemon=True,
    )
    _listen_thread.start()
    return "Realtime voice listening"


def stop_continuous() -> str:
    """Halt the listening loop AND drop the recorder singleton so the next open
    builds a fresh, working one. Reusing a .stop()'d recorder was what left voice
    stuck on 'warming up' after a close and reopen. Also releases the mic so the
    wake-word listener can take it back."""
    global _recorder
    _stop_flag.set()
    if _recorder is not None:
        try:
            _recorder.stop()
        except Exception:
            pass
    reset_recorder()
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
