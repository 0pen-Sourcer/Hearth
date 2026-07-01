"""Wake-word listener — background thread that fires a callback when the
user says a configured phrase ("Jarvis", "Hey Jarvis", "Wake up Jarvis", …).

Resource budget: low.
  - Energy-gated: only does whisper inference when mic input crosses a
    voice-activity threshold (so it sleeps through silence at ~0% CPU).
  - When voice IS detected, records up to 3 seconds, transcribes with the
    same faster-whisper instance used by listen.py, fuzzy-matches against
    the wake phrases.
  - Reuses the loaded whisper model — no extra VRAM, ~5% CPU spikes when
    audio is detected.

Public API:
    from hearth.wake import WakeListener
    wl = WakeListener(on_wake=lambda phrase: print("WAKE!", phrase))
    wl.start()       # non-blocking
    wl.stop()
    wl.is_active()

Default wake phrases:
    "jarvis", "hey jarvis", "hi jarvis", "wake up jarvis",
    "yo jarvis", "jarvis wake up"

Override via env var JARVIS_WAKE_PHRASES (comma-separated):
    JARVIS_WAKE_PHRASES="jarvis,j a r v i s,hearth"
"""

from __future__ import annotations

import os
import threading
import time
from queue import Queue
from typing import Callable, List, Optional


# ----- Configuration -----

SAMPLE_RATE = 16000
WINDOW_SEC = 2.5  # rolling window of audio to transcribe per detection
ENERGY_THRESHOLD = 0.012  # RMS threshold to wake the transcriber
SILENCE_TAIL_S = 0.7      # how long quiet must persist before we cut a window
COOLDOWN_S = 4.0          # don't fire twice in this window after a wake

DEFAULT_WAKE_PHRASES = [
    "jarvis", "hey jarvis", "hi jarvis", "yo jarvis",
    "wake up jarvis", "jarvis wake up",
]


def _wake_phrases() -> List[str]:
    env = os.environ.get("JARVIS_WAKE_PHRASES", "").strip()
    if env:
        return [p.strip().lower() for p in env.split(",") if p.strip()]
    # Track the agent's actual name — a renamed agent (FRIDAY) should wake on
    # "hey friday", not still on "jarvis".
    name = os.environ.get("HEARTH_PERSONA_NAME", "").strip().lower()
    if name and name != "jarvis":
        return [name, f"hey {name}", f"hi {name}", f"yo {name}",
                f"wake up {name}", f"{name} wake up"]
    return DEFAULT_WAKE_PHRASES


def _normalize(t: str) -> str:
    """Lowercase + collapse spaces + drop punctuation."""
    import re
    t = (t or "").lower().strip()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _matches_wake(transcript: str, phrases: List[str]) -> Optional[str]:
    n = _normalize(transcript)
    if not n:
        return None
    for p in phrases:
        if p in n:
            return p
    return None


# ----- Listener -----

class WakeListener:
    def __init__(self, on_wake: Callable[[str], None],
                 phrases: Optional[List[str]] = None,
                 sample_rate: int = SAMPLE_RATE):
        self.on_wake = on_wake
        self.phrases = phrases or _wake_phrases()
        self.sample_rate = sample_rate
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_wake_ts = 0.0

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start the listener. Returns False if deps missing."""
        try:
            import sounddevice as _sd  # noqa
            import numpy as _np         # noqa
            from . import listen as _ll
            if not _ll.is_available():
                return False
            # Warm the model so first detection is fast
            _ll._try_load_model()
        except Exception:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hearth-wake")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        import sounddevice as sd
        import numpy as np
        from . import listen as _ll

        block_dur = 0.1  # 100ms blocks
        block_size = int(self.sample_rate * block_dur)
        # Rolling buffer ~ WINDOW_SEC long
        max_blocks = int(WINDOW_SEC / block_dur)
        buf: list = []
        in_voice = False
        quiet_since: Optional[float] = None

        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate, channels=1,
                dtype="float32", blocksize=block_size,
            )
            stream.start()
        except Exception:
            return

        try:
            while not self._stop.is_set():
                try:
                    block, _ = stream.read(block_size)
                except Exception:
                    time.sleep(0.1)
                    continue
                pcm = block[:, 0]
                rms = float(np.sqrt(np.mean(pcm * pcm)))

                if rms > ENERGY_THRESHOLD:
                    in_voice = True
                    quiet_since = None
                    buf.append(pcm.copy())
                    if len(buf) > max_blocks:
                        buf.pop(0)
                else:
                    if in_voice:
                        if quiet_since is None:
                            quiet_since = time.time()
                        if time.time() - quiet_since > SILENCE_TAIL_S:
                            # End of utterance — transcribe what we have
                            if buf and time.time() - self._last_wake_ts > COOLDOWN_S:
                                audio = np.concatenate(buf)
                                if len(audio) > self.sample_rate * 0.3:
                                    self._maybe_wake(audio)
                            buf = []
                            in_voice = False
                            quiet_since = None
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _maybe_wake(self, audio) -> None:
        from . import listen as _ll
        model = _ll._try_load_model()
        if model is None:
            return
        try:
            segments, _info = model.transcribe(
                audio, language="en", beam_size=1, vad_filter=False,
                # Be conservative — wake words are short, no need for long ctx
                without_timestamps=True,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            return
        matched = _matches_wake(text, self.phrases)
        if matched:
            self._last_wake_ts = time.time()
            try:
                self.on_wake(matched)
            except Exception:
                pass
