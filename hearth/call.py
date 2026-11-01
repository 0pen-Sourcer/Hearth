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
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import env as _env
from . import realtime_voice as _rv
from . import voice as _voice

# Where a reply may be cut to start speaking: a sentence end, or for the first
# piece a clause once it is long enough to sound whole.
# A danda (।) ends a Hindi sentence and is never part of a number, so it needs no space after.
_SENTENCE = re.compile(r"[.!?]+[\"')\]]*\s|[।॥]+[\"')\]]*|\n")
_CLAUSE = re.compile(r"[,;:]\s|\s[—–-]\s|—")
_BREAK = re.compile(r"[,;:–-]\s|\s[—–-]\s|—")
FIRST_CLAUSE = 24
QUICK_START = 12
# A first piece with no break in it yet is cut at a space once it is this long. With a
# local model on the card the voice takes about 25 ms a letter, so a 60-letter opening
# took 1.5-3 s to make before anything was heard; the rest is made while it plays.
LONG_FIRST = 40

TAP_LINE = "That one needs a tap on Allow."
# Speech model sizes voice mode's sheet offers (and their English-only kind).
STT_MODELS = ("tiny", "base", "small", "tiny.en", "base.en", "small.en")


def _brain_label() -> str:
    """What thought up the reply, for the timing log: an agent program, or the model and where."""
    try:
        from . import claude_brain as _cb
        if _cb.current():
            return _cb.current()
    except Exception:
        pass
    try:
        from . import headless as _hl
        from urllib.parse import urlparse
        base = _hl.LOCAL_API_BASE or ""
        host = (urlparse(base).hostname or "").lower()
        import os
        model = os.path.basename((os.environ.get("LOCAL_MODEL") or "").strip())
        where = "local" if host in ("127.0.0.1", "localhost", "::1", "") else host
        return f"{where} {model}".strip()
    except Exception:
        return ""


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
        self.times: Dict[str, float] = {}     # this turn's stages, for the timing log

    # ---------------------------------------------------------------- the timing log
    def note(self, stage: str) -> None:
        """The first time a stage of the turn happens (first_token, first_audio, ...)."""
        if self.times and stage not in self.times:
            self.times[stage] = time.time()

    def _log_turn(self) -> None:
        """One line per spoken turn in logs/voice_turns.log: each stage in seconds after the
        recognizer heard the speech end, so a slow turn says where the time went."""
        t, self.times = self.times, {}
        end = t.get("speech_end")
        if not end or "final" not in t:
            return
        row: Dict[str, Any] = {"at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end)),
                               "stt": t.get("stt", ""), "brain": _brain_label(),
                               "surface": "window" if self.quick else "phone"}
        for k in ("final", "posted", "first_token", "first_audio", "done"):
            if k in t:
                row[k] = round(t[k] - end, 3)
        if "barge_start" in t and "barge_stop" in t:
            row["barge_stop"] = round(t["barge_stop"] - t["barge_start"], 3)
        try:
            from .tools import WORKSPACE
            import json
            import os
            folder = os.path.join(str(WORKSPACE), "logs")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "voice_turns.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---------------------------------------------------------------- the voice out
    def _sink(self):
        call = self

        class Sink:
            def __call__(self, samples, sr):
                call.note("first_audio")
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
        sess = _rv.session_for(self.lease)
        return {"type": "call.voice", "voice": _voice.current_voice(), "voices": voices(),
                "language": (_env.get("STT_LANGUAGE", "") or "").strip().lower(), "languages": languages(),
                "stt_device": (_env.get("STT_DEVICE", "") or "cpu").strip().lower(),
                "stt_model": (_env.get("STT_MODEL", "") or "").strip(),
                "listening": getattr(sess, "stt", "") if sess is not None else ""}

    def set(self, voice: Optional[str] = None, language: Optional[str] = None,
            stt_device: Optional[str] = None, stt_model: Optional[str] = None) -> None:
        """The voice it answers in, the language it listens in, or where and with which
        model it listens, changed from the call; listening starts again for the last three."""
        changed: Dict[str, Any] = {}
        if voice and voice in _voice.list_voices() and voice != _voice.current_voice():
            _voice.set_default_voice(voice)
            _env.set("VOICE", voice)
            changed["voice_name"] = voice
        codes = {x["code"] for x in languages()}
        if language is not None and language in codes and language != (_env.get("STT_LANGUAGE", "") or "").strip().lower():
            _env.set("STT_LANGUAGE", language)
            changed["stt_language"] = language
        if stt_device in ("cuda", "gpu", "cpu") and stt_device != (_env.get("STT_DEVICE", "") or "cpu").lower():
            _env.set("STT_DEVICE", stt_device)
            changed["stt_device"] = stt_device
        if stt_model in STT_MODELS and stt_model != (_env.get("STT_MODEL", "") or ""):
            _env.set("STT_MODEL", stt_model)
            changed["stt_model"] = stt_model
        if changed and self.save is not None:
            try:
                self.save(changed)
            except Exception:
                pass
        self.send(self.voice_info())
        if ({"stt_language", "stt_device", "stt_model"} & set(changed)) and self.lease is not None and not self.ended:
            # The recognizer is built for one language, model and device: listen again with the new one.
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
        self.send(self.voice_info())            # what listens now, for the settings sheet
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
        if self.times:
            sess = _rv.session_for(self.lease)
            self.times["barge_start"] = (sess.speech_started_at if sess is not None and sess.speech_started_at
                                         else time.time())
            self.times["barge_stop"] = time.time()
            self._log_turn()

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
        self.note("posted")
        return n

    def _heard(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self.times:
            self._log_turn()                    # the last turn, if it never settled
        sess = _rv.session_for(self.lease)
        now = time.time()
        stopped = sess.speech_stopped_at if sess is not None else 0.0
        self.times = {"speech_end": stopped if stopped and now - stopped < 30 else now, "final": now,
                      "stt": getattr(sess, "stt", "") if sess is not None else ""}
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
            # The card on the phone and the question in the chat both show the answer.
            self.send({"type": "asked", "ask": self.ask_id, "yes": spoken_answer(text) in ("allow", "always"),
                       **({"c": self.cid} if self.cid else {})})
        elif got == "aside":
            # Said something else while a card waited: the card goes, the words go to the brain.
            self.send({"type": "asked", "ask": self.ask_id, "aside": True, **({"c": self.cid} if self.cid else {})})
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
                first = self.spoken == 0
                if first and cut > LONG_FIRST:
                    cut = 0                             # a long opening sentence: start with its first part
                if not cut and first and self.quick and len(rest) >= QUICK_START:
                    # The first break past a dozen letters: a reply that arrives in one
                    # piece starts with its opening clause, not with all of it.
                    ends = [c.end() for c in _BREAK.finditer(rest) if QUICK_START <= c.end() <= LONG_FIRST]
                    cut = ends[0] if ends else 0
                if not cut and first and len(rest) >= LONG_FIRST:
                    sp = rest.rfind(" ", QUICK_START, LONG_FIRST)
                    cut = sp + 1 if sp > 0 else (m.end() if m else 0)
                    if m and 0 < m.end() - cut < QUICK_START:
                        cut = m.end()                   # not a word or two left on its own

                if not cut and first and len(rest) >= FIRST_CLAUSE:
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
            if n == self.heard_n and "posted" in self.times:
                self.note("done")
                self._log_turn()
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

    def ask_quietly(self, tap: bool = False, aid: str = "") -> None:
        """A card the turn waits on, shown and not read out: the brain says what it
        wants in its own words; a clear spoken yes or no answers the card."""
        self.ask_id = aid
        self.send({"type": "call.state", "state": "asking", "tap": bool(tap), "ask": aid})

    def _speak(self, text: str) -> None:
        self.send({"type": "call.state", "state": "speaking"})
        self.speaker.speak(_voice._clean_for_tts(text))


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
            c.note("first_token")
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


# What a spoken answer to a question it asked is: the whole sentence is a yes or a no,
# made of answer words only ("yes", "yeah, go ahead", "no, you may not"). Anything
# else ("great", "how do I...", "no, do it this way instead") is a message.
_YES_WORDS = (r"yes|yeah|yea|yep|yup|sure|ok|okay|alright|all right|allow it|allow|confirm|fine|"
              r"go ahead|go for it|do it|do that|run it|please do|please|sounds good|that's fine|that's okay|"
              r"you may|you can|of course|absolutely|definitely|thanks|thank you")
_NO_WORDS = (r"no|nope|nah|deny|don't|do not|dont|cancel|stop|never mind|not now|no thanks|"
             r"you may not|you can't|you cannot|don't do that|do not do that|leave it|skip it|please|thanks|thank you")
_ALWAYS = re.compile(r"^(always|always allow|yes always|yes,? always)$")
_ALLOW = re.compile(rf"^(?:{_YES_WORDS})(?: (?:{_YES_WORDS}))*$")
_DENY = re.compile(rf"^(?:no|nope|nah|deny|don't|do not|dont|cancel|stop|never mind|not now)(?: (?:{_NO_WORDS}))*$")


def spoken_answer(text: str) -> str:
    """'always', 'allow', 'deny', or '' when the words aren't an answer."""
    t = (text or "").strip().lower().replace("’", "'")
    t = re.sub(r"[.!?,;:]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t or len(t.split()) > 8:
        return ""
    if _ALWAYS.match(t):
        return "always"
    if _ALLOW.match(t):
        return "allow"
    if _DENY.match(t):
        return "deny"
    return ""


# A risky step in a voice turn: it isn't asked on screen from a template; the brain
# says what it is about to do in its own words and waits for the answer.
ASK_ALOUD = ("Not run yet, and nothing changed: you're talking with them by voice. In one short plain "
             "sentence, say what you're about to do and ask if that's okay (for example \"I'll check "
             "your CPU and GPU usage with a command, okay?\"), then stop and wait for their answer. "
             "Run it once they say yes.")
ASK_TAP = ("Not run yet, and nothing changed: this step can only be allowed with a tap on Allow on "
           "their screen, never by voice. In one short plain sentence, say what you're about to do "
           "and that it needs their tap on Allow, then stop.")


def aside_note(said: str) -> str:
    """What the brain hears when something else was said while it waited for an OK."""
    return (f"Not run yet: while you waited for their OK they said something else: \"{said}\". "
            f"That is not an answer to the step. Reply to what they said. If the step is still "
            f"needed, ask about it again in one plain sentence.")


def asked_last(history) -> bool:
    """Whether the brain's last reply in the chat asked them something."""
    for m in reversed(list(history or [])):
        if (m or {}).get("role") == "assistant":
            c = m.get("content")
            return isinstance(c, str) and "?" in c[-300:]
        if (m or {}).get("role") == "user":
            continue
    return False


def step_key(name: str, args: Any) -> str:
    try:
        return name + ":" + json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        return name + ":" + str(args)


def brain_says_why() -> bool:
    """Whether the brain hears the words given with a step it wasn't let do. Codex's
    own approvals and the ACP agents only hear yes or no."""
    try:
        from . import claude_brain as _cb
        cur = _cb.current()
    except Exception:
        return True
    return cur in (None, "claude-code", "cursor-agent")


def voice_permission_check(check, emit_fn, prompt: str, history, *, asks, tapped,
                           always_allow=(), always_deny=(), says_why=None) -> Any:
    """The permission check for a voice turn. Nothing is read out from a template:
    a step the approval mode lets run, runs (Auto never asks about looking at
    things); one they just said a clear yes to, after the brain asked, runs unless
    only a tap may allow it; any other step goes back to the brain to say what it is
    about to do and ask. A brain that can't hear why waits on the card instead."""
    from . import tools as _tl
    yes = spoken_answer(prompt) in ("allow", "always") and asked_last(history)

    def _check(name: str, args: Dict) -> Any:
        if name in always_deny:
            return "deny"
        try:
            if _tl.mode_allows(name, args) or (name in always_allow and not _tl.sends_out(name, args)):
                return "allow"
            tap = _tl.needs_a_tap(name, args)
        except Exception:
            tap = True
        key = step_key(name, args)
        if key in tapped:
            tapped.discard(key)
            return "allow"
        if yes and not tap:
            return "allow"
        if not (says_why or brain_says_why)():
            return check(name, args)
        rid = f"perm_{int(time.time() * 1000)}_{name}"
        asks[rid] = {"name": name, "key": key, "tap": tap, "at": time.time()}
        for old in [k for k, v in asks.items() if time.time() - v["at"] > 600]:
            asks.pop(old, None)
        from .approval import card as _card
        _c = _card(name, args)
        emit_fn("permission_request", id=rid, name=name, args=args, aloud=True, head=_c["head"], what=_c["what"],
                detail=_c["detail"], **({"tap": True} if tap else {}))
        return {"decision": "ask_aloud", "reason": ASK_TAP if tap else ASK_ALOUD}
    return _check

