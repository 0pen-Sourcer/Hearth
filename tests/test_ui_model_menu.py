"""The model menu in the real page.

Every provider a key is saved for has one row that opens into its models, so a
provider with a long list doesn't fill the menu and another provider's models
are one click away. The box at the top looks through every agent and provider
at once, and each result says where it comes from, since the same model can be
had through an agent's plan and through an API key. Picking a model from a
provider that isn't in use switches to it with the key already saved.

The page runs in Edge; the providers and agents are stand-ins.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_model_menu.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("skipped: playwright is not installed")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="hearth-menu-")
os.environ["JARVIS_WORKSPACE"] = work
os.environ["HEARTH_WORKSPACE"] = work
from hearth import web                                               # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

ANTHROPIC = ["claude-haiku-4-5", "claude-opus-5", "claude-sonnet-5"]
LISTS = {"anthropic": ANTHROPIC, "gemini": ["models/gemini-3-flash", "models/gemini-3-pro"]}
AGENTS = {
    "claude-code": {"provider": "claude-code", "model": "", "efforts": [], "models": [
        {"id": "", "label": "Default", "model": "Sonnet 5"}, {"id": "opus", "label": "Opus 5", "ctx": 1000000},
        {"id": "haiku", "label": "Haiku 4.5"}]},
    "cursor-agent": {"provider": "cursor-agent", "model": "", "efforts": [], "models": [
        {"id": "opus-5", "label": "Opus 5"}, {"id": "gpt-5", "label": "GPT-5"}]
        + [{"id": f"opus-5-v{i}", "label": f"Opus 5 v{i}"} for i in range(1, 9)]},
}
now = {"provider": "local", "model": ""}       # what the stand-in app is switched to
DISK = "C:\\models\\Standin-9B-Q4_K_M.gguf"
started: list = []                              # local models asked to load
REAL: dict = {}                                 # the app's own answers, by path
asked: list = []                                # provider lists that were fetched
switched: list = []                             # bodies sent to switch the endpoint
saved: dict = {}                                # settings the page wrote


def the_real(route) -> dict:
    """What the app itself answers, asked for outside the browser so a stand-in
    can change one field of it. A burst of polls can get a connection dropped
    on Windows, so it asks again; an answer that never comes fails the request
    in the page instead of leaving it waiting."""
    path = route.request.url.split("?")[0]
    if path in REAL:                            # asked once: the stand-ins only change what the test is about
        return dict(REAL[path])
    for _ in range(4):
        try:
            with urllib.request.urlopen(route.request.url, timeout=60) as r:
                REAL[path] = json.load(r)
                return dict(REAL[path])
        except OSError:
            time.sleep(0.2)
    route.abort()
    return None


def models(route):
    cloud = now["provider"] == "anthropic"
    ms = ([{"id": m, "state": "loaded" if m == now["model"] else "", "type": "llm"} for m in ANTHROPIC] if cloud
          else [{"id": "qwen-9b", "state": "loaded", "type": "llm"}])
    route.fulfill(json={"models": ms, "notice": "", "agent_brains": [
        {"id": "claude-code", "label": "Claude Code", "installed": True},
        {"id": "cursor-agent", "label": "Cursor", "installed": True},
        {"id": "codex", "label": "Codex", "installed": False, "site": "https://openai.com/codex"}],
        "providers": [
            {"id": "anthropic", "label": "Anthropic", "url": "https://api.anthropic.com/v1/", "active": cloud,
             "model": now["model"] if cloud else ""},
            {"id": "gemini", "label": "Google Gemini", "url": "https://example.invalid/gemini/", "active": False, "model": ""}]})


def provider_models(route):
    pid = json.loads(route.request.post_data or "{}").get("id")
    asked.append(pid)
    route.fulfill(json={"models": LISTS[pid]})


def agent_models(route):
    route.fulfill(json=AGENTS[route.request.url.split("id=")[1]])


ENDPOINT_EXTRA = {}


def endpoint(route):
    body = json.loads(route.request.post_data or "{}")
    switched.append(body)
    now.update(provider=body["provider"], model=body["model"])
    # The app saves the switch itself, the key with it when one was typed.
    saved.update(llm_provider=body["provider"], llm_url=body.get("url", ""), llm_model=body["model"])
    if body.get("key"):
        saved["llm_key"] = body["key"]
    route.fulfill(json={"ok": True, "model": body["model"], **ENDPOINT_EXTRA})


SETTING_POSTS = []


def settings(route):
    if route.request.method == "POST":
        SETTING_POSTS.append(json.loads(route.request.post_data or "{}"))
        saved.update(json.loads(route.request.post_data or "{}"))
        if "llm_model" in saved:
            now["model"] = saved["llm_model"]
        return route.fulfill(json={"ok": True})
    real = the_real(route)
    if real is None:
        return
    real.update(saved)
    route.fulfill(json=real)


def state(route):
    real = the_real(route)
    if real is None:
        return
    real.update(picked_model=saved.get("llm_model", real.get("picked_model", "")))
    real.update(provider=now["provider"], endpoint={"anthropic": "https://api.anthropic.com/v1/",
                                                     "gemini": "https://example.invalid/gemini/"}.get(now["provider"], real["endpoint"]))
    route.fulfill(json=real)


def status(route):
    real = the_real(route)
    if real is None:
        return
    real["disk_models"] = [{"path": DISK, "filename": "Standin-9B-Q4_K_M.gguf", "size_gb": 5.4}]
    route.fulfill(json=real)


def start(route):
    started.append(json.loads(route.request.post_data or "{}").get("model_path"))
    now.update(provider="local", model="")
    saved.update(llm_provider="local", llm_model="")        # the server does this itself on a start
    route.fulfill(json={"ok": True, "url": "http://localhost:1234/v1", "info": {"ctx": 32768}})


# Wait for the page to get there instead of guessing how long it takes.
UNTIL = """window.until = async (ok, ms = 15000) => {
  const t = Date.now();
  while (Date.now() - t < ms) { try { if (ok()) return true; } catch {} await new Promise(r => setTimeout(r, 100)); }
  return false;
}"""
OPEN = """async () => {
  await refreshSettings(); await refreshState(); await refreshModels();
  mpClose(); mpOpen();
  const menu = document.getElementById('mp-menu');
  return { secs: [...menu.querySelectorAll('.mp-body .mp-sec')].map(e => e.textContent),
           rows: [...menu.querySelectorAll('.mp-body > .mp-row')].map(e => [e.dataset.v, e.querySelector('.mp-t').textContent,
                    (e.querySelector('.mp-more') || { dataset: {} }).dataset.more || '']),
           find: !!menu.querySelector('.mp-all'), name: document.getElementById('mp-name').textContent };
}"""
SEARCH = """async (q) => {
  const menu = document.getElementById('mp-menu'), box = menu.querySelector('.mp-all');
  box.value = q; box.dispatchEvent(new Event('input'));
  await new Promise(r => setTimeout(r, 150));
  await until(() => ![...menu.querySelectorAll('.mp-hits .mp-empty')].some(e => e.textContent.startsWith('Asking')));
  let from = '';
  const hits = [];
  for (const e of menu.querySelector('.mp-hits').children) {
    if (e.classList.contains('mp-sec')) from = e.textContent;
    else if (e.classList.contains('mp-row')) hits.push([e.dataset.v, e.querySelector('.mp-t').textContent, from]);
  }
  return { bodyHidden: menu.querySelector('.mp-body').hidden, hits,
           notes: [...menu.querySelectorAll('.mp-hits .mp-note')].map(e => e.textContent) };
}"""
UNDER = """async (key) => {
  const menu = document.getElementById('mp-menu');
  menu.querySelector(key.startsWith('row:') ? `.mp-row[data-v="${key.slice(4)}"]` : `.mp-more[data-more="${key}"]`).click();
  await new Promise(r => setTimeout(r, 900));
  const sub = menu.querySelector('.mp-sub');
  return { open: !menu.hidden, rows: sub ? [...sub.querySelectorAll('.mp-row')].map(e => [e.dataset.v, !!e.querySelector('.mp-badge')]) : null };
}"""

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skipped: no browser to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    pg.route("**/api/models", models)
    pg.route("**/api/providers/models", provider_models)
    pg.route("**/api/brain/agent/models*", agent_models)
    pg.route("**/api/llm-endpoint", endpoint)
    pg.route("**/api/settings", settings)
    pg.route("**/api/state", state)
    pg.route("**/api/llmserver/status", status)
    pg.route("**/api/llmserver/start", start)
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    time.sleep(3)
    pg.evaluate(UNTIL)

    first = pg.evaluate(OPEN)
    provs = [r for r in first["rows"] if r[0].startswith("prov::")]
    check(f"each saved provider is one row that opens, under its own heading {first['secs']} {provs}",
          "Your providers" in first["secs"] and first["find"]
          and provs == [["prov::anthropic", "Anthropic", "prov::anthropic"], ["prov::gemini", "Google Gemini", "prov::gemini"]])
    check("none of a provider's models are in the menu until it is opened",
          not any("claude" in r[1] or "gemini-3" in r[1] for r in first["rows"]) and asked == [])

    opened = pg.evaluate(UNDER, "row:prov::anthropic")
    check(f"clicking the provider opens its models and keeps the menu up {opened}",
          opened["open"] and opened["rows"] == [[f"prov::anthropic::{m}", False] for m in ANTHROPIC] and asked == ["anthropic"])

    hits = pg.evaluate(SEARCH, "opus")
    cursor = [h for h in hits["hits"] if h[2] == "Cursor · your Cursor plan"]
    check(f"one search looks through every agent and provider, under a heading for each {hits['hits']}",
          hits["bodyHidden"] and hits["hits"][0] == ["agent::claude-code::opus", "Opus 5", "Claude Code · your Claude plan"]
          and cursor[0] == ["agent::cursor-agent::opus-5", "Opus 5", "Cursor · your Cursor plan"]
          and hits["hits"][-1] == ["prov::anthropic::claude-opus-5", "claude-opus-5", "Anthropic · your API key"])
    check(f"a source with many matches shows a few, so the others stay in sight {len(cursor)} {hits['notes']}",
          len(cursor) == 6 and len(hits["hits"]) == 8 and hits["notes"] == ["3 more here. Add a word to narrow it down."])
    narrowed = pg.evaluate(SEARCH, "cursor opus")
    check(f"naming the source narrows it to that one, in full {len(narrowed['hits'])}",
          len(narrowed["hits"]) == 9 and all(h[0].startswith("agent::cursor-agent::opus") for h in narrowed["hits"])
          and narrowed["notes"] == [])
    local = pg.evaluate(SEARCH, "qwen")
    check(f"what is on this PC is found too {local['hits']}", local["hits"] == [["qwen-9b", "qwen-9b", "On this PC"]])
    named = pg.evaluate(SEARCH, "flash")
    check(f"a provider's own prefix is left off the name but kept in what is picked {named['hits']}",
          named["hits"] == [["prov::gemini::models/gemini-3-flash", "gemini-3-flash", "Google Gemini · your API key"]])
    check("a provider is asked once however much is typed", asked == ["anthropic", "gemini"])
    cleared = pg.evaluate(SEARCH, "")
    check("an empty box brings the menu back", not cleared["bodyHidden"] and cleared["hits"] == [])

    pg.evaluate(SEARCH, "sonnet")
    picked = pg.evaluate("""async () => {
      const menu = document.getElementById('mp-menu');
      menu.querySelector('.mp-hits .mp-row[data-v="prov::anthropic::claude-sonnet-5"]').click();
      await until(() => document.getElementById('mp-name').textContent === 'claude-sonnet-5');
      return { closed: menu.hidden, provider: STATE.settings.llm_provider, kind: STATE.brain.kind,
               name: document.getElementById('mp-name').textContent };
    }""")
    check(f"picking another provider's model switches to it with the saved key {switched} {picked}",
          switched == [{"provider": "anthropic", "url": "https://api.anthropic.com/v1/", "key": "", "model": "claude-sonnet-5"}]
          and picked["closed"] and picked["provider"] == "anthropic" and picked["kind"] == "cloud"
          and picked["name"] == "claude-sonnet-5" and "llm_key" not in saved)

    again = pg.evaluate(OPEN)
    flat = [r for r in again["rows"] if r[0].startswith("claude")]
    check(f"the provider in use shows the one model in use, and that row opens the rest {again['secs']} {flat}",
          "From this provider" not in again["secs"] and flat == [["claude-sonnet-5", "claude-sonnet-5", "prov::anthropic"]]
          and again["name"] == "claude-sonnet-5")
    before = list(asked)
    rest = pg.evaluate(UNDER, "prov::anthropic")
    check(f"its list is the one already in hand, with the model in use marked {rest}",
          rest["rows"] == [["prov::anthropic::claude-sonnet-5", True], ["prov::anthropic::claude-haiku-4-5", False],
                           ["prov::anthropic::claude-opus-5", False]] and asked == before)
    other = pg.evaluate("""async () => {
      const menu = document.getElementById('mp-menu');
      menu.querySelector('.mp-sub .mp-row[data-v="prov::anthropic::claude-haiku-4-5"]').click();
      await until(() => document.getElementById('mp-name').textContent === 'claude-haiku-4-5');
      return { closed: menu.hidden, name: document.getElementById('mp-name').textContent };
    }""")
    for _ in range(50):                          # the save is sent after the name changes
        if saved.get("llm_model") == "claude-haiku-4-5":
            break
        pg.wait_for_timeout(100)
    check(f"another model of the provider in use is a change of model, not a new switch {other}",
          len(switched) == 1 and saved.get("llm_model") == "claude-haiku-4-5" and other["closed"]
          and other["name"] == "claude-haiku-4-5")
    # A model on this PC, picked while a provider is the brain.
    wrote = []
    pg.on("request", lambda r: wrote.append(r.post_data or "") if r.method == "POST" and r.url.endswith("/api/settings") else None)
    pg.evaluate(OPEN)
    local = pg.evaluate("""async (v) => {
      document.querySelector(`#mp-menu .mp-row[data-v="${CSS.escape(v)}"]`).click();
      const during = [...document.getElementById('model-select').children]
        .filter(o => o.tagName === 'OPTION').map(o => o.value);
      await until(() => !STATE.brain.busy && document.getElementById('brand-sub').textContent === '· local');
      return { provider: STATE.settings.llm_provider, kind: STATE.brain.kind, during,
               head: document.getElementById('brand-sub').textContent };
    }""", "disk::" + DISK)
    check(f"a model on this PC picked while a provider is the brain is loaded here {started} {local}",
          started == [DISK] and len(switched) == 1 and not any(".gguf" in w for w in wrote))
    check(f"while it loads, the provider's models are not listed as this PC's {local['during']}",
          not any(v.startswith("claude") for v in local["during"]))
    check("and the window says local everywhere at once",
          local["provider"] == "local" and local["kind"] == "local" and local["head"] == "· local")

    # Settings switches through the same code, with the key typed there.
    applied = pg.evaluate("""async () => {
      showView('settings'); await refreshSettings();
      const mode = document.getElementById('setting-brain-mode');
      mode.value = 'gemini'; mode.dispatchEvent(new Event('change'));
      document.getElementById('setting-llm-key').value = 'typed-key';
      document.getElementById('btn-llm-apply').click();
      await until(() => STATE.settings.llm_provider === 'gemini' && document.getElementById('mp-name').textContent === 'gemini-3-flash');
      return { provider: STATE.settings.llm_provider, name: document.getElementById('mp-name').textContent };
    }""")
    last = switched[-1]
    check(f"Settings still switches, and saves the key that was typed {last} {applied}",
          len(switched) == 2 and last["provider"] == "gemini" and last["key"] == "typed-key" and last["model"]
          and saved.get("llm_key") == "typed-key" and applied["provider"] == "gemini" and applied["name"] == last["model"])
    # A state reply already on its way when the brain is switched here describes the brain before.
    late = pg.evaluate("""async () => {
      const real = window.fetch;
      let let_go;
      const held = new Promise(r => { let_go = r; });
      window.fetch = async (u, o) => { const r = await real(u, o); if (String(u).includes('/api/state')) await held; return r; };
      const looking = refreshState();                 // asked while still on gemini...
      await new Promise(r => setTimeout(r, 300));
      window.fetch = real;
      STATE.settings.llm_provider = 'anthropic';      // ...and the window moves on before it lands
      brainMoved();
      let_go(); await looking;
      const kept = STATE.settings.llm_provider;
      STATE.settings.llm_provider = 'gemini';
      return kept;
    }""")
    check(f"a state reply asked for before a switch does not undo it ({late})", late == "anthropic")

    # The saved model was put right on the server while this window held the old name.
    saved.update(llm_model="gemini-3-pro")
    renamed = pg.evaluate("""async () => {
      const before = STATE.settings.llm_model;
      await refreshState();
      return { before, saved: STATE.settings.llm_model, shown: STATE.brain.modelId };
    }""")
    check(f"a model name changed on the server is the one this window shows {renamed}",
          renamed["before"] != "gemini-3-pro" and renamed["saved"] == "gemini-3-pro" and renamed["shown"] == "gemini-3-pro")

    # The brain changed without this window doing it (the Models tab, the terminal).
    now.update(provider="local", model="")
    saved.update(llm_provider="local", llm_model="")
    behind = pg.evaluate("""async () => {
      const before = STATE.settings.llm_provider;
      await refreshState();
      await until(() => document.getElementById('brand-sub').textContent === '· local');
      return { before, provider: STATE.settings.llm_provider, kind: STATE.brain.kind,
               head: document.getElementById('brand-sub').textContent };
    }""")
    check(f"a brain changed elsewhere is caught up with on the next look {behind}",
          behind["before"] == "gemini" and behind["provider"] == "local" and behind["kind"] == "local"
          and behind["head"] == "· local")
    # Switching to a provider waited on four settings saves the server had
    # already made, and ended with different words than an agent switch, plus
    # a second toast about the phone bridges.
    ENDPOINT_EXTRA.update(restarted_bridges=["telegram"], stopped_builtin=True)
    SETTING_POSTS.clear()
    said = pg.evaluate("""async () => {
      const seen = [], real = window.toast;
      window.toast = (title, body) => { seen.push([title, body || '']); };
      try { await switchEndpoint({ provider: 'anthropic', url: 'https://api.anthropic.com/v1/', key: '', model: 'claude-x' }); }
      finally { window.toast = real; }
      return seen;
    }""")
    ENDPOINT_EXTRA.clear()
    check(f"a switch to a provider ends with the same words as an agent switch, once {said}",
          len(said) == 1 and said[0][0].endswith("now thinks with Anthropic · claude-x")
          and "Telegram moved over too" in said[0][1])
    check(f"the page doesn't save again what the server saved {SETTING_POSTS}", SETTING_POSTS == [])

    # Keeping the local model loaded beside a cloud brain was a switch deep in
    # Settings; picking a cloud model in the menu dropped it.
    SETTING_POSTS.clear()
    keep = pg.evaluate("""async () => {
      mpOpen();
      const btn = document.querySelector('#mp-menu .mp-keep .switch');
      if (!btn) return { found: false };
      const before = btn.classList.contains('on');
      btn.click();
      await new Promise(r => setTimeout(r, 300));
      const settingsSwitch = document.querySelector('.switch[data-key="keep_local_warm"]');
      const out = { found: true, before, after: btn.classList.contains('on'),
                    settings: settingsSwitch ? settingsSwitch.classList.contains('on') : null };
      btn.click();
      await new Promise(r => setTimeout(r, 300));
      mpClose();
      return out;
    }""")
    check(f"the model menu keeps this PC's model loaded with one switch, the same one Settings shows {keep}",
          keep.get("found") and keep["after"] != keep["before"] and keep["settings"] == keep["after"]
          and {"keep_local_warm": keep["after"]} in SETTING_POSTS)

    # OpenRouter, Groq and most agent programs had no logo, so their badge was a
    # letter; a saved provider in the menu was a plain cloud.
    marks = pg.evaluate("""() => ({
      openrouter: harnessMark(BRAIN_MARKS.openrouter), goose: harnessMark(HARNESS.goose),
      aider: harnessMark(HARNESS.aider), claude: harnessMark(HARNESS['claude-code']),
      prov: provIco('openrouter'), unknown: provIco('custom') })""")
    check("a badge with no drawn logo shows its site's own icon",
          "/api/favicon?d=openrouter.ai" in marks["openrouter"] and "/api/favicon?d=goose-docs.ai" in marks["goose"])
    check("a drawn logo stays, and a maker with no icon keeps its letter",
          marks["claude"].startswith("<svg") and marks["aider"] == "A")
    check("a saved provider in the menu carries its own mark, an unknown one the cloud",
          "openrouter.ai" in marks["prov"] and "<svg" in marks["unknown"] and "favicon" not in marks["unknown"])

    # A local model kept beside a cloud brain for the helpers was invisible
    # unless the menu was opened.
    chip = pg.evaluate("""() => {
      const was = { kind: STATE.brain.kind, kept: STATE.localKept };
      STATE.brain.kind = 'cloud'; STATE.localKept = 'Ornith-1.5-9B-Q4_K_M';
      mpSync();
      const el = document.getElementById('mp-helper');
      const on = { shown: !el.hidden, text: el.textContent };
      STATE.brain.kind = 'local'; mpSync();
      on.localHidden = el.hidden;
      STATE.brain.kind = was.kind; STATE.localKept = was.kept; mpSync();
      return on;
    }""")
    check(f"the sticker shows the local model kept for helpers beside a cloud brain {chip}",
          chip["shown"] and chip["text"] == "+ Ornith-1.5-9B" and chip["localHidden"])

    # A provider with no key saved had no way in from the menu.
    added = pg.evaluate("""async () => {
      mpOpen();
      const row = document.querySelector('#mp-menu .mp-row[data-v="add-provider"]');
      if (!row) return { row: false };
      row.click();
      await new Promise(r => setTimeout(r, 200));
      const out = { row: true, view: STATE.view, focused: document.activeElement && document.activeElement.classList.contains('brain-card') ? 'brain-card' : '' };
      showView('chat');
      return out;
    }""")
    check(f"the model menu has a way to a provider with no key yet {added}",
          added.get("row") and added.get("view") == "settings" and added.get("focused") == "brain-card")

    # Picking a brain in Settings was one long dropdown.
    cards = pg.evaluate("""async () => {
      showView('settings'); await refreshSettings();
      const all = [...document.querySelectorAll('#brain-cards .brain-card')].map(c => c.dataset.brain);
      document.querySelector('.brain-card[data-brain="gemini"]').click();
      const out = { all, picked: document.getElementById('setting-brain-mode').value,
                    on: (document.querySelector('.brain-card.on') || {}).dataset.brain,
                    apply: !document.getElementById('brain-apply-row').hidden };
      document.getElementById('brain-apply-row').hidden = true;
      showView('chat');
      return out;
    }""")
    # Settings showed three agents while New helper listed twenty; nothing said why.
    note = pg.evaluate("""async () => {
      STATE.helperAgents = [{ id: 'opencode', label: 'OpenCode' }, { id: 'codex', label: 'Codex' }]; STATE.moreAgents = 18;
      showView('settings'); renderBrainCards();
      const n = document.querySelector('#brain-cards .brain-note');
      const out = { text: n ? n.textContent : '' };
      n.querySelector("a").click();
      for (let i = 0; i < 40 && !document.getElementById("nh-task"); i++) await new Promise(r => setTimeout(r, 150));
      out.dialog = STATE.view === 'team' && !!document.getElementById('nh-task');
      const cancel = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Cancel' && x.offsetParent);
      if (cancel) cancel.click();
      showView('chat');
      return out;
    }""")
    check(f"Settings says the other agents work as helpers and links to them {note}",
          "OpenCode is on this PC" in note["text"] and "18 more" in note["text"] and "Codex is" not in note["text"] and note["dialog"])
    check(f"Settings shows every brain as a card, and picking one picks it {cards}",
          len(cards["all"]) == 11 and cards["picked"] == "gemini" and cards["on"] == "gemini" and cards["apply"])

    # A key pasted into Settings was put back to the old one by the next
    # settings refresh, before "Use this brain" was clicked.
    kept = pg.evaluate("""async () => {
      showView('settings');
      await refreshSettings();
      const box = document.getElementById('setting-llm-key');
      box.value = 'AQ.pasted-new-key';
      box.dispatchEvent(new Event('input'));
      await refreshSettings();
      const v = box.value;
      document.getElementById('brain-apply-row').hidden = true;
      showView('chat');
      return v;
    }""")
    check(f"a key pasted into Settings stays there through a refresh ({kept})", kept == "AQ.pasted-new-key")

    # Codex's Fast tier (quicker, uses more of the plan) had no switch anywhere.
    SETTING_POSTS.clear()
    fast = pg.evaluate("""async () => {
      const keep = { brain: { ...STATE.brain }, info: STATE.agentInfo };
      STATE.brain.providerKey = 'codex';
      STATE.agentInfo = { provider: 'codex', model: 'gpt-x', fast_on: false,
                          models: [{ id: 'gpt-x', label: 'GPT X', efforts: ['low'], fast: '2x speed, increased usage' },
                                   { id: 'gpt-slow', label: 'GPT S', efforts: ['low'], fast: '' }] };
      renderThinkMenu();
      const row = document.querySelector('#think-menu .think-fast');
      const out = { row: !!row, text: row ? row.textContent : '' };
      if (row) { row.click(); await new Promise(r => setTimeout(r, 200)); out.on = row.classList.contains('on'); out.tick = row.querySelector('.tick').textContent; }
      STATE.agentInfo.model = 'gpt-slow';
      renderThinkMenu();
      out.slowRow = !!document.querySelector('#think-menu .think-fast');
      Object.assign(STATE.brain, keep.brain); STATE.agentInfo = keep.info; renderThinkMenu();
      return out;
    }""")
    check(f"a Codex model with a Fast tier gets a Fast switch in the think menu, in Codex's words {fast}",
          fast["row"] and "2x speed" in fast["text"] and fast.get("on") and fast.get("tick") == "✓"
          and {"fast_codex": True} in SETTING_POSTS and fast["slowRow"] is False)

    # An agent update was a toast reading "Updating…" with nothing else; it
    # shows in the Downloads list now, with what it is doing and a Cancel.
    cancels = []
    pg.route("**/api/agents/jobs", lambda r: r.fulfill(json={"jobs": [
        {"id": "claude-code", "label": "Claude Code", "kind": "update", "status": "running", "started": time.time() - 12,
         "line": "changed 3 packages in 9s", "fetched": 4, "cancelled": False}]}))
    pg.route("**/api/agents/cancel", lambda r: (cancels.append(json.loads(r.request.post_data or "{}")), r.fulfill(json={"ok": True})))
    dl = pg.evaluate("""async () => {
      await agentJobsPoll();
      dlToggle(true);
      const ov = document.getElementById('downloads-overlay');
      const text = ov.textContent;
      const buttons = [...ov.querySelectorAll('button[data-dlact]')].map(b => b.dataset.dlact);
      const bar = !!ov.querySelector('.dl-indet');
      ov.querySelector('button[data-dlact="cancel"]').click();
      await new Promise(r => setTimeout(r, 300));
      dlToggle(false);
      return { text, buttons, bar };
    }""")
    pg.unroute("**/api/agents/jobs")
    pg.unroute("**/api/agents/cancel")
    # A finished update stayed in the list until the app restarted.
    pg.route("**/api/agents/jobs", lambda r: r.fulfill(json={"jobs": [
        {"id": "gemini-cli", "label": "Gemini CLI", "kind": "update", "status": "done", "started": time.time() - 60,
         "ended": time.time() - 30, "line": "changed 5 packages", "fetched": 21, "version": "0.63.0"}]}))
    gone = pg.evaluate("""async () => { _dls['agent:gemini-cli'] = { name: 'Updated Gemini CLI', kind: 'agent', status: 'done', pct: 100 };
                                         await agentJobsPoll(); return !('agent:gemini-cli' in _dls); }""")
    pg.unroute("**/api/agents/jobs")
    check("a finished update leaves the list a few seconds after it ends", gone)
    check(f"an agent update shows in Downloads with what it is doing {dl['text'][:160]!r}",
          "Updating Claude Code" in dl["text"] and "4 packages fetched" in dl["text"] and "changed 3 packages" in dl["text"] and dl["bar"])
    check(f"it can be cancelled there, and offers no pause npm couldn't keep {dl['buttons']} {cancels}",
          dl["buttons"] == ["cancel"] and cancels == [{"id": "claude-code"}])

    check(f"the page ran without a script error {errors}", not errors)

    print()
    print(f"{len(FAILS)} check(s) failed" if FAILS else "all model menu checks passed")
    sys.stdout.flush()
    # The page polls the app the whole time; closing the browser with one of
    # those in a stand-in's hands can wait for ever, so the run just ends.
    os._exit(1 if FAILS else 0)
