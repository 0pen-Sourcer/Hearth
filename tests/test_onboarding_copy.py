"""First run says what Inglen is, asks the language, and the tour ends each stop in something to try.

The welcome used to sell "open apps, find files" and promise "Nothing leaves this
machine unless you pick a cloud model", which web search and chat apps make
untrue. Nobody was asked their language: a Hindi speaker got English voices and
an English-only speech model. The tour was eleven tabs read out one by one.

    .\\.venv\\Scripts\\python.exe tests\\test_onboarding_copy.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

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
check("and replies follow it: the profile the model reads every turn says so",
      "- Language: reply in Hindi" in memory.read_profile())
onboarding.apply({"language": "en"})
check("English needs no language line", "Language:" not in memory.read_profile())
onboarding.apply({"name": "Asha", "language": "ta"})
onboarding.apply({"language": ""})
check("'more than one' saves blank, so each utterance is worked out", saved.get("stt_language") == "")
check("and drops the reply line, keeping the rest of the profile",
      "Language:" not in memory.read_profile() and "Asha" in memory.read_profile())
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
    check(f"the welcome is a hello and one line on what it is {welcome.strip()!r}",
          "I run on your PC and do real things on it" in welcome and "What makes me different" not in welcome)
    check("and that line says what sets it apart: any brain, one switch, memory, undo, the phone",
          all(w in welcome for w in ("Claude Code and Codex", "one switch", "remember", "undone", "phone")))
    agent = pg.evaluate("() => _agentName()")
    check(f"the hello uses the agent's own name ({agent})", f"Hey, I'm {agent}." in welcome)

    # Each step is a title, at most one short line, and its controls: no boxed notes, no pitch.
    steps = pg.evaluate("""() => [...document.querySelectorAll('#onboard .ob-step')].map(s => ({
        n: Number(s.dataset.step),
        notes: s.querySelectorAll('.ob-tipline, .ob-points, .ap-accent-note').length,
        subs: s.querySelectorAll(':scope > .ob-sub, :scope .ob-split-main > .ob-sub').length,
        text: s.textContent.replace(/\\s+/g, ' ') }))""")
    check(f"setup has eight steps and nothing indexes a ninth {[s['n'] for s in steps]}",
          [s["n"] for s in steps] == list(range(1, 9)) and pg.evaluate("() => OB_TOTAL_STEPS") == 8
          and pg.evaluate("() => document.querySelectorAll('#ob-progress .dot').length") == 8)
    check(f"no step has a boxed note {[s['n'] for s in steps if s['notes']]}", all(not s["notes"] for s in steps))
    check(f"no step has more than one line under its title {[s['n'] for s in steps if s['subs'] > 1]}",
          all(s["subs"] <= 1 for s in steps))
    pitch = ("What makes me different", "Keys are never copied", "Letters, digits and spaces, up to 20",
             "A few quick choices", "Try one", "You're all set", "This is optional", "Hear it before")
    check(f"none of the old pitch or helper lines remain {[p for p in pitch if any(p in s['text'] for s in steps)]}",
          not [p for p in pitch if any(p in s["text"] for s in steps)])
    check("no step hardcodes a persona name in its fields",
          pg.evaluate("() => !/JARVIS|FRIDAY/.test([...document.querySelectorAll('#onboard input, #onboard textarea')].map(i => i.placeholder).join(' '))"))
    bad = pg.evaluate("""async () => {
      obStep = 6; obRender(); await new Promise(r => setTimeout(r, 300));
      const el = $('ob-agent-name'); el.value = 'Bad_Name!'; el.dispatchEvent(new Event('input'));
      const out = { err: !$('ob-agent-name-err').hidden, placeholder: el.placeholder };
      $('ob-next').click(); await new Promise(r => setTimeout(r, 200));
      out.stayed = obStep === 6;
      el.value = 'Friday'; el.dispatchEvent(new Event('input'));
      out.cleared = $('ob-agent-name-err').hidden;
      el.value = ''; el.dispatchEvent(new Event('input'));
      return out; }""")
    check(f"a bad name is caught as it's typed and holds the step {bad}",
          bad["err"] and bad["stayed"] and bad["cleared"] and bad["placeholder"] == agent)

    lang = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      obStep = 4; obRender(); await wait(300);
      document.querySelector('#ob-body [data-voice="full"]').click(); await wait(200);
      const sel = $('ob-lang'), out = {};
      document.querySelector('#ob-body [data-voice="text"]').click(); await wait(200);
      out.textOnly = !!sel.getClientRects().length;
      document.querySelector('#ob-body [data-voice="full"]').click(); await wait(200);
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
    check("the language is asked for text only too, since replies follow it", lang["textOnly"])
    check(f"Hindi offers only Hindi voices {lang['hi']}",
          lang["hi"]["voices"] and all(v.startswith("h") for v in lang["hi"]["voices"]))
    check(f"German keeps the English voices and says no voice speaks it {lang['de']['note']!r}",
          all(v[:1] in "ab" for v in lang["de"]["voices"]) and "English voice" in lang["de"]["note"])
    check("English goes back to the English voices", all(v[:1] in "ab" for v in lang["en"]["voices"]))

    pg.evaluate("() => { obStep = 8; obRender(); }")
    pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(shots, "3_last_step.png"))
    last = pg.evaluate("() => ({ next: $('ob-next').textContent, meta: $('ob-step-meta').textContent, skip: $('ob-skip').hidden })")
    check(f"the memory step is the last one, with Finish {last}",
          last["next"] == "Finish" and last["meta"] == "Step 8 of 8" and last["skip"])
    pg.click("#ob-next")
    for _ in range(40):
        pg.wait_for_timeout(150)
        if pg.evaluate("() => !!document.getElementById('tour-card')"):
            break
    check("Finish goes straight to the tour", pg.evaluate("() => !!document.getElementById('tour-card')"))
    check("and setup is closed", pg.evaluate("() => !$('onboard').classList.contains('open')"))
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
    check(f"the tour has seven stops {[s['title'] for s in stops]}", len(stops) == 7)
    check("it stops at the approval switch, the phone and the chat apps",
          {"Before I act", "Your phone", "Chat apps and email"} <= {s["title"] for s in stops})
    check("and ends back in the chat", pg.evaluate("() => document.getElementById('view-chat').classList.contains('active')"))
    check("each stop ends in something to try", stops and all(s["tried"].startswith("Try: ") for s in stops))
    check(f"each card sits inside the window {[s['inside'] for s in stops]}", stops and all(s["inside"] for s in stops))
    check(f"no page errors {errors[:3]}", not errors)
    browser.close()
srv.shutdown()
print("screenshots in", shots)
print("\n" + ("all onboarding copy checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
