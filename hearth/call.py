"""A voice call: from the phone app (Inglen Plus), or voice mode in the window.

The phone streams its mic as 16 kHz 16-bit mono frames over the encrypted
relay. Here they are fed to the same speech recognizer voice mode uses (no PC
mic involved); each finished sentence becomes a message in the call's chat,
and the reply is spoken by the assistant's own voice into frames sent back to
the phone, as the reply is written, a clause at a time. Talking over it, or a
tap on the phone, cuts it off on both ends. Steps and approvals reach the call
screen as they happen; a spoken "yes" approves only what Auto mode would let
run anyway, and for the rest the phone's Allow button is asked for out loud.

Voice mode in the window is the same call over a socket on the window's own
server: the page captures the mic with the browser's echo cancelling and plays
the voice through it, so talking over a reply works on speakers too.
"""
from __future__ import annotations

import base64
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import env as _env
from . import realtime_voice as _rv
from . import voice as _voice

# Where a reply may be cut to start speaking: a sentence end, or for the first
# piece a clause once it is long enough to sound whole.
_SENTENCE = re.compile(r"[.!?]+[\"')\]]*\s|\n\n")
_CLAUSE = re.compile(r"[,;:]\s")
_BREAK = re.compile(r"[,;:–-]\s")
FIRST_CLAUSE = 24
QUICK_START = 12

TAP_LINE = "That one needs a tap on Allow."


def languages() -> List[Dict[str, str]]:
    """What the call can listen in: detect, or one of the languages its voices speak."""
    out, seen = [{"code": "", "name": "Detect it"}], set()
    for code, name in _voice.VOICE_LANG_NAMES.items():
        short = "zh" if code == "cmn" else code[:2]
        if short in seen:
            continue
        seen.add(short)
        out.append({"code": short, "name": re.sub(r"\s*\(.*\)$", "", name)})
    return out


def voices() -> List[Dict[str, str]]:
    """The voices it can answer in, named the way a person reads them ("Emma", "English (UK), female")."""
    out = []
    for v in _voice.list_voices():
        kind = {"f": "female", "m": "male"}.get(v[1:2], "")
        lang = _voice.VOICE_LANG_NAMES.get(_voice.voice_language(v), "")
        out.append({"id": v, "name": v.split("_", 1)[-1].capitalize(), "about": ", ".join(x for x in (lang, kind) if x)})
    return out


