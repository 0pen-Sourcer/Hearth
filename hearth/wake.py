"""Wake-word listener — background thread that fires a callback when the
user says a configured phrase (the configured wake word).

Resource budget: low.
  - Energy-gated against the room's own level, so silence, a fan or a hum cost
    nothing but reading the mic (~0% CPU).
  - A run of sound up to a few seconds long is checked with a small
    faster-whisper model, the first words of its first seconds only. A long run
    (a TV, music) is checked once, then left alone until a real break.
  - The model is loaded when there is something to check and let go after
    WAKE_IDLE_UNLOAD_S of mic audio with nothing to check; while voice mode has
    dictation's model loaded, that one is used and nothing more is held.

Public API:
    from hearth.wake import WakeListener
    wl = WakeListener(on_wake=lambda phrase: print("WAKE!", phrase))
    wl.start()       # non-blocking
    wl.stop()
    wl.is_active()

Default wake phrases:
    "jarvis", "hey jarvis", "hi jarvis", "wake up jarvis",
    "yo jarvis", "jarvis wake up"

Override via env var HEARTH_WAKE_PHRASES (comma-separated):
    HEARTH_WAKE_PHRASES="jarvis,j a r v i s,hearth"
"""

from __future__ import annotations

import os
from . import env as _env
import sys
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
FLOOR_RISE = 0.02         # how fast the room's own level is followed upward, per block
FLOOR_FALL = 0.2          # and downward
BUSY_TAIL_S = 2.0         # the break a long run of sound (a TV, music) needs before the next check
FLOOR_TIMES = 2.5         # a block this far over the room's level counts as sound
# The wake check has a small model of its own, loaded when there is something to
# check and let go after a quiet while. It used the model picked for dictation,
# loaded at start and held for as long as Inglen ran (base.en: ~300 MB; large: GBs).
# base.en: tiny.en, half the cost per check, missed one name in twelve in the C36 measurement.
WAKE_MODEL = _env.get("WAKE_MODEL", "base.en")
IDLE_UNLOAD_S = float(_env.get("WAKE_IDLE_UNLOAD_S", "600"))
CHECK_S = 3.0             # of an utterance, what the check listens to
CHECK_TOKENS = 10         # and how many words' worth it writes down

DEFAULT_WAKE_PHRASES = [
    "jarvis", "hey jarvis", "hi jarvis", "yo jarvis",
    "wake up jarvis", "jarvis wake up",
]


def _wake_phrases() -> List[str]:
    env = _env.get("WAKE_PHRASES", "").strip()
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
    from . import default_agent_name
    name = (os.environ.get("HEARTH_PERSONA_NAME", "").strip() or default_agent_name()).lower()
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
# waking on "earth". Set HEARTH_WAKE_EDITS=0 to require an exact match.
def _wake_edit_budget(name: str) -> int:
    env = _env.get("WAKE_EDITS", "").strip()
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


# ----- The wake check's model -----

_OWN = None               # the small model this module loaded, when it has one
_USED = 0.0
_MODEL_LOCK = threading.Lock()


def _download_root() -> str:
    from . import listen as _ll
    return os.path.join(_ll.VOICES_DIR, "whisper")


def _fetch_model() -> None:
    """Put the small model on disk ahead of time (not in memory), so the first
    wake isn't kept waiting on a download."""
    try:
        from faster_whisper.utils import download_model  # type: ignore
        os.makedirs(_download_root(), exist_ok=True)
        download_model(WAKE_MODEL, cache_dir=_download_root())
    except Exception:
        pass


def _model(now: float = 0.0):
    """The model to check an utterance with: dictation's, when voice is in use and
    has it loaded already (nothing more held), else this module's small one,
    loaded now and let go after IDLE_UNLOAD_S without anything to check. `now`
    is the listener's clock: seconds of mic audio heard."""
    global _OWN, _USED
    _USED = now
    from . import listen as _ll
    shared = getattr(_ll, "_model", None)
    if shared is not None:
        return shared
    with _MODEL_LOCK:
        if _OWN is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore
                _OWN = WhisperModel(WAKE_MODEL, device="cpu", compute_type="int8",
                                    download_root=_download_root())
            except Exception:
                return None
        return _OWN


