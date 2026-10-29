"""Voice mode's settings sheet in the window: the phone call sheet's choices (voice,
language it listens in, speaker where the page can pick one, ready and hang-up
tones) behind a small icon, saved to the PC's settings the phone's call reads.
Stand-in recognizer and voice; the page in Edge. With SHOTS=<folder> it saves
screenshots of the sheet in Dark and Light.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_sheet.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-voice-sheet-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WORK
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

import numpy as np  # noqa: E402

from hearth import realtime_voice as rv, voice as V, voice_overlay as VO, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


V._load_engine = lambda allow_download=False: ("kokoro", None)
V._synth_chunk = lambda kind, obj, chunk, voice, speed: (np.zeros(2400, dtype=np.int16), 24000)


class Recorder:
    def __init__(self):
        self.go = threading.Event()

    def feed_audio(self, data, original_sample_rate=16000):
        pass

    def text(self):
        self.go.wait(); self.go.clear()
        return ""

    def clear_audio_queue(self):
        pass

    def shutdown(self):
        self.go.set()


rv._REALTIME_AVAILABLE = True
rv.model_downloaded = lambda size="": True
BUILT: list = []
rv._build_recorder = lambda sess=None: BUILT.append(sess) or Recorder()
rv._mic_index = lambda: None
VO.start = VO.stop = lambda: None
V.list_voices = lambda: ["af_heart", "am_michael", "bf_emma", "hf_alpha"]
web._save_settings(dict(web._load_settings(), onboarded=True, voice_mic_hint_dismissed=True,
                        voice_name="af_heart", stt_language="", llm_provider="local"))

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{port}"
SHOTS = os.environ.get("SHOTS", "")


def saved(key):
    return web._load_settings().get(key)


from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                                    "--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1180, "height": 780})
    ctx.grant_permissions(["microphone"], origin=URL)
    pg = ctx.new_page()
    for _try in range(4):                  # a big local page can stall on a busy PC; it comes on a retry
        try:
            pg.goto(URL + "/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    pg.wait_for_function("() => typeof enterVoiceMode === 'function'", timeout=60000)
    pg.wait_for_timeout(1500)
    pg.evaluate("() => { window.warmUpBrainForVoice = async () => true; STATE.loadedModel = { id: 'stand-in' };"
                " enterVoiceMode(); }")
    pg.wait_for_function("() => _voiceReady === true", timeout=60000)
    check("voice mode has a settings icon", pg.is_visible("#voice-set"))
    pg.click("#voice-set")
    pg.wait_for_function("() => !!document.querySelector('#voice-set-body [data-lang]')", timeout=10000)
    secs = pg.eval_on_selector_all("#voice-set-body .sec", "els => els.map(e => e.textContent)")
    check(f"the sheet has the call sheet's sections ({secs})",
          secs == ["Voice", "Language you speak", "Listening runs on", "Speech model", "Sound", "Tones"])
    check("it shows the voice picked on the PC", "Heart" in pg.inner_text("#vs-voice"))
    for theme in ("dark", "light"):
        pg.evaluate(f"() => {{ document.documentElement.dataset.theme = '{theme}'; }}")
        pg.wait_for_timeout(300)
        if SHOTS:
            pg.screenshot(path=os.path.join(SHOTS, f"voice_sheet_{theme}.png"))
    pg.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    pg.click("#vs-voice")
    pg.wait_for_selector("#voice-set-body [data-voice='bf_emma']")
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, "voice_sheet_voices_dark.png"))
    pg.click("#voice-set-body [data-voice='bf_emma']")
    end = time.time() + 6
    while time.time() < end and saved("voice_name") != "bf_emma":
        time.sleep(0.1)
    check(f"a voice picked here is the PC's voice, the one the phone's call uses ({saved('voice_name')})",
          saved("voice_name") == "bf_emma")
    pg.click("#voice-set-body [data-lang='hi']")
    end = time.time() + 6
    while time.time() < end and saved("stt_language") != "hi":
        time.sleep(0.1)
    check(f"so is the language it listens in ({saved('stt_language')!r})", saved("stt_language") == "hi")
    pg.click("#vs-tones")
    end = time.time() + 6
    while time.time() < end and saved("voice_tones") is not False:
        time.sleep(0.1)
    check("the tones can be turned off", saved("voice_tones") is False)
    # Where it listens and with which model: changed live, it listens again without leaving voice mode.
    n0 = len(BUILT)
    pg.click("#voice-set-body [data-dev='cpu']")
    end = time.time() + 15
    while time.time() < end and not (saved("stt_device") == "cpu" and len(BUILT) > n0):
        time.sleep(0.1)
    check(f"listening moved to the processor live ({saved('stt_device')}, listener rebuilt {len(BUILT) - n0}x)",
          saved("stt_device") == "cpu" and len(BUILT) > n0 and pg.evaluate("() => voiceModeOn"))
    n0 = len(BUILT)
    pg.click("#voice-set-body [data-mdl^='tiny']")
    end = time.time() + 15
    while time.time() < end and not (str(saved("stt_model")).startswith("tiny") and len(BUILT) > n0):
        time.sleep(0.1)
    check(f"and to the tiny model ({saved('stt_model')})", str(saved("stt_model")).startswith("tiny") and len(BUILT) > n0)
    pg.click("#voice-set-body [data-dev='cuda']")
    pg.wait_for_timeout(800)
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, "voice_sheet_listening_dark.png"))
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    check("Esc closes the sheet and stays in voice mode",
          pg.evaluate("() => document.getElementById('voice-set-sheet').hidden && voiceModeOn"))
    # Approvals in voice mode: no buttons for what a spoken yes or no answers; a small quiet
    # pair only for a step that needs a click.
    pg.evaluate("() => showInlinePermission('p1', 'run_command', {command: 'nvidia-smi'}, '', true, false)")
    check("a step a spoken yes may answer shows no buttons", pg.evaluate("() => document.getElementById('voice-ask').hidden"))
    pg.evaluate("() => hideInlinePermission()")
    pg.evaluate("() => showInlinePermission('p2', 'delete_path', {path: 'C:/x/notes'}, '', true, true)")
    check("one that needs a click shows a small quiet Allow and Deny",
          pg.evaluate("() => !document.getElementById('voice-ask').hidden && document.getElementById('voice-ask').classList.contains('quiet')"))
    for theme in ("dark", "light"):
        pg.evaluate(f"() => {{ document.documentElement.dataset.theme = '{theme}'; }}")
        pg.wait_for_timeout(300)
        if SHOTS:
            pg.screenshot(path=os.path.join(SHOTS, f"voice_tap_{theme}.png"))
    b.close()
srv.shutdown()

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall voice sheet checks passed")
