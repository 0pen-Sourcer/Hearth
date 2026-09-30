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
  HEARTH_REALTIME_MODEL     faster-whisper size override; without it the
                            whisper-size setting (JARVIS_STT_MODEL) is used,
                            falling back to tiny.en
  HEARTH_REALTIME_LANG      language override; without it the spoken-language
                            setting (JARVIS_STT_LANGUAGE) is used, and a blank
                            one means detect it per utterance
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, Optional

from . import voice as _tts

_REALTIME_AVAILABLE: Optional[bool] = None


class _Session:
    """One voice session: its own recorder, stop signal and callbacks.

    Everything used to be module-wide. A session that was ending shared the
    stop flag, the callbacks and the recorder slot with the one starting after
    it, so reopening voice within a few seconds had the old loop clear-flag its
    way back to life, then stop the new session when its dead recorder errored.
    Each session now owns its state, and only its current owner (the lease
    handed out by start_continuous) can stop it.
    """

    def __init__(self, lease: int, on_utterance, on_caption, on_barge, on_ready, on_error):
        self.lease = lease
        self.stop = threading.Event()
        self.recorder = None
        self.thread: Optional[threading.Thread] = None
        self.listening = False
        self.on_utterance = on_utterance
        self.on_caption = on_caption    # live partial transcripts, ~10/sec
        self.on_barge = on_barge        # the user started talking over TTS
        self.on_ready = on_ready        # the model loaded and the mic is open
        self.on_error = on_error        # the recorder could not start, or died


_session: Optional[_Session] = None
_session_lock = threading.Lock()
_next_lease = 0
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
    """Shared with the CLI capture loop; see voice.half_duplex_enabled."""
    try:
        from . import voice as _v
        return _v.half_duplex_enabled()
    except Exception:
        return False
# How long after TTS stops the mic stays deaf, to swallow the acoustic tail +
# device/transcription buffering that arrives late on speakers.
# Echo tail lives in voice.py so the realtime path and the CLI capture loop
# cannot drift apart. Read through a helper, not copied at import, so an env
# change is picked up without a restart.
def _echo_tail() -> float:
    try:
        from . import voice as _v
        return _v.ECHO_TAIL_S
    except Exception:
        return 1.8
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


def _model_size() -> str:
    """Which faster-whisper size the recorder runs.

    The whisper-size setting writes JARVIS_STT_MODEL, which the push-to-talk
    path reads. Nothing sets HEARTH_REALTIME_MODEL, so reading only that left
    voice mode pinned to tiny.en no matter what the setting said, and the
    dropdown that appeared to control it changed the other path alone. An
    explicitly exported override still wins.
    """
    for _v in (os.environ.get("HEARTH_REALTIME_MODEL"),
               os.environ.get("JARVIS_STT_MODEL")):
        if (_v or "").strip():                 # a blank export is not a choice
            return _v.strip()
    return "tiny.en"


def _language() -> str:
    """The language code the recorder transcribes in, blank to detect it.

    The same split as the size above: the setting writes JARVIS_STT_LANGUAGE
    and only the push-to-talk path was reading it, so voice mode stayed pinned
    to English however the setting was left. A model whose name ends in .en
    holds nothing else, so there is nothing to detect.
    """
    if _model_size().lower().endswith(".en"):
        return "en"
    for _v in (os.environ.get("HEARTH_REALTIME_LANG"),
               os.environ.get("JARVIS_STT_LANGUAGE")):
        if (_v or "").strip():
            return _v.strip().lower()
    return ""


def status() -> dict:
    return {
        "available": is_available(),
        "listening": is_listening(),
        "model": _model_size(),
        "engine": "RealtimeSTT (silero VAD + faster-whisper, streaming)",
    }


def _build_recorder(sess: Optional[_Session] = None):
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

    model = _model_size()
    lang = _language()

    def _on_realtime_update(text: str) -> None:
        # Live partial transcript while user speaks. Drop captions arriving
        # mid-TTS so we never caption the assistant's own voice through the
        # speakers — they're noise, not signal.
        if _tts.is_speaking():
            return
        cb = sess.on_caption if sess is not None else None
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
        cb = sess.on_barge if sess is not None else None
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


