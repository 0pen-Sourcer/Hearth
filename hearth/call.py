"""A voice call from the phone app (Hearth Plus).

The phone streams its mic as 16 kHz 16-bit mono frames over the encrypted
relay. Here they are fed to the same speech recognizer voice mode uses (no PC
mic involved); each finished sentence becomes a message in the call's chat,
and the reply is spoken by JARVIS's own voice into frames sent back to the
phone, as the reply is written, a clause at a time. Talking over JARVIS cuts
it off on both ends. Steps and approvals reach the call screen as they happen;
a spoken "yes" approves only what Auto mode would let run anyway.
"""
from __future__ import annotations

import base64
import re
import threading
import time
from typing import Any, Callable, Dict, Optional

from . import realtime_voice as _rv
from . import voice as _voice

# Where a reply may be cut to start speaking: a sentence end, or for the first
# piece a clause once it is long enough to sound whole.
_SENTENCE = re.compile(r"[.!?]+[\"')\]]*\s|\n\n")
_CLAUSE = re.compile(r"[,;:]\s")
FIRST_CLAUSE = 24


class Call:
    """One call: a fed voice session, a speaker of its own, and the chat it talks in."""

    def __init__(self, send: Callable[[Dict[str, Any]], None], say: Callable[[str], Any], cid: str = ""):
        self.send = send              # a frame to the phone
        self.say = say                # a sentence heard from the phone, into the chat as a message
        self.cid = cid
        self.lease: Optional[int] = None
        self.speaker = _voice.Speaker(sink=self._sink(), first_chunk=FIRST_CLAUSE)
        self.spoken = 0               # how much of the reply being written has gone to the voice
        self.reply = ""
        self.started = time.time()
        self.ended = False
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- the voice out
    def _sink(self):
        call = self

        class Sink:
            def __call__(self, samples, sr):
                call.send({"type": "call.down", "sr": int(sr),
                           "data": base64.b64encode(samples.astype("<i2").tobytes()).decode()})

            def flush(self):
                call.send({"type": "call.flush"})
        return Sink()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        self.send({"type": "call.state", "state": "connecting"})
        lease = _rv.start_continuous(self._heard, on_caption=self._caption, on_barge=self._barge,
                                     on_ready=self._ready, on_error=self._error,
                                     speaker=self.speaker, fed=True)
        if lease is None:
            self.send({"type": "call.error", "detail": "Voice isn't set up on this PC yet. Open voice mode there once."})
            return False
        self.lease = lease
        return True

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        self.speaker.stop()
        if self.lease is not None:
            _rv.stop_continuous(self.lease)
        self.send({"type": "call.ended", "seconds": int(time.time() - self.started)})

    # ---------------------------------------------------------------- the voice in
    def up(self, data: str, rate: int = 16000) -> None:
        """A frame of the phone's mic."""
        sess = _rv.session_for(self.lease)
        if sess is None or not data:
            return
        try:
            sess.feed(base64.b64decode(data), int(rate or 16000))
        except Exception:
            pass

    def _ready(self) -> None:
        self.send({"type": "call.state", "state": "listening"})

    def _error(self, why: str) -> None:
        self.send({"type": "call.error", "detail": str(why)})

    def _caption(self, text: str) -> None:
        self.send({"type": "call.partial", "text": text})

    def _barge(self) -> None:
        """They talked over JARVIS: stop speaking here and on the phone."""
        self.speaker.stop()
        self.send({"type": "call.state", "state": "listening"})

    def _heard(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self.spoken, self.reply = 0, ""
        self.speaker.reset_abort()
        self.send({"type": "call.heard", "text": text})
        self.send({"type": "call.state", "state": "thinking"})
        try:
            self.say(text)
        except Exception as e:
            self.send({"type": "call.error", "detail": f"That didn't reach {type(e).__name__}"})

    # ---------------------------------------------------------------- the reply, as it is written
    def live(self, text: str) -> None:
        """The reply so far: speak each piece that is complete."""
        with self._lock:
            self.reply = text or ""
            while True:
                rest = self.reply[self.spoken:]
                m = _SENTENCE.search(rest)
                cut = m.end() if m else 0
                if not cut and self.spoken == 0 and len(rest) >= FIRST_CLAUSE:
                    c = None
                    for c in _CLAUSE.finditer(rest):
                        if c.end() >= FIRST_CLAUSE:
                            break
                    cut = c.end() if c is not None and c.end() >= FIRST_CLAUSE else 0
                if not cut:
                    break
                piece = rest[:cut].strip()
                self.spoken += cut
                if piece:
                    self._speak(piece)

    def done(self, text: str) -> None:
        """The finished reply: speak what is left of it."""
        with self._lock:
            full = text or self.reply
            rest = full[self.spoken:].strip() if full.startswith(self.reply[:self.spoken]) else ""
            self.spoken = len(full)
            self.reply = ""
        if rest:
            self._speak(rest)

    def step(self, words: str) -> None:
        self.send({"type": "call.state", "state": "step", "words": words})

    def ask(self, question: str) -> None:
        """An approval: shown on the call screen and said aloud."""
        self.send({"type": "call.state", "state": "asking", "words": question})
        self._speak(question)

    def _speak(self, text: str) -> None:
        self.send({"type": "call.state", "state": "speaking"})
        self.speaker.speak(text)
