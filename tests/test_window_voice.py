"""Voice mode in the window is a call, the one the phone app makes: the page
opens the mic with the browser's echo cancelling, sends it to the PC as 16 kHz
frames over /api/voice/call, and plays the PC's voice back through a WebRTC
loop, which is what makes Chromium cancel its echo. Stand-ins for the
recognizer, the voice and the model, so it runs anywhere with Edge.

Checked in the real page: entering voice mode opens the mic with echo
cancelling (the mic picked in Settings, by name), plays through the loop,
streams frames the recognizer is fed, a heard sentence becomes a message whose
reply is spoken back into the page as it is written, talking over it stops the
voice on both ends and drops the reply, and leaving ends the call on the PC.
On the PC: only the window's own page may open the socket, a spoken yes
answers an approval only when Auto would allow the step, and a plain sentence
is handed back to the page as a message.

    .\\.venv\\Scripts\\python.exe tests\\test_window_voice.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-window-voice-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WORK
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

import numpy as np  # noqa: E402

from hearth import realtime_voice as rv, voice as V, voice_overlay as VO, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# While a test waits, the page's events still need passing on (sleeping instead
# holds every request the page makes until the next call into it).
PUMP = [time.sleep]


def wait_for(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        PUMP[0](0.05)
    try:
        return bool(cond())
    except Exception:
        return False


# ---------------------------------------------------------------- stand-ins
# A voice that reads every ten letters as 50 ms of a tone, so the page has something to hear.
read: list = []
V._load_engine = lambda allow_download=False: ("kokoro", None)


def _synth(kind, obj, chunk, voice, speed):
    read.append(chunk)
    n = int(24000 * 0.05 * max(1, len(chunk) / 10))
    return (np.sin(np.arange(n) * 2 * np.pi * 220 / 24000) * 9000).astype(np.int16), 24000


V._synth_chunk = _synth


class Recorder:
    """Stands in for the speech recognizer: counts what it is fed, says what the test hands it."""
    def __init__(self):
        self.q: list = []
        self.go = threading.Event()
        self.fed = 0
        self.rates: set = set()

    def feed_audio(self, data, original_sample_rate=16000):
        self.fed += len(data)
        self.rates.add(original_sample_rate)

    def text(self):
        self.go.wait()
        self.go.clear()
        return self.q.pop(0) if self.q else ""

    def clear_audio_queue(self):
        pass

    def shutdown(self):
        self.go.set()


built: list = []
rv._REALTIME_AVAILABLE = True
rv.model_downloaded = lambda size="": True
rv._build_recorder = lambda sess=None: built.append(Recorder()) or built[-1]


def hear(text):
    built[-1].q.append(text)
    built[-1].go.set()


hud: list = []
VO.start = lambda: hud.append("start")
VO.stop = lambda: hud.append("stop")

# The model: says a fixed reply in pieces, slowly enough to be talked over.
REPLY = ["It is noon here. ", "The sun is high and the day is long, ", "so there is plenty of time left. ",
         "Anything else you want to know?"]
turns: list = []


async def fake_run_once(prompt, emit=None, should_cancel=None, **kw):
    turns.append(prompt)
    written = ""
    for piece in REPLY:
        if should_cancel and should_cancel():
            emit("cancelled")
            return
        emit("assistant_chunk", content=piece)
        written += piece
        await asyncio.sleep(0.25)
    emit("assistant", content=written)
    emit("done", stats={})


web.run_once = fake_run_once
web._detect_loaded = lambda: {"id": "stand-in"}
web._save_settings(dict(web._load_settings(), onboarded=True, voice_mic_hint_dismissed=True, voice_window_call=True, ui_theme="dark", ui_accent="sage",
                        voice_input_name="Fake Default Audio Input", voice_half_duplex=True))
os.environ["HEARTH_VOICE_INPUT_NAME"] = "Fake Default Audio Input"

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{port}"

# ---------------------------------------------------------------- the socket, from the PC's side
from websockets.sync.client import connect  # noqa: E402

try:
    connect(f"ws://127.0.0.1:{port}/api/voice/call", origin="http://evil.example", open_timeout=5).close()
    refused = False
except Exception:
    refused = True
check("another site's page can't open voice mode's socket", refused)
with connect(f"ws://127.0.0.1:{port}/api/voice/call", origin=URL, open_timeout=5) as ws:
    hello = json.loads(ws.recv(timeout=5))
    check(f"the PC tells the page which mic to open, and what isn't a mic ({hello.get('name')!r})",
          hello.get("type") == "call.mic" and hello.get("name") == "Fake Default Audio Input"
          and "stereo mix" in hello.get("not_mics", []))

# ---------------------------------------------------------------- approvals said in the window
from hearth import call as C  # noqa: E402

sent: list = []
fake = C.Call(sent.append, web._window_said, cid="c1")
web._WIN_CALL = fake
q = web._queue_mod.Queue()
web._permission_queues["perm_1"] = q
web._WIN_ASK.update(id="perm_1", tap=True)
check("a spoken yes to a step only a click may allow asks for the click", web._window_said("yes") == "needs_tap" and q.empty())
check("a spoken no still answers it", web._window_said("No.") == "answered" and q.get_nowait() == "deny")
web._WIN_ASK.update(id="perm_1", tap=False)
check("a spoken yes answers one Auto would let run", web._window_said("go ahead") == "answered" and q.get_nowait() == "allow")
web._WIN_ASK.update(id="perm_1", tap=False)
said = web._window_said("no, use the other folder")
ans = q.get_nowait() if not q.empty() else None
check("a sentence that isn't an answer goes to the brain as what they said, never as a no",
      said == "aside" and isinstance(ans, dict) and ans.get("decision") == "aside"
      and ans.get("reason") == "no, use the other folder")
web._permission_queues.pop("perm_1", None)
web._WIN_ASK.clear()
web._WIN_CALL = None
fake.ended = True

# ---------------------------------------------------------------- the page
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped the page: playwright is not installed")
    sys.exit(1 if FAILS else 0)

WATCH = """
window.__gum = [];
window.__pcs = 0;
const _gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = (c) => { window.__gum.push(JSON.parse(JSON.stringify(c))); return _gum(c); };
const _PC = window.RTCPeerConnection;
window.RTCPeerConnection = function (...a) { window.__pcs++; return new _PC(...a); };
window.RTCPeerConnection.prototype = _PC.prototype;
"""

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge", args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required"])
    except Exception as e:
        print(f"skipped the page: no browser ({type(e).__name__})")
        srv.shutdown()
        sys.exit(1 if FAILS else 0)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.grant_permissions(["microphone"], origin=URL)
    pg = ctx.new_page()
    pg.add_init_script(WATCH)
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    PUMP[0] = lambda secs: pg.wait_for_timeout(secs * 1000)
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL + "/", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    # A model counts as loaded (the window polls it, so set in the same moment as the click).
    pg.evaluate("""() => {
      window.warmUpBrainForVoice = async () => true;
      STATE.loadedModel = { id: 'stand-in' };
      $('btn-voice-mode').click();
    }""")
    check("voice mode starts a call from the page", wait_for(lambda: pg.evaluate("() => !!winCall && winCall.live"), 15))
    gum = pg.evaluate("() => window.__gum")
    check(f"the page opened the mic with echo cancelling, noise suppression and gain control ({len(gum)} opens)",
          gum and all(g.get("audio", {}).get("echoCancellation") is True and g["audio"].get("noiseSuppression") is True
                      and g["audio"].get("autoGainControl") is True for g in gum))
    check("the mic picked in Settings, by name", pg.evaluate("() => winCall.A.label()") == "Fake Default Audio Input")
    pg.wait_for_timeout(300)
    check("and no word of a fallback when it is the one in use",
          not pg.evaluate("() => [...document.querySelectorAll('.toast')].some(t => /fell back/i.test(t.textContent))"))
    check("the voice plays through a WebRTC loop into the audio element",
          pg.evaluate("() => winCall.A.aec && window.__pcs >= 2 && $('voice-audio').srcObject instanceof MediaStream"))
    check("the PC's call is on, its HUD shown", wait_for(lambda: web.window_call() is not None) and "start" in hud)
    check("the call listens, and the page says so", wait_for(lambda: pg.evaluate(
        "() => winCall.ready && $('voice-status-text').textContent === 'listening'"), 10))
    check("the page's mic reaches the recognizer as 16 kHz frames",
          wait_for(lambda: built and built[-1].fed > 16000 and built[-1].rates == {16000}))
    pg.evaluate("() => { window.__downs = 0; const d = winCall.A.down; winCall.A.down = (m) => { window.__downs++; d(m); }; }")
    pg.evaluate("() => { window.__tts = 0; const f = window.fetch; window.fetch = (u, o) => { if (String(u).includes('/api/tts')) window.__tts++; return f(u, o); }; }")

    hear("what time is it")
    check("a heard sentence becomes a message in the chat", wait_for(lambda: turns == ["what time is it"], 10))
    check("its reply is spoken as it is written, from the first clause",
          wait_for(lambda: read and read[0].startswith("It is noon"), 6))
    check("and played in the page", wait_for(lambda: pg.evaluate("() => window.__downs") > 5, 6))
    check("the window shows it speaking", wait_for(lambda: pg.evaluate("() => _voiceState === 'speaking'"), 6))
    check("nothing went to the PC's speakers", pg.evaluate("() => window.__tts") == 0)

    # Talking over it: the recognizer hears speech while the voice is playing.
    call = web.window_call()
    before = len(read)
    sess = rv.session_for(call.lease)
    sess.on_barge()
    check("talking over it stops the voice on the PC", wait_for(lambda: not call.speaker.busy(), 3))
    check("and in the page, with the reply being written dropped",
          wait_for(lambda: pg.evaluate("() => !winCall.A.playing() && !STATE.streaming"), 3))
    PUMP[0](1.2)
    check(f"nothing more of that reply is said ({len(read) - before} pieces after)", len(read) - before <= 1)

    hear("never mind")
    check("what was said over it goes as the next message", wait_for(lambda: turns[-1:] == ["never mind"], 10))
    check("and its reply is spoken", wait_for(lambda: len(read) > before + 1, 8))

    pg.screenshot(path=os.path.join(WORK, "voice.png"))
    pg.keyboard.press("Escape")
    check("leaving voice mode ends the call on the PC", wait_for(lambda: web.window_call() is None and call.ended, 5))
    check("and lets go of the listener and the HUD", wait_for(lambda: rv._session is None and hud[-1:] == ["stop"], 5))
    check("and of the mic in the page", pg.evaluate("() => winCall === null"))

    # The mic is chosen as the PC chooses it: never system sound, a missing pick said out loud.
    picks = pg.evaluate("""async () => {
      const hints = ['stereo mix', 'loopback'];
      const out = {};
      for (const [k, name] of [['gone', 'Headset (Not Here)'], ['loop', 'Stereo Mix (Realtek(R) Audio)']]) {
        const A = CallAudio.make();
        try { out[k] = { note: await winCallMic(A, name, hints), label: A.label() }; }
        catch (e) { out[k] = { why: e.why || String(e) }; }
        A.close();
      }
      return out;
    }""")
    check(f"a picked mic that isn't connected falls back to the default input and says so ({picks['gone']})",
          "is not connected, so voice is using the Windows default input" in (picks["gone"].get("note") or ""))
    check(f"system sound picked as the mic is refused with the reason ({picks['loop']})",
          "is not a microphone" in (picks["loop"].get("why") or ""))
    # Windows' own privacy switch keeps the mic from the window: one line, and its settings a click away.
    opened: list = []
    pg.route("**/api/voice/mic-privacy", lambda r: (opened.append(r.request.method), r.fulfill(json={"ok": True})))
    pg.evaluate("""() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('blocked', 'NotAllowedError'));
      STATE.loadedModel = { id: 'stand-in' };
      $('btn-voice-mode').click();
    }""")
    blocked = wait_for(lambda: pg.evaluate("() => $('voice-status-text').textContent") == "Windows is blocking the microphone", 10)
    check("a mic Windows blocks says so in one line", blocked if sys.platform == "win32" else True)
    if sys.platform == "win32":
        check("with a button to Windows' microphone settings", pg.evaluate("() => !$('voice-fix').hidden"))
        shots = os.environ.get("HEARTH_SHOT_DIR")
        if shots:
            pg.wait_for_timeout(600)                 # the voice screen's fade-in
            pg.screenshot(path=os.path.join(shots, "dark-windows-blocks-mic.png"))
        pg.click("#voice-fix-open")
        check("which opens them", wait_for(lambda: opened == ["POST"], 3))
        check("and no call was left on", web.window_call() is None)
    pg.keyboard.press("Escape")
    check(f"no page errors ({errors[:2]})", not errors)
    browser.close()

srv.shutdown()
print("\n" + ("FAILED: " + "; ".join(FAILS) if FAILS else "all window voice checks passed"))
sys.exit(1 if FAILS else 0)