def _pyaudio_inputs(pa) -> list:
    """PyAudio's table as [(index, name, input_channels, hostapi)]. RealtimeSTT
    opens the mic through PyAudio, so its index has to come from this table."""
    out = []
    for i in range(pa.get_device_count()):
        try:
            d = pa.get_device_info_by_index(i)
            api = pa.get_host_api_info_by_index(d["hostApi"]).get("name", "")
            out.append((i, str(d.get("name", "")), int(d.get("maxInputChannels", 0)), api))
        except Exception:
            continue
    return out


def _probe_device(pa, idx) -> bool:
    """Open and read the device the way RealtimeSTT validates it: 16 kHz,
    16-bit mono, one 512-frame buffer. An easier check here than there let a
    device through that the recorder then refused, and the session reported
    ready while hearing nothing."""
    import pyaudio  # type: ignore
    try:
        st = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True,
                     frames_per_buffer=512, input_device_index=idx, start=False)
        try:
            st.start_stream()
            data = st.read(512, exception_on_overflow=False)
        finally:
            try:
                st.stop_stream()
            except Exception:
                pass
            st.close()
        return len(data) > 0
    except Exception:
        return False


def _mic_index():
    """The PyAudio index of the mic to open. Never a loopback of the speakers,
    and never a guess: a device picked by chance among several can be a
    virtual one that records silence, which looks like voice not working.

    Picked mic: that one. If it is gone or will not open, the Windows default
    input when that is a real mic, said out loud; otherwise stop and say why.
    Nothing picked: the Windows default input when it is a real mic that
    opens; otherwise stop and name the mics there are to pick from."""
    global _mic_warning
    _mic_warning = ""
    import pyaudio  # type: ignore
    from .listen import is_system_audio_device, selected_input_name
    pa = pyaudio.PyAudio()
    try:
        table = _pyaudio_inputs(pa)
        by_idx = {d[0]: d for d in table}
        name = selected_input_name()
        if not name:                                 # a pick saved as a number
            try:
                from .listen import input_device_index
                import sounddevice as sd  # type: ignore
                legacy = input_device_index()
                if legacy is not None:
                    name = str(sd.query_devices(legacy).get("name", ""))
            except Exception:
                name = ""
        try:
            d = pa.get_default_input_device_info()
            default = (int(d["index"]), str(d.get("name", "")))
        except Exception:
            default = None
        default_ok = (default is not None and not is_system_audio_device(default[1])
                      and _probe_device(pa, default[0]))

        if name:
            from .listen import index_for_name
            idx = index_for_name(name, table)
            if is_system_audio_device(name):
                raise NoUsableMic(f"'{name}' records what your speakers play, not a "
                                  "microphone. Pick your mic in Settings > Voice.")
            if idx is not None and _probe_device(pa, idx):
                return idx
            why = "is not connected" if idx is None else "would not open"
            if default_ok and index_for_name(default[1], table) != idx:
                _mic_warning = (f"'{name}' {why}, so voice is using the Windows "
                                f"default input, '{default[1]}'.")
                return default[0]
            raise NoUsableMic(f"Your microphone '{name}' {why}. Reconnect it, or pick "
                              "another in Settings > Voice.")

        if default_ok:
            return default[0]
        fullest: dict = {}                 # one entry per mic, its fullest name
        for i, n, ch, _api in table:
            if ch > 0 and not is_system_audio_device(n) and "@system32" not in n.lower():
                key = n.strip().lower()[:31]
                if len(n) > len(fullest.get(key, "")):
                    fullest[key] = n
        mics = list(fullest.values())
        if default is not None and is_system_audio_device(default[1]):
            lead = (f"The Windows default input is '{default[1]}', which records "
                    "your speakers, so Hearth will not listen to it.")
        else:
            lead = "The Windows default input could not be opened."
        tail = (" Pick your microphone in Settings > Voice: " + ", ".join(mics[:4]) + "."
                if mics else " No other microphone is connected.")
        raise NoUsableMic(lead + tail)
    finally:
        pa.terminate()


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


def _shutdown_recorder(rec) -> None:
    """End a recorder for good. stop() only pauses recording: its mic reader
    and transcription processes stay alive holding the mic and a Whisper
    model, measured at two processes and ~785 MB per voice session with the
    smallest model. shutdown() is what releases them."""
    if rec is None:
        return
    try:
        rec.shutdown()
    except Exception:
        pass


def _call(cb, *args) -> None:
    if cb is not None:
        try:
            cb(*args)
        except Exception:
            pass


