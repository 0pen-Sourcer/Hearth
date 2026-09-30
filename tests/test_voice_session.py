"""Voice sessions: each one ends cleanly, and an ending one cannot touch the next.

Turning voice off called the recorder's stop(), which only pauses it. Its mic
reader and transcription processes stayed alive, measured at two processes and
about 785 MB per session with the smallest model, each still holding the mic.
Everything about a session was also module-wide, so reopening voice within a
few seconds let the old loop clear-flag its way back to life and the old
stream's cleanup stop the new session. That is "voice won't turn on after I
change something".

A stand-in recorder replaces RealtimeSTT, so this runs without a mic.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_session.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import realtime_voice as rv                              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Recorder:
    built: list = []

    def __init__(self):
        self.down = threading.Event()
        self.shutdowns = 0
        Recorder.built.append(self)

    def text(self):
        self.down.wait()          # blocks like the real one until shut down
        raise RuntimeError("recorder is shut down")

    def clear_audio_queue(self):
        pass

    def stop(self):
        pass                      # pausing only, like the real one

    def shutdown(self):
        self.shutdowns += 1
        self.down.set()


rv._REALTIME_AVAILABLE = True
rv._build_recorder = lambda sess=None: Recorder()


def wait(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ---------------------------------------------------------- ending a session
ready = threading.Event()
a = rv.start_continuous(lambda t: None, on_ready=ready.set)
check("a session starts and reports ready", ready.wait(3) and rv.is_listening())
rec_a = Recorder.built[-1]
rv.stop_continuous(a)
check("stopping it shuts the recorder down, releasing mic and model",
      wait(lambda: rec_a.shutdowns >= 1))
check("and it is no longer listening", wait(lambda: not rv.is_listening()))

# -------------------------------------------- a late stop from a closed stream
b = rv.start_continuous(lambda t: None)
wait(rv.is_listening)
rec_b = Recorder.built[-1]
check("a stale lease cannot stop the session that replaced it",
      rv.stop_continuous(a) is False)
time.sleep(0.3)
check("so the new session keeps listening", rv.is_listening() and rec_b.shutdowns == 0)

# ------------------------------------------ reopening while one is still running
got = []
ready_c = threading.Event()
c = rv.start_continuous(got.append, on_ready=ready_c.set)
check("reopening hands over the running session, no second recorder",
      Recorder.built[-1] is rec_b and ready_c.wait(1))
check("the new caller owns it and the old one does not", rv.owns(c) and not rv.owns(b))
check("the old owner's cleanup leaves it running",
      rv.stop_continuous(b) is False and rv.is_listening())
rv.stop_continuous(c)
check("the owner can stop it", wait(lambda: rec_b.shutdowns >= 1 and not rv.is_listening()))

# ------------------------------------- a dying session cannot end the next one
d = rv.start_continuous(lambda t: None)
wait(rv.is_listening)
rec_d = Recorder.built[-1]
rv.stop_continuous(d)
e = rv.start_continuous(lambda t: None)       # reopened at once
wait(rv.is_listening)
time.sleep(0.5)                               # the old loop errors out meanwhile
check("the previous session winding down does not stop the new one",
      rv.owns(e) and rv.is_listening() and rec_d.shutdowns >= 1)
rv.stop_continuous(e)

# ------------------------------------------------------ the window's streams
# Leave voice, change a setting, come back: the first stream's connection is
# gone but its handler only notices on its next write, and its cleanup ran
# after the second stream had started.
import http.client                                                   # noqa: E402
import json                                                          # noqa: E402
import socket                                                        # noqa: E402
from http.server import ThreadingHTTPServer                          # noqa: E402

os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
from hearth import web, voice_overlay                                # noqa: E402

voice_overlay.start = voice_overlay.stop = lambda *a, **k: None      # no desktop HUD
web._rt_voice = rv


class _Quiet(web.HearthHandler):
    def log_message(self, *_a, **_kw):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def open_stream():
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    c.request("GET", "/api/voice/realtime/stream", headers={"Host": f"127.0.0.1:{PORT}"})
    r = c.getresponse()
    return c, r


def until(r, kind, secs=10):
    end = time.time() + secs
    while time.time() < end:
        line = r.fp.readline()
        if not line:
            return False
        if json.loads(line).get("type") == kind:
            return True
    return False


ca, ra = open_stream()
check("the first stream reaches ready", until(ra, "ready"))
ra.close()
ca.close()                                    # the window left voice mode
cb, rb = open_stream()
check("the second stream reaches ready", until(rb, "ready"))
time.sleep(17)                                # past the old stream's heartbeat
check("after the first stream's handler has given up, voice is still on",
      rv.is_listening())
rb.fp.raw._sock.settimeout(1.5)
try:
    tail = rb.fp.readline()
except socket.timeout:
    tail = b"(still open)"
check(f"and the second stream is still open, not told it stopped: {tail[:60]!r}",
      tail == b"(still open)" or json.loads(tail).get("type") == "heartbeat")
built = len(Recorder.built)
c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
c.request("POST", "/api/voice/realtime/stop", body=b"{}",
          headers={"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json"})
c.getresponse().read()
check("turning voice off shuts the recorder down",
      wait(lambda: Recorder.built[-1].shutdowns >= 1 and not rv.is_listening(), 5))
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice session checks passed")
