"""A call from the phone, on the PC: the phone's audio frames reach the speech
recognizer (not this PC's mic), a finished sentence becomes a message in the
call's chat, the reply is spoken into audio frames for the phone as it is
written (from the first clause), talking over JARVIS cuts it on both ends, and
a spoken "yes" approves only what Auto mode would let run anyway.

    .\\.venv\\Scripts\\python.exe tests\\test_call_session.py
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-call-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import call as C, channel_core as cc, realtime_voice as rv, tools as T, voice as V  # noqa: E402

# The voice model files stay where Hearth keeps them, outside the test's workspace.
_home_voices = os.path.join(os.path.expanduser("~"), "Jarvis", "voices")
if os.path.isdir(_home_voices):
    V.VOICES_DIR = _home_voices

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# ---------------------------------------------------------------- spoken approvals
check("deleting needs a tap", T.needs_a_tap("delete_path", {"path": "x"}))
check("a destructive command needs a tap", T.needs_a_tap("run_command", {"command": "rm -rf build"}))
check("a push needs a tap", T.needs_a_tap("run_command", {"command": "git push origin main"}))
check("an ordinary command can be said yes to", not T.needs_a_tap("run_command", {"command": "dir"}))
check("so can opening an app", not T.needs_a_tap("open_app", {"name": "chrome"}))


class Quiet(cc.Adapter):
    name, label = "test", "Test"

    def send(self, chat, text, reply_to=None):
        return None


hub = cc.Hub(Quiet(), configured_senders=["phone"])
for tap, said, spoken, want in [(True, "yes", True, "needs_tap"), (True, "yes", False, "answered"),
                                (True, "no", True, "answered"), (False, "yeah go ahead", True, "answered")]:
    ev = threading.Event()
    hub._busy["k"] = {"steer": [], "question": ev, "answer": None, "key": "k", "tap": tap}
    got = hub.receive("chat1", "phone", said, cid="k", spoken=spoken)
    what = f"{'spoken' if spoken else 'tapped'} '{said}' on {'a delete' if tap else 'an ordinary step'}"
    check(f"{what} -> {got}{' (allowed)' if hub._busy['k']['answer'] else ''}", got == want
          and (want != "answered" or hub._busy["k"]["answer"] == (said != "no")))
hub._busy.clear()

# ---------------------------------------------------------------- a call, start to cut
if V._load_engine() is None:
    print("skipped the call itself: no voice model on this PC")
else:
    fed, heard_q = [], []

    class Recorder:
        """Stands in for the speech recognizer: hears a sentence once audio has been fed."""
        def __init__(self):
            self.go = threading.Event()

        def feed_audio(self, data, original_sample_rate=16000):
            fed.append((len(data), original_sample_rate))
            if len(fed) >= 3:
                self.go.set()

        def text(self):
            self.go.wait()
            self.go.clear()
            return heard_q.pop(0) if heard_q else ""

        def clear_audio_queue(self):
            pass

        def shutdown(self):
            self.go.set()

    built = []
    rv._REALTIME_AVAILABLE = True
    rv._build_recorder = lambda sess=None: built.append(sess) or Recorder()
    sent, said = [], []

    call = None

    def say(text):
        said.append(text)
        # The reply as the turn writes it: a clause, then the rest.
        def write():
            reply = "Sure, opening Chrome for you now. It should be on your screen in a second."
            for n in (12, 34, len(reply)):
                call.live(reply[:n])
                time.sleep(0.15)
            call.done(reply)
        threading.Thread(target=write, daemon=True).start()
        return "ran"

    call = C.Call(sent.append, say, cid="c_call")
    t0 = time.time()
    call.start()
    while not any(f.get("state") == "listening" for f in sent if f.get("type") == "call.state") and time.time() - t0 < 10:
        time.sleep(0.05)
    check("the call listens once its recognizer is up", any(f.get("state") == "listening" for f in sent))
    check("to audio fed from the phone, not this PC's mic", built and built[0].fed and built[0].speaker is call.speaker)
    heard_q.append("open chrome for me")
    frame = base64.b64encode(b"\x00\x01" * 960).decode()          # 60 ms at 16 kHz
    for _ in range(4):
        call.up(frame, 16000)
    while not said and time.time() - t0 < 10:
        time.sleep(0.05)
    check(f"the phone's frames reach the recognizer at 16 kHz ({fed[:1]})", fed and fed[0] == (1920, 16000))
    check(f"a finished sentence becomes a message in the call's chat ({said})", said == ["open chrome for me"])
    check("and the phone is shown what was heard", any(f.get("type") == "call.heard" and f.get("text") == "open chrome for me"
                                                       for f in sent))
    while not any(f.get("type") == "call.down" for f in sent) and time.time() - t0 < 30:
        time.sleep(0.05)
    first = next((f for f in sent if f.get("type") == "call.down"), None)
    check("the reply comes back as audio frames for the phone", first is not None and first["sr"] > 0)
    spoken = V.DEFAULT.recently_spoke_text()
    check("from the call's own speaker, not this PC's", "opening chrome" in call.speaker.recently_spoke_text()
          and "opening chrome" not in spoken)
    time.sleep(0.4)
    n_before = sum(1 for f in sent if f.get("type") == "call.down")
    call._barge()
    time.sleep(0.8)
    n_after = sum(1 for f in sent if f.get("type") == "call.down")
    check(f"talking over JARVIS stops the audio and clears the phone ({n_before} -> {n_after} frames)",
          n_after <= n_before + 1 and any(f.get("type") == "call.flush" for f in sent))
    call.end()
    check("hanging up ends the session", call.ended and any(f.get("type") == "call.ended" for f in sent)
          and rv.session_for(call.lease) is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all call session checks passed")
