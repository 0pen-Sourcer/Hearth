"""Voice mode in the real Inglen window (pywebview + WebView2) gets the mic
without the web view's own permission bubble: the window grants it to its own
page, and only the mic. Opened with no click, the way the wake word opens it,
the voice plays through the echo-cancelled loop, the page's frames reach the
PC, and the screen wears the saved look. Windows only; it opens a window for
a few seconds. Stand-ins for the recognizer, voice and model.

    .\\.venv\\Scripts\\python.exe tests\\test_window_mic_grant.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

if sys.platform != "win32":
    print("skipped: the WebView2 window is Windows only")
    sys.exit(0)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-mic-grant-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WORK
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
SHOT = os.environ.get("HEARTH_SHOT_DIR") or WORK

import numpy as np  # noqa: E402

from hearth import realtime_voice as rv, voice as V, voice_overlay as VO, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


from hearth.desktop_attach import permission_for  # noqa: E402

check("the window's own page may use the mic, camera and clipboard",
      all(permission_for(k, "http://127.0.0.1:8765/x", "http://127.0.0.1:8765/") for k in ("Microphone", "Camera", "ClipboardRead")))
check("another address may not", not permission_for("Microphone", "http://127.0.0.1:9000/", "http://127.0.0.1:8765/")
      and not permission_for("Microphone", "https://example.com/", "http://127.0.0.1:8765/"))
check("nor may the page have anything else (location, notifications)",
      not permission_for("Geolocation", "http://127.0.0.1:8765/", "http://127.0.0.1:8765/")
      and not permission_for("Notifications", "http://127.0.0.1:8765/", "http://127.0.0.1:8765/"))


V._load_engine = lambda allow_download=False: ("kokoro", None)
V._synth_chunk = lambda kind, obj, chunk, voice, speed: (
    (np.sin(np.arange(4800) * 2 * np.pi * 200 / 24000) * 4000).astype(np.int16), 24000)


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


built: list = []
rv._REALTIME_AVAILABLE = True
rv.model_downloaded = lambda size="": True
rv._build_recorder = lambda sess=None: built.append(Recorder()) or built[-1]
VO.start = VO.stop = lambda: None


async def fake_run_once(prompt, emit=None, **kw):
    emit("assistant_chunk", content="Hello from the window, through the loop.")
    await asyncio.sleep(0.2)
    emit("assistant", content="Hello from the window, through the loop.")


web.run_once = fake_run_once
web._detect_loaded = lambda: {"id": "stand-in"}
web._save_settings(dict(web._load_settings(), onboarded=True, voice_mic_hint_dismissed=True,
                        ui_theme="dark", ui_accent="sage"))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}/"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

s = socket.socket(); s.bind(("127.0.0.1", 0)); cdp = s.getsockname()[1]; s.close()
# Its own web view storage (another Inglen window may be open with the usual
# one, and two web views can't share it with different settings), and
# pywebview's DevTools port to watch the page through.
RUN = "; ".join([
    "import os, sys, webview",
    "webview.settings['REMOTE_DEBUGGING_PORT'] = int(sys.argv[1])",
    "home = sys.argv[2]",
    "real = os.path.expanduser",
    "os.path.expanduser = lambda p: home + p[1:] if str(p).startswith('~') else real(p)",
    "import hearth.desktop_attach as d",
    "sys.exit(d.main(sys.argv[3:]))",
])
win = subprocess.Popen([sys.executable, "-c", RUN, str(cdp), WORK, "--url", url, "--width", "1100", "--height", "760"],
                       cwd=ROOT)
try:
    with sync_playwright() as pw:
        b = None
        for _ in range(160):
            try:
                b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp}")
                break
            except Exception:
                if win.poll() is not None:
                    break                       # no native window here
                time.sleep(0.5)
        if b is None:
            print("skipped: the window didn't open (no WebView2 here?)")
            sys.exit(0)
        pg = None
        for _ in range(120):
            pages = [p for c in b.contexts for p in c.pages if str(port) in p.url]
            if pages:
                pg = pages[0]
                break
            time.sleep(0.5)
        check("the window opened its page", pg is not None)
        pg.wait_for_function("() => typeof enterVoiceMode === 'function' && typeof CallAudio !== 'undefined'", timeout=60000)
        pg.wait_for_timeout(1500)
        # Asked for the mic, the page gets an answer at once: a prompt would leave it waiting.
        got = pg.evaluate("""() => Promise.race([
            navigator.mediaDevices.getUserMedia({ audio: true }).then(s => { s.getTracks().forEach(t => t.stop()); return 'allowed'; },
                                                                    e => e.name),
            new Promise(r => setTimeout(() => r('waiting on a prompt'), 8000))])""")
        check(f"the window's own page gets the mic without anyone being asked ({got})",
              got in ("allowed", "NotReadableError", "NotFoundError"))
        # A real mic on this PC for voice mode (the default input may be system sound).
        mics = pg.evaluate("async () => (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput' && d.label).map(d => d.label)")
        from hearth.listen import is_system_audio_device
        real = [m for m in mics if not is_system_audio_device(m) and not m.lower().startswith(("default -", "communications -"))]
        if real:
            os.environ["HEARTH_VOICE_INPUT_NAME"] = real[0]
            web._save_settings({"voice_input_name": real[0]})
        # The wake word's way in: voice mode opened by the page itself, no click.
        pg.evaluate("""() => { window.warmUpBrainForVoice = async () => true; STATE.loadedModel = { id: 'stand-in' };
                       enterVoiceMode(); }""")
        try:
            pg.wait_for_function("() => winCall && winCall.ready", timeout=12000)
            ready = True
        except Exception:
            ready = False
        check(f"voice mode starts in the window ({pg.evaluate('() => $(`voice-status-text`).textContent')})", ready)
        if ready:
            pg.wait_for_timeout(1500)
            check("its frames reach the PC", bool(built) and built[-1].fed > 0)
            check("the voice plays through the echo-cancelled loop",
                  pg.evaluate("() => winCall.A.aec && winCall.A.ctx.state === 'running'"))
            built[-1].q.append("say hello"); built[-1].go.set()
            try:
                pg.wait_for_function("() => winCall.A.playing() && !$('voice-audio').paused", timeout=15000)
                played = True
            except Exception:
                played = False
            check("with no click, the reply plays", played)
            look = pg.evaluate("""() => { const d = document.querySelector('#voice-grid .d.on');
                const probe = document.createElement('i'); probe.style.color = 'var(--accent)'; document.body.appendChild(probe);
                const want = getComputedStyle(probe).color; probe.remove();
                return { theme: document.documentElement.dataset.theme, accent: document.documentElement.dataset.accent,
                         dot: d ? getComputedStyle(d).backgroundColor : '', want }; }""")
            check(f"the voice screen wears the saved look, Dark and Sage ({look})",
                  look["theme"] == "dark" and look["accent"] == "sage" and look["dot"] == look["want"])
            pg.screenshot(path=os.path.join(SHOT, "real-window-dark-sage.png"))
            pg.evaluate("() => exitVoiceMode()")
            pg.wait_for_timeout(800)
            check("leaving ends the call on the PC", web.window_call() is None)
finally:
    win.terminate()
    srv.shutdown()

print("\n" + ("FAILED: " + "; ".join(FAILS) if FAILS else "all window mic checks passed"))
sys.exit(1 if FAILS else 0)
