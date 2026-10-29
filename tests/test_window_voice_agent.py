"""Voice mode in the window with an agent program as the brain (OpenCode, Claude
Code...), through the real turn path: the agent is told the turn is spoken
even in a session it began typing in, its reply streams out as speech while it
is still being written (audio reaches the page before the reply ends), a
socket the PC cuts mid-reply doesn't end the call (the page takes the same
call back, its listener still warm, and the reply goes on being spoken), and
the chat is named like a typed one. Stand-ins for the recognizer, the voice
and the agent; the page in Edge.

    .\\.venv\\Scripts\\python.exe tests\\test_window_voice_agent.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-window-voice-agent-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WORK
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

import numpy as np  # noqa: E402

from hearth import claude_brain as CB, realtime_voice as rv, voice as V, voice_overlay as VO, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


V._load_engine = lambda allow_download=False: ("kokoro", None)


def _synth(kind, obj, chunk, voice, speed):
    n = int(24000 * 0.05 * max(1, len(chunk) / 10))
    return (np.sin(np.arange(n) * 2 * np.pi * 220 / 24000) * 6000).astype(np.int16), 24000


V._synth_chunk = _synth
built: list = []


class Recorder:
    def __init__(self):
        self.q, self.go, self.fed = [], threading.Event(), 0

    def feed_audio(self, data, original_sample_rate=16000):
        self.fed += len(data)

    def text(self):
        self.go.wait(); self.go.clear()
        return self.q.pop(0) if self.q else ""

    def clear_audio_queue(self):
        pass

    def shutdown(self):
        self.go.set()


rv._REALTIME_AVAILABLE = True
rv.model_downloaded = lambda size="": True
rv._build_recorder = lambda sess=None: built.append(Recorder()) or built[-1]
VO.start = VO.stop = lambda: None

# The agent: streams its reply a few words at a time, slowly, the way a model writes.
PIECES = ["Three hearts, ", "two of which ", "stop while it swims. ", "Its blood is blue, ", "from copper. ",
          "That's why ", "it would rather ", "crawl than swim."]
asked: list = []
ended: dict = {}


async def agent_turn(prompt, *, emit, voice=False, **kw):
    asked.append((prompt, voice))
    said = ""
    for p in PIECES:
        await asyncio.sleep(0.35)
        emit("assistant_chunk", content=p)
        said += p
    ended.setdefault(len(asked), time.time())
    emit("assistant", content=said)
    emit("done", stats={})
    return 0


CB.active = lambda: True
CB.run_turn = agent_turn
web._save_settings(dict(web._load_settings(), onboarded=True, voice_mic_hint_dismissed=True))

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{port}"


def wait_for(cond, secs=8.0, pg=None):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        if pg is not None:
            pg.wait_for_timeout(50)
        else:
            time.sleep(0.05)
    try:
        return bool(cond())
    except Exception:
        return False


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge", args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required"])
    except Exception as e:
        print(f"skipped: no browser ({type(e).__name__})")
        sys.exit(0)
    ctx = browser.new_context(viewport={"width": 1180, "height": 780})
    ctx.grant_permissions(["microphone"], origin=URL)
    pg = ctx.new_page()
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL + "/", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(3000)
    # The brain counts as ready for the whole test (the window's model poll would clear a stand-in).
    pg.evaluate("""() => { window.warmUpBrainForVoice = async () => true;
                   Object.defineProperty(STATE, 'loadedModel', { get: () => ({ id: 'stand-in' }), set: () => {}, configurable: true });
                   $('btn-voice-mode').click(); }""")
    check("voice mode is on, as a call", wait_for(lambda: pg.evaluate("() => !!winCall && winCall.ready"), 20, pg))
    pg.evaluate("""() => { window.__first = 0; const d = winCall.A.down;
                   winCall.A.down = (m) => { if (!window.__first) window.__first = Date.now(); d(m); }; }""")

    built[-1].q.append("tell me about octopus hearts"); built[-1].go.set()
    check("the sentence reaches the agent", wait_for(lambda: len(asked) == 1, 10, pg))
    check("told it is a spoken turn (the agent's session may have begun typed)",
          bool(asked) and asked[0][1] is True and "Voice mode" in asked[0][0] and "spoken aloud" in asked[0][0])
    check("the reply is heard in the page", wait_for(lambda: pg.evaluate("() => window.__first") > 0, 10, pg))
    wait_for(lambda: 1 in ended, 10, pg)
    first = pg.evaluate("() => window.__first") / 1000.0
    lead = ended.get(1, 0) - first
    check(f"and it starts while the agent is still writing it ({lead:.2f} s before the reply ended)", lead > 1.0)

    # Something on the PC cuts the socket mid-reply: the same call carries on.
    wait_for(lambda: not pg.evaluate("() => STATE.streaming"), 15, pg)
    pg.wait_for_timeout(1500)
    call, lease, recs = web.window_call(), web.window_call().lease, len(built)
    built[-1].q.append("and what about their arms"); built[-1].go.set()
    check("the next sentence reaches the agent", wait_for(lambda: len(asked) == 2, 10, pg))
    wait_for(lambda: pg.evaluate("() => winCall.A.playing()"), 8, pg)
    pg.evaluate("() => winCall.ws.close()")
    check("the page takes the same call back on a new socket",
          wait_for(lambda: pg.evaluate("() => !!winCall && winCall.ws.readyState === 1"), 8, pg)
          and web.window_call() is call and call.lease == lease and not call.ended)
    check("its listener was never rebuilt", len(built) == recs and rv.session_for(lease) is not None)
    pg.evaluate("() => { window.__after = 0; const d = winCall.A.down; winCall.A.down = (m) => { window.__after++; d(m); }; }")
    check("and the reply goes on being heard", wait_for(lambda: pg.evaluate("() => window.__after") > 3, 8, pg))

    # A voice chat is named like a typed one.
    wait_for(lambda: not pg.evaluate("() => STATE.streaming"), 15, pg)
    title = ""
    end = time.time() + 40
    while time.time() < end:
        title = pg.evaluate("() => (activeConvo() || {}).title || ''")
        if title and title != "New chat":
            break
        pg.wait_for_timeout(250)
    check(f"the voice chat gets a title, not 'New chat' ({title!r})", bool(title) and title != "New chat")

    check("Settings can send voice mode back to the PC's own mic",
          pg.evaluate("() => { STATE.settings.voice_window_call = false; const off = !winCallCan(); STATE.settings.voice_window_call = true; return off && winCallCan(); }"))
    pg.keyboard.press("Escape")
    check("leaving ends the call on the PC", wait_for(lambda: web.window_call() is None and call.ended, 5, pg))
    check(f"no page errors ({errors[:2]})", not errors)
    browser.close()

srv.shutdown()
print("\n" + ("FAILED: " + "; ".join(FAILS) if FAILS else "all window voice agent checks passed"))
sys.exit(1 if FAILS else 0)