class Call:
    """One call: a fed voice session, a speaker of its own, and the chat it talks in."""

    def __init__(self, send: Callable[[Dict[str, Any]], None], say: Callable[[str], Any], cid: str = "",
                 save: Optional[Callable[[Dict[str, Any]], Any]] = None, ref: str = "",
                 opener: Optional[Dict[str, Any]] = None, post: Optional[Callable[[str], Any]] = None):
        self.send = send              # a frame to the phone
        self.say = say                # a sentence heard from the phone, into the chat as a message
        self.save = save              # a setting changed in the call, kept where the window keeps it
        self.cid = cid
        self.ref = ref                # the phone's name for this call, so a late end never ends the next one
        self.lease: Optional[int] = None
        self.speaker = _voice.Speaker(sink=self._sink(), first_chunk=FIRST_CLAUSE)
        self.spoken = 0               # how much of the reply being written has gone to the voice
        self.reply = ""
        self.started = time.time()
        self.ended = False
        self.ask_id = ""              # the last approval asked in the call
        self.heard_n = 0              # sentences heard so far: a later one makes an earlier wait moot
        self.opener = opener          # a call it made itself: what it says first ({say, ask, question, tap})
        self.post = post              # a line it says on its own, kept in the call's chat
        self.leaving = False          # it said goodbye: the call ends once that has been heard
        self.ready = False
        self.turn = 0                 # the turn being spoken; a cut makes the one running go quiet
        # How soon a reply starts to be heard: by default the first clause once it is long enough
        # to sound whole; quick, at any clause break once a dozen letters are written (as the
        # window's voice mode always has).
        self.quick = False
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
        self.send(self.voice_info())
        return self._listen()

    def _listen(self) -> bool:
        lease = _rv.start_continuous(self._heard, on_caption=self._caption, on_barge=self._barge,
                                     on_ready=self._ready, on_error=self._error,
                                     speaker=self.speaker, fed=True)
        if lease is None:
            self.send({"type": "call.error", "detail": "Voice isn't set up on this PC yet. Open voice mode there once."})
            return False
        self.lease = lease
        if self.ended:                          # ended while the listener was starting
            _rv.stop_continuous(lease)
            return False
        return True

    def end(self, by: str = "") -> None:
        """The call ends. `by` is "pc" when this side hung up, so the phone can say so."""
        if self.ended:
            return
        self.ended = True
        self.speaker.stop()
        if self.lease is not None:
            _rv.stop_continuous(self.lease)
        self.send({"type": "call.ended", "seconds": int(time.time() - self.started),
                   **({"ref": self.ref} if self.ref else {}), **({"by": by} if by else {})})

    def hang_up(self) -> None:
        """The agent ended the call (end_session): once its goodbye has been heard, it hangs up."""
        if self.ended or self.leaving:
            return
        self.leaving = True

        def later():
            time.sleep(30)                      # the reply that should follow never came
            if not self.ended:
                self.end(by="pc")
        threading.Thread(target=later, daemon=True, name="call-hang-up").start()

    # ---------------------------------------------------------------- settings
    def voice_info(self) -> Dict[str, Any]:
        return {"type": "call.voice", "voice": _voice.current_voice(), "voices": voices(),
                "language": (_env.get("STT_LANGUAGE", "") or "").strip().lower(), "languages": languages()}

    def set(self, voice: Optional[str] = None, language: Optional[str] = None) -> None:
        """The voice it answers in or the language it listens in, changed from the call."""
        changed: Dict[str, Any] = {}
        if voice and voice in _voice.list_voices() and voice != _voice.current_voice():
            _voice.set_default_voice(voice)
            _env.set("VOICE", voice)
            changed["voice_name"] = voice
        codes = {x["code"] for x in languages()}
        if language is not None and language in codes and language != (_env.get("STT_LANGUAGE", "") or "").strip().lower():
            _env.set("STT_LANGUAGE", language)
            changed["stt_language"] = language
        if changed and self.save is not None:
            try:
                self.save(changed)
            except Exception:
                pass
        self.send(self.voice_info())
        if "stt_language" in changed and self.lease is not None and not self.ended:
            # The recognizer is built for one language: listen again in the new one.
            _rv.stop_continuous(self.lease)
            self.send({"type": "call.state", "state": "connecting"})
            self._listen()

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
        first, self.ready = not self.ready, True
        if first and self.opener:
            self._open()
            return
        self.send({"type": "call.state", "state": "listening"})

    def _open(self) -> None:
        """A call it made: say why first, and ask the question it called about."""
        o = self.opener or {}
        say = str(o.get("say") or "").strip()
        if say:
            if self.post is not None:
                try:
                    self.post(say)
                except Exception:
                    pass
            self._speak(say)
        if o.get("ask"):
            self.ask(str(o.get("question") or ""), tap=bool(o.get("tap")), aid=str(o["ask"]))
        else:
            self._settle()

    def _error(self, why: str) -> None:
        self.send({"type": "call.error", "detail": str(why)})

    def _caption(self, text: str) -> None:
        self.send({"type": "call.partial", "text": text})

    def _barge(self) -> None:
        """They talked over it: stop speaking here and on the phone."""
        self.speaker.stop()
        with self._lock:
            self.turn += 1
        self.send({"type": "call.state", "state": "listening", "barge": True})

    def hush(self) -> None:
        """The orb tapped on the phone: stop talking, keep the turn and the call."""
        self.speaker.stop()                     # the rest of this reply stays unsaid; its text still shows
        with self._lock:
            self.turn += 1
        self.send({"type": "call.state", "state": "listening"})

    def begin(self) -> int:
        """A turn starts in the call's chat (said, or typed during the call): its reply is spoken.
        Returns the turn's number; a reply given with an older one is not spoken."""
        with self._lock:
            self.spoken, self.reply = 0, ""
            self.turn += 1
            n = self.turn
        self.speaker.reset_abort()
        return n

    def _heard(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self.spoken, self.reply = 0, ""
            self.heard_n += 1
        self.speaker.reset_abort()
        self.send({"type": "call.heard", "text": text})
        try:
            got = self.say(text)
        except Exception as e:
            self.send({"type": "call.error", "detail": f"That didn't reach the PC ({type(e).__name__})"})
            return
        if got == "needs_tap":
            # Said yes to something only the button may allow: ask for the tap, out loud.
            self.send({"type": "call.state", "state": "asking", "tap": True, "again": True, "ask": self.ask_id})
            self._speak(TAP_LINE)
            self._settle()
            return
        if got == "answered":
            from .channel_core import _YES
            # The card on the phone and the question in the chat both show the answer.
            self.send({"type": "asked", "ask": self.ask_id, "yes": bool(_YES.match(text)),
                       **({"c": self.cid} if self.cid else {})})
        self.send({"type": "call.state", "state": "thinking"})

    # ---------------------------------------------------------------- the reply, as it is written
    def live(self, text: str, turn: Optional[int] = None) -> None:
        """The reply so far: speak each piece that is complete."""
        with self._lock:
            if turn is not None and turn != self.turn:
                return
            self.reply = text or ""
            while True:
                rest = self.reply[self.spoken:]
                m = _SENTENCE.search(rest)
                cut = m.end() if m else 0
                if not cut and self.spoken == 0 and self.quick and len(rest) >= QUICK_START:
                    ends = [c.end() for c in _BREAK.finditer(rest)]
                    cut = ends[-1] if ends else 0
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

    def done(self, text: str, turn: Optional[int] = None) -> None:
        """The finished reply: speak what is left of it, then listen again."""
        with self._lock:
            if turn is not None and turn != self.turn:
                return
            full = text or self.reply
            rest = full[self.spoken:].strip() if full.startswith(self.reply[:self.spoken]) else ""
            self.spoken = len(full)
            self.reply = ""
        if rest:
            self._speak(rest)
        self._settle()

    def _settle(self) -> None:
        """Back to listening once everything said has been played on the phone,
        unless something new was heard meanwhile."""
        n = self.heard_n

        def wait():
            idle = 0
            while not self.ended and idle < 2:
                idle = 0 if self.speaker.busy() else idle + 1
                time.sleep(0.1)
            if self.ended:
                return
            if self.leaving:
                self.end(by="pc")
            elif n == self.heard_n:
                self.send({"type": "call.state", "state": "listening"})
        threading.Thread(target=wait, daemon=True, name="call-settle").start()

    def step(self, words: str) -> None:
        self.send({"type": "call.state", "state": "step", "words": words})

    def ask(self, question: str, tap: bool = False, aid: str = "") -> None:
        """An approval: shown on the call screen and said aloud. For one only a tap
        may allow, it says so, so the person isn't left saying yes to nothing."""
        self.ask_id = aid
        self.speaker.reset_abort()              # asked even after the reply before it was cut
        self.send({"type": "call.state", "state": "asking", "words": question, "tap": bool(tap), "ask": aid})
        self._speak(question + (" " + TAP_LINE if tap else ""))
        self._settle()

    def _speak(self, text: str) -> None:
        self.send({"type": "call.state", "state": "speaking"})
        self.speaker.speak(text)


class Turn:
    """A reply this process writes in the call's chat, followed from its events
    (headless.run_once's emit) the way a bridge follows one: spoken as it is
    written, its steps shown on the call, the rest said when it ends."""

    _OPEN_TOOL = re.compile(r"<\|?tool_call\|?>(?![\s\S]*<\|?/\|?tool_call\|?>)", re.I)

    def __init__(self, call: Call):
        self.call = call
        self.n = call.begin()
        self.parts: List[str] = []
        self.written: List[str] = []

    def event(self, kind: str, kw: Dict[str, Any]) -> None:
        c = self.call
        if c.ended or self.n != c.turn:
            return
        if kind == "assistant_chunk" and kw.get("content"):
            self.written.append(kw["content"])
            so_far = "".join(self.parts) + "".join(self.written)
            if not self._OPEN_TOOL.search(so_far):      # a call written as text is never read out half-made
                c.live(so_far, turn=self.n)
        elif kind == "assistant":
            self.parts.append(kw.get("content") or "")
            self.written.clear()
        elif kind == "tool_call" and kw.get("name"):
            from .term_render import tool_words
            c.step(tool_words(kw["name"], kw.get("args")))

    def end(self) -> None:
        text = "".join(self.parts) or "".join(self.written)
        self.call.done(text, turn=self.n)


# What a spoken answer to an approval is, said on its own: the window takes only a
# clear answer, so "no, do it this way instead" still goes through as a message.
_ALWAYS = re.compile(r"^(always|always allow|yes always)$")
_ALLOW = re.compile(r"^(yes|yeah|yep|yup|sure|ok|okay|allow|do it|go ahead|confirm|yes please)$")
_DENY = re.compile(r"^(no|nope|nah|deny|don'?t|cancel|stop|no thanks)$")


def spoken_answer(text: str) -> str:
    """'always', 'allow', 'deny', or '' when the words aren't an answer."""
    t = re.sub(r"[.!?,]+$", "", (text or "").strip().lower()).strip()
    if _ALWAYS.match(t):
        return "always"
    if _ALLOW.match(t):
        return "allow"
    if _DENY.match(t):
        return "deny"
    return ""
