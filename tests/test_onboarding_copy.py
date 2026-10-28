"""First run says what Inglen is, asks the language, and the tour ends each stop in something to try.

The welcome used to sell "open apps, find files" and promise "Nothing leaves this
machine unless you pick a cloud model", which web search and chat apps make
untrue. Nobody was asked their language: a Hindi speaker got English voices and
an English-only speech model. The tour was eleven tabs read out one by one.

    .\\.venv\\Scripts\\python.exe tests\\test_onboarding_copy.py
"""
from __future__ import annotations

import builtins
import json
import os
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


work = tempfile.mkdtemp(prefix="hearth-obcopy-")
os.environ["JARVIS_WORKSPACE"] = work
os.environ["HEARTH_WORKSPACE"] = work
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
from hearth import memory, onboarding, web                          # noqa: E402

# ---------------------------------------------------------------- the saved answer
memory.PROFILE_PATH = os.path.join(work, "profile.md")
memory.RULES_PATH = os.path.join(work, "rules.md")
saved: dict = {}
real_save = web._save_settings
web._save_settings = lambda d: saved.update(d) or saved
onboarding.apply({"language": "hi"})
check("the language spoken is saved for listening", saved.get("stt_language") == "hi")
onboarding.apply({"language": ""})
check("'more than one' saves blank, so each utterance is worked out", saved.get("stt_language") == "")
onboarding.apply({"language": "hindi; rm"})
check("anything that isn't a language code is ignored", saved.get("stt_language") == "")
check("names and codes both read as a language",
      onboarding.language_code("Hindi") == "hi" and onboarding.language_code("hi") == "hi"
      and onboarding.language_code("spa") == "es" and onboarding.language_code("klingon") is None)
check("a language with voices gets one of the same kind",
      (onboarding.voice_for("hi", "am_michael") or "").startswith("hm")
      and (onboarding.voice_for("hi", "af_heart") or "").startswith("hf"))
check("no voice change for English, for a language without voices, or when the voice already speaks it",
      onboarding.voice_for("en", "am_michael") is None and onboarding.voice_for("de", "am_michael") is None
      and onboarding.voice_for("hi", "hm_omega") is None)

# ---------------------------------------------------------------- the terminal asks it
import hearth_cli                                                    # noqa: E402
answers_in = iter(["", "", "", "", "", "", "h", "hindi", ""])
cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
got = hearth_cli.JarvisCLI._ask_personal(cli, lambda _q: next(answers_in, ""))
check(f"the terminal setup asks the language and picks a voice that speaks it {got.get('language'), got.get('voice_name')}",
      got.get("language") == "hi" and str(got.get("voice_name", "")).startswith("h"))
web._save_settings = real_save

# ---------------------------------------------------------------- the window
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped the window: playwright is not installed")
    sys.exit(1 if FAILS else 0)

web._save_settings(dict(web._load_settings(), onboarded=True))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
shots = os.environ.get("HEARTH_SHOTS") or tempfile.mkdtemp(prefix="hearth-obcopy-shots-")

with sync_playwright() as pw:
    browser = None
    for kw in ({"channel": "msedge"}, {}):
        try:
            browser = pw.chromium.launch(**kw)
            break
        except Exception:
            pass
    if browser is None:
        print("skipped the window: no browser")
        srv.shutdown()
        sys.exit(1 if FAILS else 0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    posted: list = []

    def onboarding_api(route):
        posted.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "settings": {}}))
    pg.route("**/api/onboarding", onboarding_api)
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_function("typeof obRender === 'function' && typeof startTour === 'function'", timeout=60000)
    pg.wait_for_timeout(1500)

    pg.evaluate("() => { $('onboard').classList.add('open'); obStep = 1; obRender(); }")
    pg.wait_for_timeout(600)
    welcome = pg.evaluate("() => document.querySelector('#onboard .ob-step[data-step=\"1\"]').textContent.replace(/\\s+/g, ' ')")
    pg.screenshot(path=os.path.join(shots, "1_welcome.png"))
    check("the welcome no longer says nothing leaves the machine", "Nothing leaves" not in welcome)
    check("the welcome says what is different: your own AI plan, memory, helpers, phone, asks first",
          all(w in welcome for w in ("already pay for", "remember", "helpers", "phone", "OK")))

    lang = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      obStep = 4; obRender(); await wait(300);
      document.querySelector('#ob-body [data-voice="full"]').click(); await wait(200);
      const sel = $('ob-lang'), out = {};
      out.langs = sel.options.length;
      const pick = async (v) => { sel.value = v; sel.dispatchEvent(new Event('change')); await wait(100);
        return { voices: [...$('ob-voice-name').options].map(o => o.value), note: $('ob-lang-note').textContent }; };
      out.hi = await pick('hi');
      out.de = await pick('de');
      out.en = await pick('en');
      await pick('hi');
      return out;
    }""")
    pg.evaluate("() => $('ob-voice-picker').scrollIntoView({block: 'end'})")
    pg.wait_for_timeout(200)
    pg.screenshot(path=os.path.join(shots, "2_voice_language.png"))
    check(f"the voice step asks the language, from the list listening knows ({lang['langs']})", lang["langs"] >= 20)
    check(f"Hindi offers only Hindi voices and says so {lang['hi']}",
          lang["hi"]["voices"] and all(v.startswith("h") for v in lang["hi"]["voices"]) and "Hindi" in lang["hi"]["note"])
    check(f"German keeps the English voices and says no voice speaks it {lang['de']['note']!r}",
          all(v[:1] in "ab" for v in lang["de"]["voices"]) and "English voice" in lang["de"]["note"])
    check("English goes back to the English voices", all(v[:1] in "ab" for v in lang["en"]["voices"]))

    pg.evaluate("() => { obStep = 9; obRender(); }")
    pg.wait_for_timeout(400)
    pg.screenshot(path=os.path.join(shots, "3_all_set.png"))
    tries = pg.evaluate("() => [...document.querySelectorAll('#onboard .ob-try')].map(b => b.dataset.try)")
    check(f"the last step offers one try that shows memory and helpers {tries}",
          any("remember" in t and "helpers" in t for t in tries))
    pg.evaluate("() => obFinish(true)")
    pg.wait_for_timeout(800)
    check(f"finishing sends the language picked {posted[-1] if posted else None}",
          bool(posted) and posted[-1].get("language") == "hi")

    pg.evaluate("() => { try { $('onboard').classList.remove('open'); } catch {} document.querySelectorAll('.modal-bg,.modal').forEach(m => m.remove()); }")
    stops = []
    pg.evaluate("() => startTour()")
    for n in range(10):
        pg.wait_for_timeout(450)
        st = pg.evaluate("""() => { const c = document.getElementById('tour-card'); if (!c) return null;
          const r = c.getBoundingClientRect();
          return { title: c.querySelector('b').textContent, tried: (c.querySelector('.tour-try') || {}).textContent || '',
                   inside: r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth }; }""")
        if not st:
            break
        pg.screenshot(path=os.path.join(shots, f"4_tour_{n + 1}.png"))
        stops.append(st)
        pg.click("#tour-card [data-t='next']")
    check(f"the tour has six stops {[s['title'] for s in stops]}", len(stops) == 6)
    check("each stop ends in something to try", stops and all(s["tried"].startswith("Try: ") for s in stops))
    check(f"each card sits inside the window {[s['inside'] for s in stops]}", stops and all(s["inside"] for s in stops))
    check(f"no page errors {errors[:3]}", not errors)
    browser.close()
srv.shutdown()
print("screenshots in", shots)
print("\n" + ("all onboarding copy checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