def _continuous_loop(sess: _Session) -> None:
    """Run one session until it is stopped, feeding each finished utterance to
    its current sink. The recorder is shut down on the way out, always."""
    global _barge_grace_until
    try:
        rec = _build_recorder(sess)
    except NoUsableMic as e:
        # No real mic — don't listen (would transcribe the speakers). Surface the
        # plain reason, not a scary "recorder failed" stack.
        print(f"[hearth.realtime_voice] {e}")
        _call(sess.on_error, str(e))
        return
    except Exception as e:
        print(f"[hearth.realtime_voice] recorder init failed: {e}")
        _call(sess.on_error, f"Voice recorder failed to start: {e}")
        return
    sess.recorder = rec
    if sess.stop.is_set():          # stopped while the model was loading
        _shutdown_recorder(rec)
        return

    sess.listening = True
    # The model has loaded. Signal ready so the HUD leaves "warming up";
    # start_continuous returns long before this.
    _call(sess.on_ready)
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
        while not sess.stop.is_set():
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
                if sess.stop.is_set():
                    break           # shut down on purpose; text() gave up
                _err_streak += 1
                print(f"[hearth.realtime_voice] rec.text() error ({_err_streak}): {e}", flush=True)
                if _err_streak >= 4:
                    print("[hearth.realtime_voice] mic looks disconnected — stopping voice", flush=True)
                    _call(sess.on_error, "Your microphone disconnected, so voice mode "
                                         "stopped. Reconnect it and start voice again.")
                    sess.stop.set()
                    break
                time.sleep(0.3)
                continue
            if sess.stop.is_set():
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
                if _tts.is_speaking() or _tts.seconds_since_spoke() < _echo_tail():
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
            elif _tts.is_speaking() or _tts.seconds_since_spoke() < _echo_tail():
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
                sess.on_utterance(text)
            except Exception as e:
                print(f"[hearth.realtime_voice] on_utterance error: {e}")
    finally:
        sess.listening = False
        _shutdown_recorder(rec)


def start_continuous(on_utterance: Callable[[str], None], *,
                     on_caption: Optional[Callable[[str], None]] = None,
                     on_barge: Optional[Callable[[], None]] = None,
                     on_ready: Optional[Callable[[], None]] = None,
                     on_error: Optional[Callable[[str], None]] = None) -> Optional[int]:
    """Begin listening and return a lease; only that lease can stop it.

    A session that is still running (the overlay reopened, the chat switched)
    is handed to the new caller instead of being rebuilt, so the model does not
    reload. Returns None when realtime voice is not installed."""
    global _session, _next_lease
    if not is_available():
        return None
    old_rec = None
    with _session_lock:
        _next_lease += 1
        lease = _next_lease
        cur = _session
        if (cur is not None and not cur.stop.is_set()
                and cur.thread is not None and cur.thread.is_alive()):
            cur.lease = lease
            cur.on_utterance, cur.on_caption = on_utterance, on_caption
            cur.on_barge, cur.on_ready, cur.on_error = on_barge, on_ready, on_error
            if cur.listening:
                _call(on_ready)
            return lease
        if cur is not None:
            cur.stop.set()
            old_rec = cur.recorder
        sess = _Session(lease, on_utterance, on_caption, on_barge, on_ready, on_error)
        sess.thread = threading.Thread(target=_continuous_loop, args=(sess,),
                                       daemon=True, name=f"hearth-voice-{lease}")
        _session = sess
    if old_rec is not None:
        threading.Thread(target=_shutdown_recorder, args=(old_rec,), daemon=True).start()
    sess.thread.start()
    return lease


def stop_continuous(lease: Optional[int] = None) -> bool:
    """Stop the running session and release its mic and model. With a lease,
    only if that lease still owns it, so a stream that is closing late cannot
    stop the session that replaced it. Returns whether anything was stopped."""
    global _session
    with _session_lock:
        cur = _session
        if cur is None or (lease is not None and cur.lease != lease):
            return False
        _session = None
        cur.stop.set()
        rec = cur.recorder
    if rec is not None:
        # Shutting down waits on the recorder's processes; the loop thread
        # also exits once text() returns, and shutdown is idempotent.
        threading.Thread(target=_shutdown_recorder, args=(rec,), daemon=True).start()
    return True


def owns(lease: Optional[int]) -> bool:
    """True while `lease` is the owner of the running session."""
    cur = _session
    return lease is not None and cur is not None and cur.lease == lease


def is_listening() -> bool:
    cur = _session
    return bool(cur is not None and cur.listening)


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