def _release_if_idle(now: float) -> None:
    global _OWN
    if _OWN is not None and now - _USED > IDLE_UNLOAD_S:
        with _MODEL_LOCK:
            _OWN = None
        import gc
        gc.collect()
        if sys.platform.startswith("linux"):
            try:                                      # hand the freed memory back to the system
                import ctypes
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass


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
        self.floor = 0.0          # the room's own level: a fan, a hum
        self.busy = False         # a long run of sound already checked once: wait for a pause

    def feed(self, block):
        """Take one block (1-D float array); return an utterance when one ends.

        "Loud" is over the fixed threshold and over the room's own level, which
        drops at once to a quieter block and creeps up slowly, so a fan or a hum
        stops counting as speech (it used to hold an utterance open forever, and
        the wake word went deaf). A run of sound longer than MAX_UTTERANCE_S (a TV,
        music) is checked once, from its start, then left alone until a pause."""
        import numpy as np
        rms = float(np.sqrt(np.mean(block * block)))
        # The room's level moves only outside a normal utterance: on quiet blocks
        # between them, and while a long run of sound (a TV, a fan) is waited out. It
        # falls faster than it rises, but not at once, so the short gaps between
        # a TV's words don't reset it.
        loud = rms > max(self.threshold, self.floor * FLOOR_TIMES)
        if self.busy or (not self.in_voice and not loud):
            step = FLOOR_FALL if rms < self.floor else FLOOR_RISE
            self.floor = rms if self.floor == 0.0 else self.floor + (rms - self.floor) * step
        if self.busy:
            self.quiet_s = 0.0 if loud else self.quiet_s + self.block_s
            if self.quiet_s >= BUSY_TAIL_S:
                self.busy, self.quiet_s = False, 0.0
            return None
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
        elif self.quiet_s < SILENCE_TAIL_S:
            # Still going past the longest a wake sentence needs: check its start once.
            audio = np.concatenate(self.buf)
            self.buf, self.in_voice, self.busy = [], False, True
            self.preroll.clear()
            return audio
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
        self._heard_s = 0.0       # seconds of mic audio read: the clock the model is let go by

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start the listener. Returns False if deps missing."""
        # Only that the pieces are there: nothing is loaded until there is
        # something to check (see _model).
        import importlib.util
        try:
            if not all(importlib.util.find_spec(m) for m in ("sounddevice", "numpy", "faster_whisper")):
                return False
        except (ImportError, ValueError):
            return False
        threading.Thread(target=_fetch_model, daemon=True, name="hearth-wake-fetch").start()
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
        """(stream, '') or (None, why). The picked mic, looked up by name in a
        freshly enumerated device table, so a headset plugged in after launch
        is found; with none picked, the Windows default input when it is a real
        mic. Never a guess among other devices: that is how wake ended up on a
        virtual mic that records nothing, or on a loopback of the speakers."""
        from . import listen as _ll
        try:
            from .web import _load_settings
            cfg = _load_settings() or {}
        except Exception:
            cfg = {}
        name = str(cfg.get("voice_input_name", "") or "").strip()
        if name and not cfg.get("wake_on_bluetooth") and _ll.named_handsfree(name):
            return None, self._bluetooth_why(name)
        rows, default = _ll.device_rows()
        if not name:
            legacy, why = _ll.name_for_numbered_pick(cfg.get("voice_input_device", -1))
            if why:
                return None, why + ("; wake is paused until it is" if "not connected" in why else "")
            name = legacy
        if name:
            dev = _ll.index_for_name(name, rows)
            if dev is None:
                return None, f"your mic '{name}' is not connected; wake is paused until it is"
        else:
            dev = default
            name = next((r[1] for r in rows if r[0] == dev), "") if dev is not None else ""
            if not name:
                return None, "no microphone is connected"
        if _ll.is_system_audio_device(name):
            return None, f"'{name}' is not a microphone; pick yours in Settings > Voice"
        if not cfg.get("wake_on_bluetooth") and _ll.is_bluetooth_handsfree(name, rows):
            # Holding a Bluetooth headset's mic keeps it in hands-free mode (call-quality
            # sound) for as long as Hearth runs. Voice mode still uses it when asked.
            return None, self._bluetooth_why(name)
        try:
            return _ll.MicInput(dev, self.sample_rate, int(self.sample_rate * BLOCK_S)), ""
        except Exception as e:
            return None, f"'{name}' would not open ({type(e).__name__})"

    @staticmethod
    def _bluetooth_why(name: str) -> str:
        return (f"'{name}' is a Bluetooth headset; the wake word leaves its mic alone so it keeps "
                f"full-quality sound (Settings > Voice can let it listen)")

    @staticmethod
    def _waiting_on() -> str:
        """What a paused wake word waits to change before it tries again: the
        input devices (listed without starting PortAudio) and the voice settings."""
        from . import listen as _ll
        try:
            from .web import _load_settings
            cfg = _load_settings() or {}
        except Exception:
            cfg = {}
        return "%s|%s|%s|%s" % (_ll.input_fingerprint(), cfg.get("voice_input_name", ""),
                                cfg.get("voice_input_device", ""), cfg.get("wake_on_bluetooth", ""))

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
        stream, said, waited = None, "", ""
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
                    if said and self._waiting_on() == waited:
                        # Nothing changed since the mic couldn't be used: don't start
                        # PortAudio again (its start pokes every device; see input_fingerprint).
                        self._stop.wait(3.0)
                        continue
                    waited = self._waiting_on()
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
                self._heard_s += BLOCK_S
                _release_if_idle(self._heard_s)
                audio = seg.feed(block[:, 0])
                if audio is not None and time.time() - self._last_wake_ts > COOLDOWN_S:
                    self._maybe_wake(audio)
        finally:
            self._close(stream)
            _ll.wake_released.set()

    def _maybe_wake(self, audio) -> None:
        model = _model(self._heard_s)
        if model is None:
            return
        try:
            # The name opens what is said to it: the first words of the first
            # seconds are all the check needs, so it stops there (a TV's long
            # sentences cost a full transcription each before).
            segments, _info = model.transcribe(
                audio[:int(self.sample_rate * CHECK_S)], language="en", beam_size=1, vad_filter=False,
                without_timestamps=True, condition_on_previous_text=False, max_new_tokens=CHECK_TOKENS,
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
