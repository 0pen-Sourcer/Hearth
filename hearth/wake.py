"""Wake-word listener — background thread that fires a callback when the
user says a configured phrase (the configured wake word).

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
from typing import Callable, List, Optional


# ----- Configuration -----

SAMPLE_RATE = 16000
BLOCK_S = 0.1             # the mic is read in blocks this long
ENERGY_THRESHOLD = 0.012  # RMS a block needs to count as sound
SILENCE_TAIL_S = 0.7      # quiet that ends an utterance
PREROLL_S = 0.3           # kept from before the onset: a soft first consonant
MAX_UTTERANCE_S = 4.0     # a wake word opens a sentence; the rest is not needed
MIN_UTTERANCE_S = 0.3
COOLDOWN_S = 4.0          # don't fire twice in this window after a wake

DEFAULT_WAKE_PHRASES = [
    "jarvis", "hey jarvis", "hi jarvis", "yo jarvis",
    "wake up jarvis", "jarvis wake up",
]


def _wake_phrases() -> List[str]:
    env = os.environ.get("JARVIS_WAKE_PHRASES", "").strip()
    if env:
        return [p.strip().lower() for p in env.split(",") if p.strip()]
    # Whatever the user typed in Settings, Voice. Without this the phrases were
    # reachable only through an environment variable, so nobody could see or
    # change what the thing actually listens for.
    try:
        from .web import _load_settings
        saved = str((_load_settings() or {}).get("wake_phrases") or "").strip()
        if saved:
            return [p.strip().lower() for p in saved.split(",") if p.strip()]
    except Exception:
        pass
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


# Speech recognition renders a short, unusual name inconsistently: a real log
# line shows "okay jarvis wake up" coming back as "okay jarmis make up", which
# an exact match never sees. So allow the name to be off by one letter, but
# only when it is long enough that one letter is not most of the word, and only
# when the first letter still agrees. That accepts jarmis, jervis and javis
# while refusing travis, service, harvest, and it stops a name like "hearth"
# waking on "earth". Set JARVIS_WAKE_EDITS=0 to require an exact match.
def _wake_edit_budget(name: str) -> int:
    env = os.environ.get("JARVIS_WAKE_EDITS", "").strip()
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            pass
    return 1 if len(name) >= 6 else 0


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _wake_name(phrases: List[str]) -> str:
    """The bare name out of the phrase list, which is the shortest entry."""
    solo = [p for p in phrases if p and " " not in p]
    if solo:
        return min(solo, key=len)
    return min(phrases, key=len).split()[-1] if phrases else ""


def _matches_wake(transcript: str, phrases: List[str]) -> Optional[str]:
    n = _normalize(transcript)
    if not n:
        return None
    for p in phrases:
        if p in n:
            return p
    name = _wake_name(phrases)
    budget = _wake_edit_budget(name)
    if len(name) < 4 or budget <= 0:
        return None
    for w in n.split():
        if len(w) < 4 or w[0] != name[0]:
            continue
        if _edit_distance(w, name) <= budget:
            return name
    return None


class _Segmenter:
    """Cuts the stream of mic blocks into utterances for the wake check.

    An utterance starts at the first block over the energy threshold, carries
    the PREROLL_S before it, and runs, quiet blocks included, until
    SILENCE_TAIL_S of quiet. Keeping only the loud blocks, as this once did,
    cut out consonants and the gaps between words and handed the recognizer
    glued audio; starting at the first loud block lost the first syllable;
    and a rolling window kept the end of a long sentence, dropping the wake
    word that opens it.
    """

    def __init__(self, threshold: float = ENERGY_THRESHOLD, block_s: float = BLOCK_S):
        from collections import deque
        self.threshold = threshold
        self.block_s = block_s
        self.preroll = deque(maxlen=max(1, round(PREROLL_S / block_s)))
        self.buf: list = []
        self.in_voice = False
        self.quiet_s = 0.0

    def feed(self, block):
        """Take one block (1-D float array); return an utterance when one ends."""
        import numpy as np
        loud = float(np.sqrt(np.mean(block * block))) > self.threshold
        if not self.in_voice:
            if loud:
                self.in_voice = True
                self.quiet_s = 0.0
                self.buf = [b.copy() for b in self.preroll] + [block.copy()]
            else:
                self.preroll.append(block.copy())
            return None
        self.quiet_s = 0.0 if loud else self.quiet_s + self.block_s
        if len(self.buf) * self.block_s < MAX_UTTERANCE_S:
            self.buf.append(block.copy())
        if self.quiet_s < SILENCE_TAIL_S:
            return None
        audio = np.concatenate(self.buf)
        self.buf, self.in_voice = [], False
        self.preroll.clear()
        return audio if len(audio) * self.block_s / len(block) >= MIN_UTTERANCE_S else None


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

    def _open_mic(self):
        """(stream, '') or (None, why). The picked mic, looked up by name in this
        process's device table; with none picked, the Windows default input when
        it is a real mic. Never a guess among other devices: that is how wake
        ended up on a virtual mic that records nothing, or on a loopback of the
        speakers that woke on a video saying the name."""
        import sounddevice as sd
        from . import listen as _ll
        try:
            from .web import _load_settings
            cfg = _load_settings() or {}
        except Exception:
            cfg = {}
        name = str(cfg.get("voice_input_name", "") or "").strip()
        dev = None
        if name:
            dev = _ll.index_for_name(name, _ll.sounddevice_inputs())
            if dev is None:
                return None, f"your mic '{name}' is not connected; wake is paused until it is"
        else:
            try:
                legacy = int(cfg.get("voice_input_device", -1))
            except (TypeError, ValueError):
                legacy = -1
            if legacy >= 0:
                dev = legacy
                try:
                    name = str(sd.query_devices(dev).get("name", ""))
                except Exception:
                    return None, f"the saved mic (input {dev}) is gone; pick it again in Settings > Voice"
            else:
                try:
                    name = str(sd.query_devices(kind="input").get("name", ""))
                except Exception:
                    return None, "no microphone is connected"
        if _ll.is_system_audio_device(name):
            return None, (f"'{name}' records your speakers, not a microphone; pick your "
                          "microphone in Settings > Voice")
        try:
            st = sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="float32",
                                blocksize=int(self.sample_rate * BLOCK_S), device=dev)
            st.start()
            return st, ""
        except Exception as e:
            return None, f"'{name}' would not open ({type(e).__name__})"

    @staticmethod
    def _close(stream) -> None:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _loop(self) -> None:
        from . import listen as _ll
        block_n = int(self.sample_rate * BLOCK_S)
        seg = _Segmenter()
        stream, said = None, ""
        try:
            while not self._stop.is_set():
                if _ll.mic_claimed():
                    # Voice mode has the mic: let go, and come back after.
                    if stream is not None:
                        self._close(stream)
                        stream = None
                        seg = _Segmenter()
                    _ll.wake_released.set()
                    self._stop.wait(0.2)
                    continue
                if stream is None:
                    stream, why = self._open_mic()
                    if stream is None:
                        if why != said:
                            print(f"[hearth.wake] {why}", flush=True)
                            said = why
                        self._stop.wait(3.0)
                        continue
                    said = ""
                    _ll.wake_released.clear()
                try:
                    block, _ = stream.read(block_n)
                except Exception as e:
                    # A headset that dropped: reopen by name rather than spin.
                    print(f"[hearth.wake] the mic stopped ({type(e).__name__}); reopening", flush=True)
                    self._close(stream)
                    stream = None
                    _ll.wake_released.set()
                    continue
                audio = seg.feed(block[:, 0])
                if audio is not None and time.time() - self._last_wake_ts > COOLDOWN_S:
                    self._maybe_wake(audio)
        finally:
            self._close(stream)
            _ll.wake_released.set()

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
        # Log what wake heard (goes to hearth_tray.log). If this line shows your
        # words but nothing opens, it's a phrase mismatch; if it never appears
        # while you speak, wake is on the wrong mic. Set HEARTH_WAKE_DEBUG=0 off.
        if text and os.environ.get("HEARTH_WAKE_DEBUG", "1") not in ("0", "", "false"):
            print(f"[hearth.wake] heard: {text!r}", flush=True)
        matched = _matches_wake(text, self.phrases)
        if matched:
            self._last_wake_ts = time.time()
            try:
                self.on_wake(matched)
            except Exception:
                pass
