"""How the chat behaves in the real page.

Runs the window in Edge and drives its own functions: tool calls with nothing
said between them fold into one line and a lone tool gets the same folded row;
regenerate replaces the reply instead of adding a second turn; a slash command
typed while a reply is streaming goes to the app and not to the model; and a
file shows its lines while the call that writes it is still arriving.

    .\\.venv\\Scripts\\python.exe tests\\test_ui_chat.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time

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

work = tempfile.mkdtemp(prefix="hearth-chat-")
os.environ["JARVIS_WORKSPACE"] = work
os.environ["HEARTH_WORKSPACE"] = work
from hearth import web                                               # noqa: E402

web._save_settings(dict(web._load_settings(), onboarded=True))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

import json                                                          # noqa: E402
import urllib.request                                                # noqa: E402

vs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/voice/status", timeout=30))
check("setup is told speech is available exactly when the voice module says it is ready",
      "available" in vs["tts"] and vs["tts"]["available"] == bool(vs["tts"].get("ready")))

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="msedge")
    except Exception as e:
        print(f"skipped: no browser to run the page in ({type(e).__name__})")
        srv.shutdown()
        sys.exit(0)
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.route("**/api/llmserver/download*", lambda r: r.abort())
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(4):
        try:
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception:
            pass
    time.sleep(3)

    folded = pg.evaluate("""async () => {
      document.documentElement.dataset.ui = 'simple';
      const mk = () => { const m = document.createElement('div');
        m.innerHTML = '<div class="body"><div class="actions"></div></div>'; document.body.appendChild(m); return m; };
      const a = mk();
      for (let i = 0; i < 4; i++) {
        appendPart(makeToolCard('read_file', { path: 'a' + i }).div, a);
        appendPart(makeContentBlock(''), a);        // the model said nothing in between
      }
      const b = mk();
      const said = makeContentBlock('<p>Let me read it.</p>'); appendPart(said, b);
      appendPart(makeToolCard('read_file', { path: 'x' }).div, b);
      const after = makeContentBlock('<p>Done.</p>'); appendPart(after, b);
      await new Promise(r => setTimeout(r, 200));
      const runs = a.querySelectorAll('.tool-run');
      return { runs: runs.length, inFirst: runs[0] ? runs[0].querySelectorAll('.tool').length : 0,
               line: runs[0] ? runs[0].querySelector('.tr-sum').textContent : '',
               loneRuns: b.querySelectorAll('.tool-run').length,
               loneRaw: b.querySelectorAll('.body > .tool').length };
    }""")
    check(f"tool calls with nothing said between them are one line {folded}",
          folded["runs"] == 1 and folded["inFirst"] == 4 and folded["line"] == "Read 4 files")
    check("a tool on its own gets the same folded row, not a raw card",
          folded["loneRuns"] == 1 and folded["loneRaw"] == 0)

    regen = pg.evaluate("""async () => {
      const c = newConversation();
      const u = { role: 'user', content: 'hey' }, a = { role: 'assistant', content: '', error: 'limit hit', events: [] };
      c.messages = [u, a];
      STATE.loadedModel = { id: 'x', state: 'loaded' };
      const calls = [];
      const real = window.send;
      window.send = async (text) => { await Promise.resolve(); calls.push([text, c.messages.length]); };
      await regenerateFromMessage(a);
      await new Promise(r => setTimeout(r, 50));
      window.send = real;
      return { calls, left: c.messages.length };
    }""")
    check(f"regenerate cuts the old turn and sends once, in place {regen}",
          regen["calls"] == [["hey", 0]] and regen["left"] == 0)

    fresh = pg.evaluate("""() => {
      const y = new Date(); y.setDate(y.getDate() - 1); y.setHours(12, 0, 0, 0);
      STATE.conversations = STATE.conversations.filter(x => convoMsgCount(x));
      const old = { id: 'empty-yday', title: 'New chat', messages: [], created: +y, updated: +y };
      STATE.conversations.push(old);
      const c = newConversation();
      const label = [...document.querySelectorAll('#convo-list .convo-section-label')]
        .find(h => { let n = h.nextElementSibling;
          while (n && !n.classList.contains('convo-section-label')) {
            if (n.textContent.includes('New chat')) return true; n = n.nextElementSibling; }
          return false; });
      return { reused: c.id === old.id, under: label ? label.textContent : '' };
    }""")
    check(f"a reused empty chat is listed as new, under Today {fresh}",
          fresh["reused"] and fresh["under"] == "Today")

    tab = pg.evaluate("""() => {
      const was = [localStorage.getItem('hearth_theme'), localStorage.getItem('hearth_accent')];
      const asked = [];
      window.pywebview = { api: { set_icon: (m, a) => asked.push([m, a]) } };
      hearthSetAppearance('dark', null);
      hearthSetAppearance(null, 'sage');
      const href = document.querySelector('link[rel="icon"]').getAttribute('href');
      delete window.pywebview;
      hearthSetAppearance(was[0] || 'classic', was[1] || 'rose');
      return { href, last: asked[asked.length - 1] };
    }""")
    dict_ = pg.evaluate("""async () => {
      const realFetch = window.fetch, realSend = window.send, sent = [], stopped = [];
      rtVoiceAvailable = true;
      window.send = async (t) => { sent.push(t); };
      window.fetch = async (url, o) => {
        if (String(url).includes('/api/voice/realtime/stream')) {
          const lines = ['{"type":"warming"}', '{"type":"ready"}', '{"type":"partial","text":"remind me"}',
                         '{"type":"final","text":"remind me to stretch"}'];
          const body = new ReadableStream({ start(c) {
            lines.forEach(l => c.enqueue(new TextEncoder().encode(l + '\\n')));
            o.signal.addEventListener('abort', () => { try { c.error(new DOMException('x', 'AbortError')); } catch {} });
          } });
          return new Response(body, { status: 200 });
        }
        if (String(url).includes('/api/voice/realtime/stop')) { stopped.push(1); return new Response('{}'); }
        return new Response('{"ok":true}', { status: 200 });
      };
      const box = document.getElementById('input');
      box.value = 'note:';
      await micToggle();
      await new Promise(r => setTimeout(r, 300));
      const during = box.value, live = !!_dict;
      await micToggle();
      await new Promise(r => setTimeout(r, 50));
      const after = { text: box.value, live: !!_dict, sent: sent.length, stopped: stopped.length };
      window.fetch = realFetch; window.send = realSend; rtVoiceAvailable = null; box.value = '';
      return { during, live, after,
               bottom: document.querySelector('.voice-bottom-text').textContent,
               doing: toolDoing('run_command') };
    }""")
    check(f"the composer mic types what you say into the box, after what was there {dict_}",
          dict_["during"] == "note: remind me to stretch" and dict_["live"])
    check("and stops on a second click without sending it",
          not dict_["after"]["live"] and dict_["after"]["sent"] == 0 and dict_["after"]["stopped"] == 1
          and dict_["after"]["text"] == "note: remind me to stretch")
    check(f"voice mode promises nothing it doesn't do ({dict_['bottom']!r}) and names tools in words ({dict_['doing']!r})",
          "type any time" not in dict_["bottom"] and dict_["doing"] == "running a command…")

    boot = pg.evaluate("""() => {
      const seen = [], el = document.getElementById('voice-status-text');
      const note = () => { if (seen[seen.length - 1] !== el.textContent) seen.push(el.textContent); };
      const was = [voiceModeOn, rtVoiceActive, _voiceReady];
      voiceModeOn = true; rtVoiceActive = false; _voiceReady = false;
      setVoiceStatus('starting…'); note();
      setVoiceStatus('warming up the model… 40%'); note();
      setVoiceStatus('warming up the model…'); note();
      rtVoiceActive = true; setMicUI(); note();             // the stream opened, the listener isn't ready
      setActiveId(STATE.activeId); note();                   // a chat switch mid-start
      setVoiceStatus('getting the microphone ready…'); note();
      _voiceReady = true; setVoiceStatus('listening'); note();
      [voiceModeOn, rtVoiceActive, _voiceReady] = was;
      return seen;
    }""")
    check(f"voice start-up reads as one phase, then listening once the listener is ready {boot}",
          boot == ["getting ready…", "getting ready… 40%", "getting ready…", "listening"])

    upd = pg.evaluate("""async () => {
      const realApi = window.api, realUpd = window.updateAgent, realSave = window.saveSetting;
      const updated = [], saved = {};
      window.api = async (u) => u.includes('/api/agents/updates') ? { updates: [
        { id: 'claude-code', label: 'Claude Code', installed: '1', latest: '2' },
        { id: 'gemini-cli', label: 'Gemini CLI', installed: '1', latest: '3' },
        { id: 'opencode', label: 'OpenCode', installed: '1', latest: '4' }] } : realApi(u);
      window.updateAgent = (u) => updated.push(u.id);
      window.saveSetting = (k, v) => { saved[k] = v; };
      document.querySelectorAll('.toast').forEach(t => t.remove());
      await checkAgentUpdates();
      const toasts = document.querySelectorAll('.toast.ask').length;
      const t = document.querySelector('.toast.ask');
      t.querySelector('.mp-row[data-i="1"]').click();
      const label = t.querySelector('button[data-a="yes"]').textContent;
      t.querySelector('button[data-a="yes"]').click();
      const marks = [...document.querySelectorAll('#brain-cards .brain-upd')].map(x => x.dataset.upd);
      window.api = realApi; window.updateAgent = realUpd; window.saveSetting = realSave;
      return { toasts, label, updated, marks, skip: saved.agent_update_skip || null,
               stripe: getComputedStyle(t).borderLeftWidth };
    }""")
    check(f"several agent updates are one toast, each can be left out {upd}",
          upd["toasts"] == 1 and upd["label"] == "Update 2" and upd["updated"] == ["claude-code", "opencode"]
          and upd["skip"] == {"gemini-cli": "3"})
    check("and the toast has no coloured side stripe", upd["stripe"] == "1px")
    check(f"an agent's card keeps an update mark whatever the toast was told {upd['marks']}",
          "claude-code" in upd["marks"])

    pops = pg.evaluate("""() => {
      const was = Object.assign({}, STATE.settings || {}), out = {};
      const count = () => document.querySelectorAll('#toasts .toast').length;
      for (const mode of ['all', 'needed', 'none']) {
        document.querySelectorAll('#toasts .toast').forEach(t => t.remove());
        STATE.settings = Object.assign({}, was, { popups: mode });
        toast('Remembered 1 thing', 'a' + mode, 'ok', 60000);
        toast('Mic fell back', 'b' + mode, 'warn', 60000);
        askToast('An update', 'c', 'Update', () => {});
        out[mode] = count();
      }
      STATE.settings = Object.assign({}, was, { popups: undefined, mute_toasts: true });
      document.querySelectorAll('#toasts .toast').forEach(t => t.remove());
      toast('x', 'old switch', 'warn', 60000);
      out.oldMute = count();
      document.querySelectorAll('#toasts .toast').forEach(t => t.remove());
      STATE.settings = was;
      return out;
    }""")
    check(f"pop-ups: all shows everything, only-what-needs-you drops plain notices, none shows nothing {pops}",
          pops == {"all": 3, "needed": 2, "none": 0, "oldMute": 0})

    marks = pg.evaluate("""() => {
      const was = STATE.brain, out = {};
      const read = () => { const m = document.getElementById('model-agent'), d = document.getElementById('model-state');
        return { hidden: m.hidden, cls: (m.querySelector('.brain-mark') || {}).className || '',
                 hx: (m.querySelector('.brain-mark') || { style: { getPropertyValue: () => '' } }).style.getPropertyValue('--hx'),
                 dot: !d.hidden }; };
      STATE.brain = Object.assign({}, was, { kind: 'cloud', providerKey: 'anthropic', loaded: true, busy: false });
      applyBrainState(); out.cloud = read();
      STATE.brain = Object.assign({}, was, { kind: 'local', providerKey: 'local', loaded: false, busy: false });
      applyBrainState(); out.none = read();
      STATE.brain = Object.assign({}, was, { kind: 'local', providerKey: 'local', loaded: true, busy: true });
      applyBrainState(); out.loading = read();
      STATE.brain = was; applyBrainState();
      return out;
    }""")
    check(f"the sticker shows the brain's own mark, not a status dot {marks}",
          not marks["cloud"]["hidden"] and marks["cloud"]["hx"] == "#d97757" and not marks["cloud"]["dot"]
          and "local off" in marks["none"]["cls"] and not marks["none"]["dot"]
          and marks["loading"]["dot"] and marks["loading"]["hidden"])

    at = pg.evaluate("""async () => {
      const realApi = window.api, realFetch = window.fetch, sentBodies = [];
      window.api = async (u, o) => {
        if (u.startsWith('/api/mention')) return { items: [
          { kind: 'chat', name: 'Launch warroom', id: 'c_launch', sub: 'yesterday · 40 messages' },
          { kind: 'folder', name: 'launch-assets', path: 'C:/x/launch-assets', sub: 'C:/x' }] };
        if (u.startsWith('/api/read-chat')) return { text: 'Chat: Launch warroom\\n#39 assistant\\nship it friday' };
        return realApi(u, o);
      };
      window.fetch = async (u, o) => {
        if (String(u) === '/chat') { sentBodies.push(o && o.body); return new Response('', { status: 200 }); }
        return realFetch(u, o);
      };
      newConversation();
      const box = document.getElementById('input');
      box.value = 'carry on from @launch'; box.setSelectionRange(box.value.length, box.value.length);
      box.dispatchEvent(new Event('input'));
      await new Promise(r => setTimeout(r, 150));
      const menu = document.getElementById('slash-menu');
      const shown = { open: !menu.hidden, rows: menu.querySelectorAll('.slash-item.mention').length,
                      groups: [...menu.querySelectorAll('.slash-group')].map(g => g.textContent) };
      box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await new Promise(r => setTimeout(r, 150));
      const after = { text: box.value, chips: _pendingAttachments.map(a => a.kind + ':' + a.name), menu: !menu.hidden };
      STATE.loadedModel = STATE.loadedModel || { id: 'x', state: 'loaded' };
      STATE.streaming = false;
      await send();
      await new Promise(r => setTimeout(r, 200));
      window.api = realApi; window.fetch = realFetch;
      const body = sentBodies[0] || '';
      const left = _pendingAttachments.length;
      _pendingAttachments = []; _renderAttachTray(); STATE.streaming = false;
      return { shown, after, left, carried: body.includes('ship it friday') && body.includes('Attached chat') };
    }""")
    check(f"@ in the box lists chats and folders to attach {at['shown']}",
          at["shown"]["open"] and at["shown"]["rows"] == 2 and at["shown"]["groups"] == ["Chats", "Folders and files"])
    check(f"picking one takes the @word out and puts it in the tray {at['after']}",
          at["after"]["text"] == "carry on from " and at["after"]["chips"] == ["chat:Launch warroom"]
          and not at["after"]["menu"])
    check("and an attached chat goes with the message, read in order", at["carried"] and at["left"] == 0)

    check(f"changing the accent changes the tab and window icon {tab}",
          "accent=sage" in tab["href"] and "theme=dark" in tab["href"] and tab["last"] == ["dark", "sage"])

    slash = pg.evaluate("""async () => {
      const steered = [];
      const real = window.steerMessage;
      window.steerMessage = (t) => steered.push(t);
      STATE.streaming = true;
      document.getElementById('input').value = '/help';
      await send();
      const modal = !!document.querySelector('.modal-bg, .modal');
      document.getElementById('input').value = 'actually use tabs';
      STATE.streamingConvo = STATE.activeId;
      await send();
      // A message typed in another chat while this one replies isn't a steer.
      STATE.streamingConvo = 'some-other-chat';
      document.getElementById('input').value = 'new question elsewhere';
      await send();
      const kept = document.getElementById('input').value;
      STATE.streaming = false; STATE.streamingConvo = null;
      document.getElementById('input').value = '';
      window.steerMessage = real;
      return { steered, modal, kept };
    }""")
    check(f"a slash command typed mid-reply is not sent to the model {slash}",
          slash["steered"] == ["actually use tabs"] and slash["modal"])
    # /status had to wait for the reply, opened every brain's usage at once, and never refreshed.
    stat = pg.evaluate("""async () => {
      const real = api, asked = [];
      window.api = async (p) => p.startsWith('/api/status') ? (asked.push(p), {
        brain: { id: 'claude-code', label: 'Claude Code', model: 'opus', kind: 'cloud', where: 'your Claude plan' },
        context: 1000000, mode: 'ask', version: 'x', memories: 3, workspace: 'W', helpers: { working: 0, done: 0 },
        local: { running: false }, limits: [
          { id: 'claude-code', label: 'Claude Code', kind: 'plan', plan: 'Claude', at: Date.now() / 1000,
            windows: [{ name: '5-hour', used: 40, resets_at: Date.now() / 1000 + 3600 }] },
          { id: 'codex', label: 'Codex', kind: 'plan', plan: 'ChatGPT', at: Date.now() / 1000,
            windows: [{ name: 'Weekly', used: 72, resets_at: Date.now() / 1000 + 86400 }] }] }) : real(p);
      STATE.streaming = true;
      document.getElementById('input').value = '/status';
      await send();
      await new Promise(r => setTimeout(r, 300));
      const body = document.getElementById('status-pop');
      const opened = !body.hidden && !!body.querySelector('.st-panel') && !document.getElementById('modal-body').querySelector('.st-panel');
      const bars = [...body.querySelectorAll('.st-bar')].filter(b => !/Context used/.test(b.textContent)).length;
      const folded = [...body.querySelectorAll('.st-other')].map(b => b.textContent.trim());
      body.querySelector('#st-refresh').click();
      await new Promise(r => setTimeout(r, 300));
      const fresh = asked.some(p => p.includes('fresh=1'));
      STATE.streaming = false;
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      const closed = body.hidden;
      window.api = real;
      return { opened, bars, folded, fresh, closed };
    }""")
    # A model loaded from Models & engine showed a static line; the bar was only on the sticker.
    mload = pg.evaluate("""async () => {
      showView('models');
      await new Promise(r => setTimeout(r, 300));
      modelsSetStartingLock(true, 'Loading Big-Model.gguf…');
      showLoadProgress('loading_weights', 45, '');
      const fill = document.getElementById('mlb-fill'), pct = document.getElementById('mlb-pct');
      const out = { fill: fill && fill.style.width, pct: pct && pct.textContent,
                    phase: (document.getElementById('mlb-phase') || {}).textContent };
      hideLoadProgress(); modelsSetStartingLock(false); showView('chat');
      return out;
    }""")
    check(f"a model loaded from Models & engine shows its phase and progress there {mload}",
          mload["fill"] == "45%" and mload["pct"] == "45%" and mload["phase"])
    # 90K loaded read as 88K, and the ring between turns called itself an estimate
    # though the last turn had measured it.
    ctxs = pg.evaluate("""() => [fmtCtxPill(90000), fmtCtxPill(32768), fmtCtxPill(1048576), fmtCtxPill(977000)]""")
    check(f"context sizes read as they were set {ctxs}", ctxs == ["90K", "32K", "1M", "977K"])
    check(f"/status opens mid-reply, with only the brain in use open and a refresh {stat}",
          stat["opened"] and stat["closed"] and stat["bars"] == 1 and len(stat["folded"]) == 1 and "72%" in stat["folded"][0] and stat["fresh"])
    check(f"a message typed in another chat while one replies isn't sent to that reply {slash}",
          slash["kept"] == "new question elsewhere" and "new question elsewhere" not in slash["steered"])

    partial = pg.evaluate("""() => [
      _partialJsonString('{"path": "notes.txt", "content": "line one\\\\nline \\\\"two\\\\" so f', 'content'),
      _partialJsonString('{"path": "notes.txt", "cont', 'content'),
      _partialJsonString('{"file_path": "C:\\\\\\\\a\\\\\\\\b.txt", "content": ""}', 'file_path')]""")
    check(f"a file's text is read out of a call that is still arriving {partial}",
          partial == ['line one\nline "two" so f', None, "C:\\a\\b.txt"])

    # The window takes which brain is in use from the app, so the app is the
    # one that has to say an agent is the brain for this part.
    web._save_settings({"llm_provider": "claude-code"})
    room = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      STATE.settings = Object.assign({}, STATE.settings || {}, { llm_provider: 'claude-code' });
      Object.assign(STATE.brain, deriveBrainKind());
      applyBrainState();
      showView('team');
      await wait(2500);
      // The room loads its art first; on a slow start JARVIS isn't in it yet.
      // A busy machine loads it slower; give it a minute, then say so plainly.
      for (let i = 0; i < 240 && !(ROOM.people && ROOM.people.get('__main')); i++) await wait(250);
      if (!(ROOM.people && ROOM.people.get('__main'))) return { missing: true };
      const out = { tab: TEAM.tab };
      setState('calling read_file…');
      await wait(400);
      out.doing = (ROOM.says.get('__main') || {}).text;
      setState('thinking…');
      await wait(400);
      out.thinking = (ROOM.says.get('__main') || {}).text;
      showInlinePermission('req-1', 'run_command', { command: 'dir' });
      await wait(400);
      out.asking = (ROOM.says.get('__main') || {}).text;
      const strip = document.getElementById('perm-strip');
      out.stripInRoom = !strip.hidden && strip.nextElementSibling === document.getElementById('tm-compose');
      // The brand's own icon, or its drawn mark while that loads or when it can't.
      out.mark = !!document.querySelector('#tm-labels .tm-label .hx-mini svg, #tm-labels .tm-label .hx-mini img.hx-fav.brand');
      out.markWhy = { brain: mainBrainMark(), provider: (STATE.settings || {}).llm_provider,
                      label: (document.querySelector('#tm-labels .tm-label') || {}).innerHTML || '' };
      // A name tag between two pixels makes its border shimmer as people walk.
      { const m = ROOM.people.get('__main'); m.pos = [m.pos[0] + 0.37, m.pos[1] + 0.29]; ROOM.labelSig = ''; roomDraw(performance.now()); }
      const sb = document.getElementById('tm-labels').getBoundingClientRect();
      out.whole = [...document.querySelectorAll('#tm-labels .tm-label')].map(e => {
        const r = e.getBoundingClientRect(); return [r.left - sb.left, r.top - sb.top]; });
      hideInlinePermission();
      // Thinking: paces along one line, turning round, instead of standing.
      const me = ROOM.people.get('__main');
      setState('thinking…');
      const g1 = roomMainGoal(me);
      await wait(3600);
      const g2 = roomMainGoal(me);
      out.pace = [g1.pose, g1.at[1] === g2.at[1], Math.abs(g1.at[0] - g2.at[0]) >= 150];
      setState('calling memory_save…');
      out.saving = [roomMainLine(), JSON.stringify(roomMainGoal(me).at) === JSON.stringify(ROOM.spots.pin)];
      roomMemoryHint('memory_recall', '## operating-system-windows  (user, score=22)\\nAll commands run in PowerShell.');
      out.recalled = (ROOM.says.get('__main') || {}).text;
      // Done: answers where it stands, and the card says where it is.
      const here = [me.pos[0], me.pos[1]];
      setState('idle');
      const held = roomMainGoal(me);
      out.holds = JSON.stringify(held.at) === JSON.stringify(here);
      me.path = [];
      out.word = roomMainIdleWord(me);
      me.holdUntil = 0; me.path = [[1, 1]];
      out.walkingWord = roomMainIdleWord(me);
      me.path = [];
      // Speaking, it faces the room even where it would be looking out of a window.
      ROOM.intent = { spot: 'windowSeat', pose: 'gaze', until: Date.now() + 9000 };
      me.pos = [...ROOM.spots.windowSeat]; me.holdUntil = 0;
      ROOM.says.delete('__main');
      await wait(300);
      out.gazing = me.goal.pose;
      roomSay('__main', 'Here is what I found.', 3000);
      await wait(300);
      out.speaking = me.goal.pose;
      ROOM.intent = null;
      // The app loading or ejecting a model is not JARVIS thinking.
      ROOM.says.delete('__main');
      out.lines = ['loading model…', 'ejecting…', 'ready', 'thinking…'].map(st => { setState(st); return roomMainLine(); });
      setState('idle');
      // Poses only one character was drawn in; the other stands in the nearest it has.
      const idx = n => ROOM.man.frames.indexOf(n);
      out.frames = [roomFrame('sleep', 'main') >= ROOM.man.frames.length, roomFrame('sleep', 'main_m') === idx('gaze'),
                    roomFrame('talk', 'coder') === idx('idle'), roomFrame('read', 'main_m') === idx('read')];
      // With nothing to think with it sleeps in the armchair; the one with no such pose stays up.
      const hasBrain = roomHasBrain;
      roomHasBrain = () => false;
      me.holdUntil = 0; me.wasBusy = false;
      const asleep = roomMainGoal(me);
      localStorage.setItem('hearth_room_look', 'him');
      const awake = roomMainGoal(me);
      localStorage.removeItem('hearth_room_look');
      roomHasBrain = hasBrain;
      out.sleep = [asleep.pose, !!asleep.sit, awake.pose !== 'sleep', !awake.sit];
      out.helperLine = typeof roomHelperLine === 'function'
        && roomHelperLine({ status: 'working', activity: 'tool', tool: 'read_file', what: 'SPEC.md' });
      // Writing, it always took the first desk, and each question about a command
      // sent it walking to the rug and back.
      me.holdUntil = 0; me.desk = null; me.wasBusy = true; me.room = 0;
      me.pos = [ROOM.desks[6][0], ROOM.desks[6][1] + 80];
      setState('calling write_file…');
      roomMainGoal(me);
      out.nearDesk = me.desk;
      showInlinePermission('req-2', 'run_command', { command: 'dir' });
      const ask = roomMainGoal(me);
      out.askHere = [ask.pose, JSON.stringify(ask.at) === JSON.stringify(roomSeat(me)), !!ask.seated];
      hideInlinePermission();
      setState('idle'); me.desk = null;
      showView('chat');
      out.stripBack = !document.getElementById('view-team').contains(strip);
      return out;
    }""")
    web._save_settings({"llm_provider": "local"})
    if room.get("missing"):
        sys.exit("FAIL  JARVIS never appeared in the Team room within a minute (its art didn't load)")
    check(f"in the room JARVIS says what it is doing, not dots {room}",
          room["doing"] == "Reading a file…" and room["thinking"] == "Thinking…")
    check("a permission question is asked in the room, with the approval strip right there",
          room["asking"] == "I need to run a command for this. Okay?" and room["stripInRoom"] and room["stripBack"])
    check(f"JARVIS works at the free desk nearest it and asks about a command from there {room.get('nearDesk')} {room.get('askHere')}",
          room.get("nearDesk") == 6 and room.get("askHere") == ["type", True, True])
    check(f"JARVIS's tag carries the mark of the brain it is running on {room.get('markWhy')}", room["mark"])
    check(f"name tags sit on whole pixels {room['whole']}",
          room["whole"] and all(abs(v - round(v)) < 0.01 for xy in room["whole"] for v in xy))
    check(f"it paces while it thinks instead of standing in one spot {room['pace']}", room["pace"] == ["think", True, True])
    check(f"saving a memory takes it to the board, and it says so {room['saving']}",
          room["saving"] == ["Noting that down…", True])
    check("what it recalls comes to mind out loud", room["recalled"] == "Ah… operating system windows.")
    check("it answers where it stands, and the card says where it is, not where it is headed",
          room["holds"] and room["word"] == "right here" and room["walkingWord"] == "on the way")

    check(f"it looks out of the window with its back turned, and turns to the room to speak {room['gazing'], room['speaking']}",
          room["gazing"] == "gaze" and room["speaking"] == "talk")
    check("a working helper's tag says what it has in its hands", room["helperLine"] == "Reading SPEC.md")
    check(f"a pose a character wasn't drawn in falls back to the nearest it has {room['frames']}", all(room["frames"]))
    check(f"with no model loaded it is asleep in the armchair, if it was drawn asleep {room['sleep']}",
          room["sleep"] == ["sleep", True, True, True])
    check(f"loading or ejecting a model doesn't show as thinking {room['lines']}",
          room["lines"] == ["Waking up…", "", "", "Thinking…"])

    stats = pg.evaluate("""() => {
      const l = makeStatsLine({ model: 'F:\\\\Models\\\\Ornith-1.5-9B-Q4_K_M.gguf', decode_tps: 26, duration_ms: 4200 });
      const c = makeStatsLine({ model: 'claude-opus-5-5', duration_ms: 9000, cost_usd: 0.027 });
      return [l.textContent, c.textContent];
    }""")
    check(f"a reply's footer names the model that wrote it {stats}",
          stats[0].startswith("Ornith-1.5-9B-Q4_K_M") and "26 tok/s gen" in stats[0]
          and stats[1].startswith("claude-opus-5-5") and stats[1].endswith("$0.027"))

    check(f"the page raised no errors {errors[:2]}", not errors)
    # First-run setup offers the agents already on the PC as a brain, and names
    # the other agent programs found as helpers.
    reply = lambda body: (lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(body)))
    pg.route("**/api/llmserver/status", reply({"disk_models": [], "is_lite": True}))
    pg.route("**/api/models", reply({"models": [], "agent_brains": [
        {"id": "claude-code", "label": "Claude Code", "installed": True, "plan": "Claude"},
        {"id": "codex", "label": "Codex", "installed": False, "plan": "ChatGPT"}]}))
    pg.route("**/api/team/choices", reply({"roles": [], "agents": [
        {"id": "claude-code", "label": "Claude Code"}, {"id": "gemini-cli", "label": "Gemini CLI", "refused": "ended"},
        {"id": "opencode", "label": "OpenCode", "refused": ""}]}))
    setup = pg.evaluate("""async () => {
      obState = { brain: '', voice: '', cloud: { provider: 'local', key: '', model: '' } };
      await obLoadBrains();
      const picks = [...document.querySelectorAll('#ob-brain-choices .ob-choice')];
      picks[0].click();
      return { titles: picks.map(b => b.querySelector('.ob-choice-title').textContent),
               tip: document.getElementById('ob-brain-tip').textContent,
               banner: document.getElementById('ob-brain-status').textContent, picked: JSON.parse(obState.brain) };
    }""")
    for pat in ("**/api/llmserver/status", "**/api/models", "**/api/team/choices"):
        pg.unroute(pat)
    check(f"setup offers an agent that is installed as the brain, and picking it is remembered {setup['titles']}",
          setup["titles"] == ["Claude Code", "Codex"] and setup["picked"] == {"kind": "agent", "id": "claude-code"})
    check("it names the other agents found as helpers and leaves out one its account refused",
          "OpenCode" in setup["tip"] and "Gemini CLI" not in setup["tip"])
    # One that isn't installed is set up from a dialog: install, sign in, use.
    state = {"installed": False, "signed_in": None}
    asked: list = []

    def setup_api(route):
        path = route.request.url.split("/api/agents/")[1].split("?")[0]
        if route.request.method == "POST":
            asked.append(path)
            if path == "install":
                state["installed"], state["signed_in"] = True, False
            if path == "signin":
                state["signed_in"] = True
            return route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"agents": [dict(state, id="codex", label="Codex")]}))

    pg.route("**/api/agents/**", setup_api)
    pg.route("**/api/brain/agent", lambda r: (asked.append("use"), r.fulfill(
        status=200, content_type="application/json", body='{"ok": true, "provider": "codex"}'))[1])
    dialog = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      const steps = () => [...document.querySelectorAll('#modal-body .as-step')].map(e => e.className.replace('as-step ', ''));
      const press = async () => { document.querySelector('#modal-body .as-step.now button').click(); };
      agentSetup('codex');
      await wait(500);
      const out = { first: steps(), button: document.querySelector('#modal-body .as-step.now button').textContent };
      await press(); await wait(700);
      out.afterInstall = steps();
      await press(); await wait(4200);
      out.afterSignIn = steps();
      await press(); await wait(500);
      out.closed = !document.getElementById('modal').classList.contains('open');
      return out;
    }""")
    pg.unroute("**/api/agents/**"); pg.unroute("**/api/brain/agent")
    check(f"an agent that isn't here is set up from one dialog: install, then sign in, then use {dialog}",
          dialog["first"] == ["now", "todo", "todo"] and dialog["button"] == "Install"
          and dialog["afterInstall"] == ["done", "now", "todo"] and dialog["afterSignIn"] == ["done", "done", "now"]
          and dialog["closed"] and asked == ["install", "signin", "use"])
    # The sign-in window closed without finishing: the dialog sat on
    # "Waiting for you…" for minutes, and the agent couldn't be used meanwhile.
    state2 = {"installed": True, "signed_in": False, "signing": None}

    def closed_api(route):
        path = route.request.url.split("/api/agents/")[1].split("?")[0]
        if route.request.method == "POST":
            state2["signing"] = False             # the window is opened and closed straight away
            return route.fulfill(status=200, content_type="application/json", body='{"ok": true, "paste": "right-click"}')
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"agents": [dict(state2, id="claude-code", label="Claude Code")]}))

    pg.route("**/api/agents/**", closed_api)
    gave_up = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      agentSetup('claude-code');
      await wait(500);
      document.querySelector('#modal-body .as-step.now button').click();
      await wait(800);
      const hint = (document.querySelector('#modal-body .as-note') || {}).textContent || '';
      await wait(3600);
      const note = (document.querySelector('#modal-body .as-note') || {}).textContent || '';
      const button = (document.querySelector('#modal-body .as-step.now button') || {}).textContent || '';
      document.getElementById('modal').classList.remove('open');
      return { hint, note, button };
    }""")
    pg.unroute("**/api/agents/**")
    check(f"the dialog says how to paste the code, and stops waiting once the window closes {gave_up}",
          "right-click" in gave_up["hint"] and "closed before it finished" in gave_up["note"]
          and gave_up["button"] == "Sign in")
    check("on Lite it points at the agent instead of a dead end", "Pick an agent below" in setup["banner"])
    pg.route("**/api/llmserver/status", reply({"disk_models": [{"filename": "a.gguf", "path": "a", "size_gb": 5, "source": "Hearth"},
                                                                {"filename": "b.gguf", "path": "b", "size_gb": 9, "source": "Hearth"}]}))
    pg.route("**/api/models", reply({"models": [], "agent_brains": [
        {"id": "claude-code", "label": "Claude Code", "installed": True, "plan": "Claude"},
        {"id": "codex", "label": "Codex", "installed": True, "plan": "ChatGPT"}]}))
    # Agents that only take tasks as helpers were left out of the count.
    pg.route("**/api/team/choices", reply({"roles": [], "agents": [
        {"id": "claude-code", "label": "Claude Code"}, {"id": "codex", "label": "Codex"},
        {"id": "opencode", "label": "OpenCode"}, {"id": "gemini-cli", "label": "Gemini CLI"}]}))
    media = pg.evaluate("""async () => {
      showView('chat');
      const c = newConversation();
      c.messages = [{ role: 'user', content: 'look at my screen and draw a cat' }, { role: 'assistant', content: 'Here it is.', events: [
        { kind: 'tool', name: 'view_image', args: { path: 'C:/w/shot.png' }, result: '__JARVIS_IMAGE__ C:/w/shot.png (10 bytes, png)', pos: 0 },
        { kind: 'media', mediaKind: 'image', path: 'C:/w/shot.png', pos: 0 },
        { kind: 'tool', name: 'generate_image', args: { prompt: 'a cat' }, result: '__JARVIS_IMAGE__ C:/w/generated/cat.png (10 bytes, png)', pos: 0 },
        { kind: 'media', mediaKind: 'image', path: 'C:/w/generated/cat.png', pos: 0 },
        { kind: 'done', duration_ms: 3000 }] }];
      renderChat();
      await new Promise(r => setTimeout(r, 100));
      const msg = [...document.querySelectorAll('#chat-body .msg.assistant')].pop();
      const shown = [...msg.querySelectorAll('.gen-media-img')].map(i => i.getAttribute('src'));
      const barText = (msg.querySelector('.gen-media-bar') || {}).textContent || '';
      openLightbox('/file?path=x', 'image', 'C:/Users/someone/Jarvis/generated/cat.png');
      const lb = document.querySelector('.lightbox');
      const out = { shown, barText: barText.trim(), closeIcon: !!lb.querySelector('.lightbox-close svg'),
                    caption: lb.querySelector('.lightbox-caption').textContent };
      closeLightbox();
      return out;
    }""")
    check(f"a picture the model only looked at stays out of the chat; what it made shows {media['shown']}",
          len(media["shown"]) == 1 and "cat.png" in media["shown"][0])
    check(f"the picture has no toolbar text, the viewer a line-icon close and only the file name {media}",
          media["barText"] == "" and media["closeIcon"] and media["caption"] == "cat.png")

    fprev = pg.evaluate("""async () => {
      const realApi = window.api, out = {};
      window.api = async (u, o) => u.includes('op=read') ? { content: '<b>not bold</b> a < b' } : realApi(u, o);
      const body = () => document.getElementById('modal-body');
      await filePreview('notes.txt', 'notes.txt');
      out.txt = [body().querySelector('pre') ? body().querySelector('pre').textContent : '', !!body().querySelector('b')];
      await filePreview('page.html', 'out/page.html');
      const fr = body().querySelector('iframe');
      out.html = fr ? [fr.getAttribute('sandbox'), fr.getAttribute('src')] : null;
      await filePreview('deck.pptx', 'deck.pptx');
      out.office = [...document.querySelectorAll('#modal-actions button')].map(b => b.textContent);
      document.getElementById('modal').classList.remove('open');
      window.api = realApi;
      return out;
    }""")
    check(f"a file's markup shows as text, not as part of the page {fprev['txt']}",
          fprev["txt"] == ["<b>not bold</b> a < b", False])
    check(f"a web page opens as itself in a sandbox that runs nothing {fprev['html']}",
          fprev["html"] == ["", "/file?path=out%2Fpage.html"])
    check(f"an Office file offers the app it belongs to {fprev['office']}", "Open in its app" in fprev["office"])

    pasted = pg.evaluate("""async () => {
      const box = document.getElementById('input');
      box.value = 'look at this:';
      _pendingAttachments = []; _renderAttachTray();
      const long = Array.from({ length: 120 }, (_, i) => 'line ' + i).join('\\n');
      const dt = new DataTransfer(); dt.setData('text/plain', long);
      box.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
      const short = new DataTransfer(); short.setData('text/plain', 'just a word');
      const ev2 = new ClipboardEvent('paste', { clipboardData: short, bubbles: true, cancelable: true });
      box.dispatchEvent(ev2);
      await new Promise(r => setTimeout(r, 50));
      const out = { box: box.value, chips: _pendingAttachments.map(a => a.name), shortKept: !ev2.defaultPrevented };
      document.querySelector('#attach-tray > div').click();
      await new Promise(r => setTimeout(r, 50));
      out.opened = (document.getElementById('modal-body').textContent || '').includes('line 119');
      document.getElementById('modal').classList.remove('open');
      _pendingAttachments = []; _renderAttachTray(); box.value = '';
      // A reply that used the web shows where it came from.
      showView('chat');
      const c = newConversation();
      c.messages = [{ role: 'user', content: 'who won' }, { role: 'assistant', content: 'Team A won.', events: [
        { kind: 'tool', name: 'web_search', args: { query: 'who won' }, result: '1. https://www.bbc.com/sport/x\\n2. https://espn.com/y', pos: 0 },
        { kind: 'tool', name: 'web_fetch', args: { url: 'https://www.bbc.com/sport/x' }, result: '...', pos: 0 },
        { kind: 'done', duration_ms: 3000, stats: { duration_ms: 3000 } }] }];
      renderChat();
      await new Promise(r => setTimeout(r, 100));
      const msg = [...document.querySelectorAll('#chat-body .msg.assistant')].pop();
      const chip = msg.querySelector('.src-chip');
      out.src = chip ? chip.firstElementChild.nextElementSibling.textContent + '|' + (chip.querySelector('em') || {}).textContent : '';
      out.links = chip ? chip.querySelectorAll('.src-list a').length : 0;
      return out;
    }""")
    check(f"a long paste becomes a chip that opens it, a short one stays in the box {pasted}",
          pasted["box"] == "look at this:" and pasted["chips"] == ["Pasted text · 120 lines"]
          and pasted["shortKept"] and pasted["opened"])
    check(f"a reply that used the web shows its sources ({pasted['src']}, {pasted['links']})",
          pasted["src"] == "bbc.com|+1" and pasted["links"] == 2)

    get = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      const realApi = window.api, realDl = window.modelsDownloadHF, realVoice = window._obFetchVoiceModels;
      const started = [], asked = [];
      let items = [
        { id: 'model', title: 'Ornith-1.5-9B', sub: '9.2B, fits your 8 GB card', size_gb: 5.04, repo: 'r/m', filename: 'm.gguf' },
        { id: 'voice', title: "JARVIS's voice and hearing", sub: 'Speaking and listening', size_gb: 0.48 }];
      window.api = async (u, o) => u.startsWith('/api/setup/what-to-get') ? (asked.push(u), { items }) : realApi(u, o);
      window.modelsDownloadHF = (repo, f) => started.push('model:' + f);
      window._obFetchVoiceModels = () => started.push('voice');
      obState = { brain: '', voice: 'full', cloud: { provider: 'local', key: '', model: '' } };
      document.getElementById('onboard').classList.add('open');
      obStep = 4; obRender(); await wait(200);
      document.getElementById('ob-next').click(); await wait(400);
      const out = { step: obStep, asked: asked[0] || '', rows: document.querySelectorAll('#ob-get .mp-row').length,
                    total: document.getElementById('ob-get-total').textContent };
      document.querySelector('#ob-get .mp-row[data-i="1"]').click();
      out.totalAfter = document.getElementById('ob-get-total').textContent;
      document.getElementById('ob-next').click(); await wait(300);
      out.started = started.slice(); out.after = obStep;
      // Nothing needed: the page passes itself, going forward and coming back.
      items = [];
      obStep = 4; obRender(); await wait(200);
      document.getElementById('ob-next').click(); await wait(500);
      out.skipFwd = obStep;
      document.getElementById('ob-back').click(); await wait(500);
      out.skipBack = obStep;
      window.api = realApi; window.modelsDownloadHF = realDl; window._obFetchVoiceModels = realVoice;
      document.getElementById('onboard').classList.remove('open');
      obState = { brain: '', voice: '', cloud: { provider: 'local', key: '', model: '' } };
      return out;
    }""")
    check(f"setup offers what this PC still needs, with sizes {get}",
          get["step"] == 5 and "local=1" in get["asked"] and "voice=1" in get["asked"] and get["rows"] == 2
          and "5.5 GB" in get["total"] and "5.0 GB" in get["totalAfter"])
    check("Continue fetches only what's ticked, and setup carries on",
          get["started"] == ["model:m.gguf"] and get["after"] == 6)
    check(f"with nothing to get the page passes itself both ways ({get['skipFwd']}, {get['skipBack']})",
          get["skipFwd"] == 6 and get["skipBack"] == 4)

    flow = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      obState = { brain: '', voice: '', cloud: { provider: 'local', key: '', model: '' } };
      await obLoadBrains();
      const out = { banner: document.getElementById('ob-brain-status').textContent };
      obStep = 2; obRender(); await wait(300);
      document.getElementById('ob-next').click(); await wait(300);
      out.noPick = obStep;
      document.getElementById('ob-back').click(); await wait(300);
      obState.brain = JSON.stringify({ kind: 'agent', id: 'claude-code' });
      document.getElementById('ob-next').click(); await wait(300);
      out.picked = obStep;
      document.getElementById('ob-back').click(); await wait(300);
      out.back = obStep;
      // How much to show, set in setup, shows the same in Settings; and the other way round.
      const on = (box) => (document.querySelector(box + ' .on') || { dataset: {} }).dataset;
      hearthSetInterface('advanced'); await wait(100);
      out.fromSetup = [on('#ap-ui').ui, on('#ob-uimode').uimode];
      document.querySelector('#ap-ui [data-ui="simple"]').click(); await wait(100);
      out.fromSettings = [on('#ap-ui').ui, on('#ob-uimode').uimode];
      obState = { brain: '', voice: '', cloud: { provider: 'local', key: '', model: '' } };
      return out;
    }""")
    for pat in ("**/api/llmserver/status", "**/api/models", "**/api/team/choices"):
        pg.unroute(pat)
    check(f"setup counts every agent it found, helpers too, and the model files {flow['banner']!r}",
          "4 agents you already use and 2 model files" in flow["banner"])
    check(f"after a brain is picked the cloud page is passed over, both ways {flow}",
          flow["noPick"] == 3 and flow["picked"] == 4 and flow["back"] == 2)
    check("Simple or Advanced set in one place shows the same in the other",
          flow["fromSetup"] == ["advanced", "advanced"] and flow["fromSettings"] == ["simple", "simple"])

    extras = pg.evaluate("""async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      const out = {};
      // Which character, picked anywhere, shows as picked everywhere.
      const on = (box) => (document.querySelector(box + ' .on') || { dataset: {} }).dataset;
      hearthSetRoomLook('him'); await wait(100);
      out.him = [on('#ap-room').look, on('#ob-roomlook').roomlook];
      hearthSetRoomLook('her'); await wait(100);
      out.her = [on('#ap-room').look, on('#ob-roomlook').roomlook];
      // The tour: a ring and a card, Next moves on, Skip ends it for good.
      startTour(); await wait(300);
      const title = () => (document.querySelector('#tour-card b') || {}).textContent;
      out.tour = [title(), !!document.getElementById('tour-ring')];
      document.querySelector('#tour-card [data-t="next"]').click(); await wait(300);
      out.tour.push(title());
      document.querySelector('#tour-card [data-t="skip"]').click(); await wait(100);
      out.tour.push(!!document.getElementById('tour-ring'), localStorage.getItem('hearth_tour_done'));
      // A voice test reads a different line each time.
      out.quotes = (await voiceQuote('am_michael')).text !== (await voiceQuote('am_michael')).text
        && /[ऀ-ॿ]/.test((await voiceQuote('hm_omega')).text);
      // While a local model is loading, the eject button stops the load.
      STATE.brain.kind = 'local'; STATE.brain.loaded = false;
      showLoadProgress('loading_weights', 12, '');
      out.stop = [document.getElementById('btn-eject').hidden, document.getElementById('btn-eject-label').textContent];
      hideLoadProgress();
      out.eject = [document.getElementById('btn-eject').hidden, document.getElementById('btn-eject-label').textContent];
      return out;
    }""")
    check(f"the character picked in one place shows as picked in the other {extras['him'], extras['her']}",
          extras["him"] == ["him", "him"] and extras["her"] == ["her", "her"])
    check(f"the tour points at one thing at a time, moves on, and can be skipped {extras['tour']}",
          extras["tour"] == ["What I think with", True, "Talk to me", False, "1"])
    check("a voice test reads a different line each time, in the voice's own language", extras["quotes"])
    check(f"while a model is loading the eject button stops it {extras['stop'], extras['eject']}",
          extras["stop"] == [False, "stop"] and extras["eject"] == [True, "eject"])

    # An agent brain runs tools side by side: both start, then both finish.
    stream = "\n".join(json.dumps(e) for e in [
        {"type": "tool_call", "name": "read_file", "args": {"path": "a.txt"}, "id": "a"},
        {"type": "tool_call", "name": "grep_search", "args": {"pattern": "x"}, "id": "b"},
        {"type": "tool_result", "name": "read_file", "content": "what a.txt says", "ms": 5, "id": "a"},
        {"type": "tool_result", "name": "grep_search", "content": "3 matches", "ms": 7, "id": "b"},
        {"type": "assistant_chunk", "content": "Both done."},
        {"type": "assistant", "content": "Both done."},
        {"type": "done", "duration_ms": 10, "iterations": 1, "reason": "finished"}]) + "\n"
    pg.route("**/chat", lambda r: r.fulfill(status=200, content_type="application/x-ndjson", body=stream))
    paired = pg.evaluate("""async () => {
      const c = newConversation();
      STATE.loadedModel = { id: 'x', state: 'loaded' };
      document.getElementById('input').value = 'read both';
      await send();
      await new Promise(r => setTimeout(r, 400));
      const m = c.messages[c.messages.length - 1];
      return (m.events || []).filter(e => e.kind === 'tool').map(e => [e.name, e.result]);
    }""")
    pg.unroute("**/chat")
    panel = pg.evaluate("""async () => {
      showView('team');
      await new Promise(r => setTimeout(r, 2500));
      roomPoke(ROOM.people.get('__main'));
      await new Promise(r => setTimeout(r, 400));
      const side = document.getElementById('tm-side');
      const out = { shown: !side.hidden, asked: (side.querySelector('.tm-i-asked') || {}).textContent,
                    steps: [...side.querySelectorAll('.tm-steps li span')].map(e => e.textContent),
                    said: (side.querySelector('.tm-i-result') || {}).textContent };
      showView('chat');
      return out;
    }""")
    check(f"clicking JARVIS in the room opens a panel with what it was asked and each step it took {panel}",
          panel["shown"] and "read both" in (panel["asked"] or "") and panel["steps"] == ["Read a.txt", "Searched for x"]
          and "Both done." in (panel["said"] or ""))
    check(f"with two tools running at once, each result lands on its own call {paired}",
          paired == [["read_file", "what a.txt says"], ["grep_search", "3 matches"]])

    # A helper's word arrived mid-sentence and cut it in two: "Team is" / card / "working."
    stream = "\n".join(json.dumps(e) for e in [
        {"type": "assistant_chunk", "content": "Team is"},
        {"type": "subagent_done", "agent_id": "sub_1_aaaaaa", "persona": "coder", "name": "Haiku", "status": "update",
         "summary": "a fact", "result_text": "a fact"},
        {"type": "assistant_chunk", "content": " working. Next"},
        {"type": "assistant_chunk", "content": " step."},
        {"type": "assistant", "content": "Team is working. Next step."},
        {"type": "done", "duration_ms": 10, "iterations": 1, "reason": "finished"}]) + "\n"
    pg.route("**/chat", lambda r: r.fulfill(status=200, content_type="application/x-ndjson", body=stream))
    split = pg.evaluate("""async () => {
      const c = newConversation();
      STATE.loadedModel = { id: 'x', state: 'loaded' };
      document.getElementById('input').value = 'start the team';
      await send();
      await new Promise(r => setTimeout(r, 400));
      const msgs = [...document.querySelectorAll('.msg')];
      const last = msgs[msgs.length - 1];
      const parts = [...last.querySelectorAll('.content, .subagent-card, [class*=subagent]')].map(e =>
        e.className.includes('subagent') ? 'CARD' : e.textContent.trim()).filter(Boolean);
      return parts;
    }""")
    pg.unroute("**/chat")
    check(f"a helper's card waits for the sentence to end instead of splitting it {split}",
          split and split[0].startswith("Team is working.") and "CARD" in split)

    # A chat had in the terminal never showed in the window, and turns added there never reached it.
    from hearth import chat_sync
    tid = chat_sync.new_id()
    chat_sync.append(tid, [{"role": "user", "content": "rename the photos"}, {"role": "assistant", "content": "Done, 12 renamed."}])
    shared = pg.evaluate("""async (tid) => {
      await syncConvosFromDisk();
      const row = document.querySelector(`.convo[data-cid="${tid}"]`);
      const out = { listed: !!row, mark: row && row.title };
      if (row) row.click();
      await new Promise(r => setTimeout(r, 300));
      return out;
    }""", tid)
    time.sleep(1.1)
    chat_sync.append(tid, [{"role": "user", "content": "and the videos?"}, {"role": "assistant", "content": "Those too."}])
    later = pg.evaluate("""async () => { await syncConvosFromDisk(); await new Promise(r => setTimeout(r, 200));
      return [...document.querySelectorAll('.msg')].map(m => m.textContent).join(' | '); }""")
    check(f"a chat had in the terminal shows in the window, marked, and keeps up with it {shared} {later[-80:]!r}",
          shared["listed"] and shared["mark"] == "Started in the terminal" and "Those too." in later)

    # Something said part-way showed as green text until the model took it,
    # then vanished; later there was no way to see what had changed course.
    steer_stream = "\n".join(json.dumps(e) for e in [
        {"type": "assistant_chunk", "content": "Writing it with spaces. "},
        {"type": "steered", "text": "use tabs instead"},
        {"type": "assistant_chunk", "content": "Switched to tabs."},
        {"type": "assistant", "content": "Writing it with spaces. Switched to tabs."},
        {"type": "done", "duration_ms": 10, "iterations": 1, "reason": "finished"}]) + "\n"
    pg.route("**/chat", lambda r: r.fulfill(status=200, content_type="application/x-ndjson", body=steer_stream))
    steer = pg.evaluate("""async () => {
      const c = newConversation();
      STATE.loadedModel = { id: 'x', state: 'loaded' };
      document.getElementById('input').value = 'write the file';
      await send();
      await new Promise(r => setTimeout(r, 400));
      const live = [...document.querySelectorAll('#chat-body .steer-note .steer-text')].map(e => e.textContent);
      const saved = (c.messages[c.messages.length - 1].events || []).filter(e => e.kind === 'steer').map(e => e.text);
      renderChat();
      await new Promise(r => setTimeout(r, 200));
      const again = [...document.querySelectorAll('#chat-body .steer-note .steer-text')].map(e => e.textContent);
      return { live, saved, again };
    }""")
    pg.unroute("**/chat")
    check(f"something said part-way stays in the reply, saved with the chat {steer}",
          steer["live"] == ["use tabs instead"] and steer["saved"] == ["use tabs instead"]
          and steer["again"] == ["use tabs instead"])

    # Facts the background pass saved after a reply had finished never reached
    # the window: the reply's stream was already closed. The state poll carries them.
    from hearth import memory_extract as _mx
    _mx.NOTICED.append({"at": time.time(), "titles": ["favourite editor"]})
    remembered = pg.evaluate("""async () => {
      STATE._factsSeen = 0;
      await refreshState();
      const toasts = [...document.querySelectorAll('.toast')].map(t => t.textContent);
      const p = ROOM.people.get('__main');
      const goal = p ? roomMainGoal(p) : null;
      const again = (await refreshState(), [...document.querySelectorAll('.toast')].filter(t => /Remembered/.test(t.textContent)).length);
      return { toast: toasts.some(t => /Remembered 1 fact/.test(t) && /favourite editor/.test(t)),
               board: !!goal && (goal.pose === 'pin' || goal.at === ROOM.spots.board), once: again === 1 };
    }""")
    check(f"a fact remembered in the background is shown, and JARVIS pins it to the board {remembered}",
          remembered["toast"] and remembered["board"] and remembered["once"])
    matched = pg.evaluate("""async () => {
      STATE.settings.voice_name = 'am_michael';
      hearthSetRoomLook('her'); await new Promise(r => setTimeout(r, 300));
      const toHer = STATE.settings.voice_name;
      STATE.settings.voice_name = 'bf_emma';
      hearthSetRoomLook('him'); await new Promise(r => setTimeout(r, 300));
      const kept = STATE.settings.voice_name;
      hearthSetRoomLook('her');
      return [toHer, kept];
    }""")
    check(f"picking her swaps the stock male voice for the stock female one, and a chosen voice stays {matched}",
          matched == ["af_heart", "bf_emma"])
    # Nobody walks through furniture: every route between two places in the
    # room keeps a body's width clear of the floor each piece stands on.
    crossings = pg.evaluate("""() => {
      const fp = [];
      ROOM.desks.forEach((d, i) => {
        fp.push({ name: 'desk' + i, x0: d[0] - 112, x1: d[0] + 112, y0: d[1] - 95, y1: d[1] + 2 });
        fp.push({ name: 'chair' + i, x0: d[0] - 30, x1: d[0] + 30, y0: d[1] - 8, y1: d[1] + 20 });
      });
      fp.push({ name: 'armchair', x0: 472, x1: 608, y0: 395, y1: 442 }, { name: 'side_table', x0: 622, x1: 678, y0: 410, y1: 430 },
              { name: 'books', x0: 434, x1: 470, y0: 436, y1: 448 }, { name: 'stool', x0: 1217, x1: 1259, y0: 404, y1: 420 });
      const places = Object.entries(ROOM.spots);
      ROOM.desks.forEach((d, i) => places.push(['seat' + i, roomSeat({ desk: i })]));
      const own = (name, at) => (at.startsWith('seat') && (name === 'chair' + at.slice(4) || name === 'desk' + at.slice(4)))
        || (at === 'armchairSeat' && name === 'armchair');
      const hits = new Set();
      for (const [an, a] of places) for (const [bn, b] of places) {
        if (an === bn) continue;
        const path = [a, ...roomRoute(a, b)];
        for (let i = 1; i < path.length; i++) {
          const [x0, y0] = path[i - 1], [x1, y1] = path[i];
          const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 4));
          for (let k = 0; k <= n; k++) {
            const x = x0 + (x1 - x0) * k / n, y = y0 + (y1 - y0) * k / n;
            for (const f of fp) if (!own(f.name, an) && !own(f.name, bn) && x + 14 > f.x0 && x - 14 < f.x1 && y > f.y0 && y < f.y1)
              hits.add(f.name + ': ' + an + ' to ' + bn);
          }
        }
      }
      const beside = [[460, 440], [650, 425], [1238, 412]].map(q => roomFree(q));
      return { hits: [...hits].slice(0, 6), count: hits.size, beside };
    }""")
    check(f"nobody walks through furniture on the way anywhere {crossings['hits']}", crossings["count"] == 0)
    check(f"a place picked on top of furniture moves in front of it {crossings['beside']}",
          crossings["beside"][0][1] > 448 and crossings["beside"][1][1] > 430 and crossings["beside"][2][1] > 420)

    # Only eight helpers could be in the room; the rest were never shown.
    # Further ones work in the next room, one picture at a time.
    rooms = pg.evaluate("""async () => {
      const now = Date.now() / 1000;
      const list = [...Array(12)].map((_, i) => ({ id: 'sub_many_' + i, persona: 'coder', status: 'working', updated: now }));
      roomSync(list);
      const where = list.map(a => ROOM.people.get(a.id)).filter(Boolean).map(p => p.room || 0);
      const pills = () => [...document.querySelectorAll('#tm-rooms button')].map(b => b.textContent);
      const shown = !document.getElementById('tm-rooms').hidden;
      const before = pills();
      for (const a of list) { const q = ROOM.people.get(a.id); q.enterAt = 0; q.waiting = false; q.pos = [700, 760]; }
      roomDraw(performance.now());
      const drawn0 = ROOM.hits.filter(h => h.p && h.p.id.startsWith('sub_many_')).length;
      ROOM.people.get('sub_many_11').agent && roomPoke(ROOM.people.get('sub_many_11'));
      const view = ROOM.view;
      roomDraw(performance.now());
      const drawn1 = ROOM.hits.filter(h => h.p && h.p.id.startsWith('sub_many_')).map(h => ROOM.people.get(h.p.id).room);
      roomShow(0);
      const strip = document.getElementById('tm-strip'), was = strip.style.cssText, viewWas = STATE.view;
      const roomBox = document.getElementById('tm-room'), roomHidden = roomBox.hidden;
      showView('team'); roomBox.hidden = false;
      strip.style.cssText = 'width: 300px; display: flex; overflow-x: auto;';
      strip.scrollLeft = 0;
      strip.dispatchEvent(new WheelEvent('wheel', { deltaY: 240, bubbles: true, cancelable: true }));
      const scrolled = strip.scrollLeft;
      strip.style.cssText = was; roomBox.hidden = roomHidden; showView(viewWas || 'chat');
      for (const a of list) ROOM.people.delete(a.id);
      roomStripRender();
      return { first: where.filter(r => r === 0).length, second: where.filter(r => r === 1).length,
               shown, before, drawn0, view, drawn1: [...new Set(drawn1)], after: document.getElementById('tm-rooms').hidden, scrolled };
    }""")
    check(f"twelve helpers: eight in the first room, four in the next {rooms}",
          rooms["first"] == 8 and rooms["second"] == 4)
    check("a switcher shows the rooms in use, with how many are in each",
          rooms["shown"] and len(rooms["before"]) == 2 and rooms["before"][1].endswith("4"))
    check("only the room in view is drawn, and clicking someone shows their room",
          rooms["drawn0"] == 8 and rooms["view"] == 1 and rooms["drawn1"] == [1])
    check("with one room in use the switcher goes away", rooms["after"])
    check(f"a mouse wheel scrolls the row of cards sideways ({rooms['scrolled']})", rooms["scrolled"] > 0)

    # The home room is picked in Appearance: JARVIS lives there and the others
    # follow. Moving between rooms is a pixel wipe unless Instant is picked.
    home = pg.evaluate("""() => {
      hearthSetRoomHome('winter');
      const first = ROOM.rooms[roomStyle(0)].id, second = ROOM.rooms[roomStyle(1)].id;
      const card = !!document.querySelector('#ap-home .ap-mode.on[data-home="winter"]');
      hearthSetRoomHome('cabin');
      hearthSetRoomWipe('pixel');
      const cv = document.getElementById('tm-canvas');
      if (!cv.width) { cv.width = 320; cv.height = 210; }
      ROOM.view = 0; roomShow(1);
      const wiped = !!ROOM.wipe;
      ROOM.wipe = null; document.getElementById('tm-stage').classList.remove('tm-wipe-hide');
      hearthSetRoomWipe('instant');
      roomShow(0);
      const instant = !ROOM.wipe && ROOM.view === 0;
      hearthSetRoomWipe('pixel');
      return { first, second, card, wiped, instant };
    }""")
    check(f"a home room picked in Appearance comes first, the cabin after it {home}",
          home["first"] == "winter" and home["second"] == "cabin" and home["card"])
    check("a room change wipes in, or is instant when that is picked", home["wiped"] and home["instant"])

    # Picking a room didn't offer the accent that goes with it.
    offer = pg.evaluate("""async () => {
      for (let i = 0; i < 50 && !document.querySelector('#ap-home .ap-mode[data-home="winter"]'); i++) await new Promise(r => setTimeout(r, 100));
      const btn = document.querySelector('#ap-home .ap-mode[data-home="winter"]');
      if (!btn) return { btn: false };
      const was = { theme: localStorage.getItem('hearth_theme'), accent: localStorage.getItem('hearth_accent'), home: localStorage.getItem('hearth_room_home') };
      localStorage.setItem('hearth_theme', 'dark'); localStorage.setItem('hearth_accent', 'rose');
      btn.click();
      const toastEl = [...document.querySelectorAll('#toasts > *')].find(el => /Ocean goes well/.test(el.textContent));
      const yes = toastEl && toastEl.querySelector('button[data-a="yes"]');
      if (yes) yes.click();
      const accent = localStorage.getItem('hearth_accent');
      hearthSetAppearance(was.theme || 'classic', was.accent || 'rose');
      hearthSetRoomHome(was.home || 'cabin');
      return { btn: true, offered: !!toastEl, accent };
    }""")
    check(f"picking a room offers the accent that goes with it {offer}",
          offer.get("btn") and offer.get("offered") and offer.get("accent") == "ocean")
    # Switching rooms one after another stacked an offer toast for each.
    stack = pg.evaluate("""async () => {
      document.querySelectorAll('#toasts > *').forEach(el => el.remove());
      localStorage.setItem('hearth_theme', 'dark'); localStorage.setItem('hearth_accent', 'rose');
      for (const id of ['winter', 'library', 'winter', 'library']) document.querySelector(`#ap-home .ap-mode[data-home="${id}"]`).click();
      const offers = [...document.querySelectorAll('#toasts > *')].filter(el => /goes well/.test(el.textContent)).length;
      document.querySelectorAll('#toasts > *').forEach(el => el.remove());
      hearthSetRoomHome('cabin');
      document.querySelector('#ap-accents .ap-accent[data-accent="sage"]').click();
      const room = [...document.querySelectorAll('#toasts > *')].find(el => /Library room goes well/.test(el.textContent));
      document.querySelectorAll('#toasts > *').forEach(el => el.remove());
      hearthSetAppearance('classic', 'rose'); hearthSetRoomHome('cabin');
      return { offers, room: !!room };
    }""")
    check(f"switching rooms shows one offer at a time, and an accent offers its room {stack}",
          stack["offers"] == 1 and stack["room"])

    # The logs were raw rows from the first build: a call and its result as two
    # lines, tools named only by their code name, the server as raw text.
    logs = pg.evaluate("""() => {
      const { items, starts } = _lgItems([
        { ts: '2026-10-07T10:00:00', event: 'server_start' },
        { ts: '2026-10-07T10:00:01', event: 'call', tool: 'run_command', args: { command: 'echo hi' } },
        { ts: '2026-10-07T10:00:02', event: 'result', tool: 'run_command', ms: 40 },
        { ts: '2026-10-07T10:00:03', event: 'call', tool: 'read_file', args: { path: 'C:/x/notes.txt' } },
        { ts: '2026-10-07T10:00:04', event: 'error', tool: 'read_file', error: 'not found' },
        { ts: '2026-10-07T10:00:05', event: 'call', tool: 'list_directory', args: { path: 'C:/y' } },
        { ts: '2026-10-07T10:00:06', event: 'result', tool: 'list_directory', ms: 3, ok: false, preview: 'Error: not found: C:/y' }]);
      const srv = _srvRequests('t | task 7 | prompt eval time = 1000.00 ms / 2000 tokens (0.5 ms per token, 2000.00 tokens per second)\\n'
        + 't | task 7 |        eval time = 2000.00 ms / 60 tokens (33 ms per token, 30.00 tokens per second)\\n'
        + 't | task 7 |       total time = 3000.00 ms / 2060 tokens');
      return { n: items.length, starts, first: [items[0].state, items[0].ms], second: [items[1].state, items[1].got], third: [items[2].state, items[2].got],
               plain: _lgLine('system_info', {}, true), ran: _lgLine('run_command', { command: 'echo hi' }, true), srv };
    }""")
    check(f"the agent log pairs each call with what came back {logs}",
          logs["n"] == 3 and logs["starts"] == 1 and logs["first"] == ["ok", 40] and logs["second"] == ["err", "not found"])
    check(f"a tool that answered with an error counts as one, with what it said {logs.get('third')}",
          logs["third"] == ["err", "Error: not found: C:/y"])
    srvw = pg.evaluate("""() => {
      const log = ["0.02.202.498 I srv    load_model: loading model 'F:\\\\Models\\\\Ornith-1.5-9B-Q4_K_M.gguf'",
        "0.02.679.161 W common_fit_params: failed to fit params to free device memory: n_gpu_layers already set by user to 33, abort",
        "0.03.055.519 W model has unused tensor blk.32.attn_norm.weight (size = 16384 bytes) -- ignoring",
        "0.03.055.534 W model has unused tensor blk.32.post_attention_norm.weight (size = 16384 bytes) -- ignoring",
        "0.03.055.562 W model has unused tensor blk.32.attn_q.weight (size = 18874368 bytes) -- ignoring",
        "0.38.515.764 I srv    load_model: initializing, n_slots = 1, n_ctx_slot = 71680, kv_unified = 'false'",
        "0.38.558.929 I srv  llama_server: model loaded",
        "0.38.558.936 I srv  llama_server: listening on http://127.0.0.1:1234"].join('\\r\\n');   // as Windows writes it
      const w = _srvWhat(log), box = document.createElement('div');
      box.innerHTML = _srvWhatHTML(w, true);
      return { model: w.model, ctx: w.ctx, at: w.at, secs: Math.round(w.loadedIn),
               probs: [...w.problems.values()].map(p => p.n), text: box.textContent };
    }""")
    check(f"the server page says what it loaded, how long it took and its warnings once each {srvw}",
          srvw["model"] == "Ornith-1.5-9B-Q4_K_M" and srvw["ctx"] == 71680 and srvw["at"] == "127.0.0.1:1234"
          and srvw["secs"] == 36 and sorted(srvw["probs"]) == [1, 3] and "3 times" in srvw["text"])
    # The Reminders tab was the first build's: a hint line running off the
    # right edge, the same hint again when empty, times as "in 3h".
    rem = pg.evaluate("""async () => {
      const real = api, soon = new Date(Date.now() + 25 * 60e3), later = new Date(Date.now() + 26 * 3600e3);
      const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60e3).toISOString().slice(0, 19);
      let items = [
        { id: 'r1', what: 'Stand up and stretch', due_iso: iso(soon), fired: false },
        { id: 'r2', what: 'Weather for the walk', due_iso: iso(later), fired: false, recurring_seconds: 86400,
          recurring_text: 'daily', action_tool: 'web_search', action_args: { query: 'weather today' } },
        { id: 'r3', what: 'Call home', due_iso: iso(new Date(Date.now() - 3600e3)), fired: true, fired_at: iso(new Date(Date.now() - 3600e3)) }];
      let ideas = [];
      window.api = async (path) => path.startsWith('/api/reminders/ideas') ? { ideas }
        : path.startsWith('/api/reminders') ? { items } : real(path);
      RM_IDEAS.list = null; RM_IDEAS.asked = 0;
      showView('reminders');
      await refreshReminders();
      const box = $('reminders-box'), view = $('view-reminders');
      const up = [...box.querySelectorAll('.rm-row .what')].map(x => x.innerText.split('\\n').join(' | '));
      const fits = view.scrollWidth <= view.clientWidth + 1;
      document.querySelector('[data-rm="done"]').click(); await new Promise(r => setTimeout(r, 50));
      const done = [...box.querySelectorAll('.rm-row')].length;
      items = [];
      document.querySelector('[data-rm="upcoming"]').click(); await new Promise(r => setTimeout(r, 50));
      await new Promise(r => setTimeout(r, 50));
      const none = $('rm-ideas').querySelectorAll('[data-rmask]').length;
      const learn = ($('rm-ideas').querySelector('.rm-learn') || {}).textContent || '';
      ideas = [{ title: 'Exam form', ask: 'Remind me tomorrow at 10am to finish the exam form' }];
      RM_IDEAS.list = null; RM_IDEAS.asked = 0;
      await refreshReminders(); await new Promise(r => setTimeout(r, 50));
      const examples = [...$('rm-ideas').querySelectorAll('[data-rmask]')].map(x => x.dataset.rmask);
      window.api = real;
      return { up, fits, done, none, learn, examples, count: $('rm-n-up').textContent };
    }""")
    check(f"reminders say when and what happens in words, and fit the window {rem}",
          len(rem["up"]) == 2 and "in 25 min" in rem["up"][0] and "then runs web search: weather today" in rem["up"][1]
          and "Repeats daily" in rem["up"][1] and rem["fits"])
    check(f"done ones have their own list; ideas are the server's, made from what was talked about, none when it has none {rem['examples']}",
          rem["done"] == 1 and rem["none"] == 0 and rem["examples"] == ["Remind me tomorrow at 10am to finish the exam form"])
    check(f"with no ideas yet, the page says they come as JARVIS learns how you work {rem['learn']!r}",
          "learns how you work" in rem["learn"])
    # The next turn's history had each reply's text only, so the model decided it
    # had never run the tools it ran.
    hist = pg.evaluate("""() => historyOf([
      { role: 'user', content: 'how big is Downloads?' },
      { role: 'assistant', content: 'About 12 GB.', events: [
        { kind: 'think', content: 'measure it' },
        { kind: 'tool', id: 't1', name: 'disk_usage', args: { path: '~/Downloads' }, result: '12.4 GB' }] },
      { role: 'user', content: 'thanks' }])""")
    check(f"a reply's tool steps go to the next turn as the calls and results they were {[h['role'] for h in hist]}",
          [h["role"] for h in hist] == ["user", "assistant", "tool", "assistant", "user"]
          and hist[1]["tool_calls"][0]["function"]["name"] == "disk_usage"
          and hist[2]["tool_call_id"] == "t1" and hist[2]["content"] == "12.4 GB" and hist[3]["content"] == "About 12 GB.")
    # Messages and chats carried no time at all.
    times = pg.evaluate("""() => {
      const y = Date.now() - 864e5, t = Date.now();
      STATE.conversations.unshift({ id: 'times', title: 'Over two days', created: y, updated: t, messages: [
        { role: 'user', content: 'old one', at: y }, { role: 'assistant', content: 'Old reply.', at: y + 1000 },
        { role: 'user', content: 'new one', at: t }, { role: 'assistant', content: 'New reply.', at: t + 1000 },
        { role: 'user', content: 'from before times were kept' }] });
      setActiveId('times'); renderConvoList(); renderChat();
      const body = $('chat-body');
      const row = document.querySelector('.convo[data-cid="times"] .convo-when');
      return { days: [...body.querySelectorAll('.chat-day')].map(x => x.textContent),
               stamps: [...body.querySelectorAll('.msg')].map(x => !!x.querySelector('.msg-time')),
               first: (body.querySelector('.msg-time') || {}).textContent, when: row && row.textContent };
    }""")
    check(f"each message says when it was said, with a line where the day changes, and each chat when it was last used {times}",
          times["days"] == ["Yesterday", "Today"] and times["stamps"] == [True, True, True, True, False]
          and any(ch.isdigit() for ch in times["first"] or "") and any(ch.isdigit() for ch in times["when"] or ""))
    # A question JARVIS asked left nothing in the chat but a raw tool line.
    ask = pg.evaluate("""() => {
      const q = { question: 'Which folder?', options: ['Downloads', 'Desktop', 'Documents'] };
      STATE.conversations.unshift({ id: 'ask', title: 'Asked', created: Date.now(), updated: Date.now(), messages: [
        { role: 'user', content: 'clean up' },
        { role: 'assistant', content: 'Downloads it is.', events: [
          { kind: 'tool', name: 'ask_user', args: q, result: '{"ok": true, "choice": "Downloads", "other": false}', pos: 0 }] },
        { role: 'user', content: 'again' },
        { role: 'assistant', content: 'Your own folder then.', events: [
          { kind: 'tool', name: 'ask_user', args: q, result: '{"ok": true, "choice": "F:/Stuff", "other": true}', pos: 0 }] },
        { role: 'user', content: 'and again' },
        { role: 'assistant', content: 'You were away.', events: [
          { kind: 'tool', name: 'ask_user', args: q, result: '{"ok": false, "error": "user did not answer within 3 minutes"}', pos: 0 }] }] });
      setActiveId('ask'); renderChat();
      const cards = [...$('chat-body').querySelectorAll('.ask-card')];
      const first = cards[0]; const folded = !first.classList.contains('open');
      first.querySelector('.ak-sub').click();
      return { n: cards.length, folded, opened: first.classList.contains('open'),
               inRun: cards.some(c => !!c.closest('.tool-run')),
               states: cards.map(c => c.querySelector('.ak-state').textContent),
               q: first.querySelector('.ak-q').textContent,
               picked: cards.map(c => [...c.querySelectorAll('.ak-opt.sel .ak-lab')].map(x => x.textContent)),
               opts: first.querySelectorAll('.ak-opt').length };
    }""")
    check(f"a question JARVIS asked stays in the chat as a card with every option and the one picked {ask}",
          ask["n"] == 3 and ask["folded"] and ask["opened"] and not ask["inRun"]
          and ask["states"] == ["Answered", "Answered", "Not answered"] and ask["q"] == "Which folder?"
          and ask["opts"] == 3 and ask["picked"] == [["Downloads"], ["F:/Stuff"], []])
    # Setup's "How should I work?" step said what each approval mode does, and
    # showed nothing of it. It plays one task out with the chat's own parts.
    demo = pg.evaluate("""async () => {
      const seen = {};
      const real = window.setTimeout;
      window.setTimeout = (f, ms, ...a) => real(f, Math.min(ms || 0, 5), ...a);   // the same timeline, fast
      for (const mode of ['ask', 'edits', 'auto']) {
        obState.approval = mode; obStep = 7; $('onboard').classList.add('open');
        const strip = document.querySelector('#ob-demo .obd-strip');
        const asked = [];
        const mo = new MutationObserver(() => { const t = strip.querySelector('.perm-tool'); if (t && !asked.includes(t.textContent)) asked.push(t.textContent); });
        mo.observe(strip, { childList: true, subtree: true });
        document.querySelector('#onboard .ob-card').classList.add('ob-wide');
        const p = obDemoStart();
        for (let i = 0; i < 200 && !document.querySelector('#ob-demo .obd-feed').textContent.includes('Done.'); i++) await new Promise(r => real(r, 10));
        mo.disconnect(); obDemoStop(); await p;
        seen[mode] = asked;
      }
      window.setTimeout = real;
      $('onboard').classList.remove('open');
      const feed = document.querySelector('#ob-demo .obd-feed');
      return { seen, cards: feed.querySelectorAll('.tool').length, mode: document.querySelector('#ob-demo .obd-mode').textContent };
    }""")
    check(f"setup plays each approval mode out: what asks first and what just runs {demo}",
          demo["seen"] == {"ask": ["edit_file", "run_command", "delete_path"], "edits": ["run_command", "delete_path"],
                           "auto": ["delete_path"]} and demo["cards"] == 3 and demo["mode"] == "Auto")
    # The card widens on that step, and the picked option's highlight stayed
    # where the narrow card had put it, half over the next option.
    fit = pg.evaluate("""async () => {
      $('onboard').classList.remove('open');
      document.querySelector('#onboard .ob-card').classList.remove('ob-wide');
      const off = [];
      const measure = (how) => {
        for (const id of ['ob-approval', 'ob-look', 'ob-uimode']) {
          const box = $(id), hl = box.querySelector('.ob-seg-hl'), on = box.querySelector('button.on');
          if (!hl || !on) { off.push(`${how} ${id} missing`); continue; }
          const a = hl.getBoundingClientRect(), b = on.getBoundingClientRect();
          if (Math.abs(a.left - b.left) > 2 || Math.abs(a.width - b.width) > 2) off.push(`${how} ${id} ${Math.round(a.left)}/${Math.round(b.left)} ${Math.round(a.width)}/${Math.round(b.width)}`);
        }
      };
      obStep = 6; obShow(); await new Promise(r => setTimeout(r, 300));
      obStep = 7; obRender();
      // The Continue button's hover colour finishes before the card has widened.
      await new Promise(r => setTimeout(r, 30));
      $('ob-next').dispatchEvent(new TransitionEvent('transitionend', { propertyName: 'background-color', bubbles: true }));
      await new Promise(r => setTimeout(r, 1500));
      measure('next');
      // Setup reopened where it was left (a saved draft) lands right on the step.
      obDemoStop(); $('onboard').classList.remove('open');
      document.querySelector('#onboard .ob-card').classList.remove('ob-wide');
      obStep = 7; obShow();
      // A button's own colour change ends before the card has finished widening.
      await new Promise(r => setTimeout(r, 30));
      document.querySelector('#ob-look button').dispatchEvent(new TransitionEvent('transitionend', { propertyName: 'border-color', bubbles: true }));
      await new Promise(r => setTimeout(r, 1500));
      measure('reopened');
      obDemoStop(); $('onboard').classList.remove('open');
      const run = makeToolRun();
      run._cards.appendChild(makeToolCard('delete_path', { path: 'a' }).div);
      run._cards.appendChild(makeToolCard('move_path', { src: 'b', dst: 'c' }).div);
      return { wide: document.querySelector('#onboard .ob-card').classList.contains('ob-wide'), off, line: _summarizeRun(run) || run.textContent };
    }""")
    check(f"once the setup card has widened, each picked option's highlight sits on it {fit['off']}",
          fit["wide"] and fit["off"] == [])
    check(f"a delete and a move are said as such in the folded line, not as reading a file {fit['line']!r}",
          "deleted" in str(fit["line"]).lower() and "moved" in str(fit["line"]).lower() and "read" not in str(fit["line"]).lower())
    # Setup said nothing of the name being the user's, picked no accent, and
    # showed nothing of the room characters.
    extras = pg.evaluate("""async () => {
      const real = window.setTimeout;
      window.setTimeout = (f, ms, ...a) => real(f, Math.min(ms || 0, 5), ...a);
      const sleep = (ms) => new Promise(r => real(r, ms));
      const el = document.querySelector('.ob-step[data-step="1"] .ob-agent-name');
      const names = [];
      const mo = new MutationObserver(() => { if (!names.includes(el.textContent)) names.push(el.textContent); });
      mo.observe(el, { childList: true, characterData: true, subtree: true });
      $('onboard').classList.add('open'); obStep = 1; obRender();
      for (let i = 0; i < 300 && !(names.length > 2 && el.textContent === 'JARVIS'); i++) await sleep(10);
      mo.disconnect();
      const final = el.textContent;
      // The preview speaks with the name typed in the step before.
      $('ob-agent-name').value = 'Friday';
      obStep = 7; obRender(); await sleep(50);
      hearthSetAppearance('dark');
      document.querySelector('#ob-accents [data-accent="sage"]').click();
      const accent = [document.documentElement.dataset.accent, document.querySelector('#ob-accents .on').dataset.accent];
      obState.approval = 'ask';
      const p = obDemoStart();
      for (let i = 0; i < 200 && !document.querySelector('#ob-demo .obd-strip .perm-strip'); i++) await sleep(10);
      const said = [document.querySelector('#ob-demo .obd-ph').textContent,
                    (document.querySelector('#ob-demo .msg.assistant .role-name') || {}).textContent,
                    (document.querySelector('#ob-demo .obd-strip [data-agent-name]') || {}).textContent];
      obDemoStop(); await p;
      document.querySelector('#ob-roomlook [data-roomlook="him"]').click();
      for (let i = 0; i < 200 && !document.querySelector('#ob-demo .obd-guy').style.backgroundImage; i++) await sleep(10);
      const guy = document.querySelector('#ob-demo .obd-guy');
      const room = { on: document.querySelector('#ob-demo .obd-win').classList.contains('room'), sheet: guy.style.backgroundImage,
                     cap: document.querySelector('#ob-demo .obd-cap').textContent };
      document.querySelector('#ob-approval [data-approval="edits"]').click();
      const back = document.querySelector('#ob-demo .obd-win').classList.contains('room');
      obDemoStop(); window.setTimeout = real;
      $('ob-agent-name').value = ''; $('onboard').classList.remove('open');
      return { names, final, accent, said, room, back };
    }""")
    check(f"setup's first line tries on other names before settling on the one in use {extras['names']}",
          len(extras["names"]) >= 3 and extras["final"] == "JARVIS" and "FRIDAY" in extras["names"])
    check(f"setup's look offers the accent, the same setting as Settings {extras['accent']}", extras["accent"] == ["sage", "sage"])
    check(f"the preview talks as the name typed a step before {extras['said']}",
          extras["said"] == ["Message Friday…", "friday", "Friday"])
    check(f"picking a room look plays that character in the room, and an approval pick goes back to the chat {extras['room']}",
          extras["room"]["on"] and "main_m.png" in extras["room"]["sheet"] and extras["room"]["cap"].startswith("Teal scarf")
          and extras["back"] is False)
    # About said a bare address for the brain (LM Studio's, with Claude Code in
    # use) and an LM Studio line to everyone; "Another server" kept the agent
    # brain's internal address in its URL box.
    about = pg.evaluate("""async () => {
      showView('settings');
      for (let i = 0; i < 60 && ($('setting-endpoint').textContent || '…') === '…'; i++) await new Promise(r => setTimeout(r, 50));
      const mode = $('setting-brain-mode'), url = $('setting-llm-url');
      url.value = 'claude-code://local';
      mode.value = 'custom'; mode.dispatchEvent(new Event('change'));
      const cleared = url.value;
      url.value = 'http://192.168.1.5:8080/v1';
      mode.value = 'custom'; mode.dispatchEvent(new Event('change'));
      const kept = url.value;
      showView('chat');
      const st = await api('/api/state');
      const lmsAround = !!st.lms_cli || /:1234/.test(st.endpoint || '');
      return { said: $('setting-endpoint').textContent, lms: $('field-lms').hidden === !lmsAround, cleared, kept };
    }""")
    check(f"About names the brain in words, LM Studio's line only when it is around, and Another server starts empty {about}",
          not about["said"].startswith("http") and about["lms"] is True and about["cleared"] == ""
          and about["kept"] == "http://192.168.1.5:8080/v1")
    # A request the provider refused for a moment was retried in silence: the
    # window looked stuck while the terminal said "retrying 1/5".
    retried = pg.evaluate("""async () => {
      const lines = [
        { type: 'retry', attempt: 1, max_attempts: 5, wait_s: 2, category: 'rate_limit', message: 'busy' },
        { type: 'retry', attempt: 2, max_attempts: 5, wait_s: 4, category: 'rate_limit', message: 'busy' },
        { type: 'assistant_chunk', content: 'Here it is.' }, { type: 'assistant', content: 'Here it is.' },
        { type: 'done', duration_ms: 10, iterations: 1, reason: 'finished' }];
      const realFetch = window.fetch;
      // The reply arrives a piece at a time, the way a real one does.
      window.fetch = (u, o) => {
        if (String(u) !== '/chat') return realFetch(u, o);
        const enc = new TextEncoder();
        const body = new ReadableStream({ async start(ctl) {
          for (const l of lines) { ctl.enqueue(enc.encode(JSON.stringify(l) + '\\n')); await new Promise(r => setTimeout(r, 120)); }
          ctl.close(); } });
        return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } }));
      };
      const seen = [];
      const look = setInterval(() => document.querySelectorAll('#chat-body .retry-note').forEach(n => {
        if (!seen.includes(n.textContent)) seen.push(n.textContent); }), 20);
      newConversation();
      STATE.loadedModel = { id: 'x', state: 'loaded' };
      document.getElementById('input').value = 'hi';
      await send();
      await new Promise(r => setTimeout(r, 300));
      clearInterval(look); window.fetch = realFetch;
      return { seen, left: document.querySelectorAll('#chat-body .retry-note').length };
    }""")
    check(f"a retried request says so in the reply, try by try, and the line goes when the answer comes {retried}",
          retried["seen"][:2] == ["Reconnecting · try 1 of 5, in 2 s", "Reconnecting · try 2 of 5, in 4 s"] and retried["left"] == 0)
    # A chat opened with "Yo!" was named once, off the greeting, and never again.
    titles = iter(["", "Biggest Files in Downloads"])
    title_asks: list = []

    def _title(r):
        title_asks.append(json.loads(r.request.post_data or "{}").get("text", ""))
        r.fulfill(status=200, content_type="application/json", body=json.dumps({"title": next(titles, "Should Not Be Asked")}))
    pg.route("**/api/title", _title)
    pg.route("**/chat", lambda r: r.fulfill(status=200, content_type="application/x-ndjson", body="\n".join(json.dumps(e) for e in [
        {"type": "assistant_chunk", "content": "Sure."}, {"type": "assistant", "content": "Sure."},
        {"type": "done", "duration_ms": 5, "iterations": 1, "reason": "finished"}]) + "\n"))
    named = pg.evaluate("""async () => {
      const c = newConversation();
      STATE.loadedModel = { id: 'x', state: 'loaded' };
      const settle = async () => { for (let i = 0; i < 60; i++) { await new Promise(r => setTimeout(r, 50)); } };
      const say = async (t) => { STATE.loadedModel = { id: 'x', state: 'loaded' }; document.getElementById('input').value = t; await send(); await settle(); };
      await say('Yo!');
      const first = c.title;
      await say('find the biggest files in Downloads');
      const second = c.title;
      await say('and the oldest ones');
      return { first, second, third: c.title };
    }""")
    pg.unroute("**/chat"); pg.unroute("**/api/title")
    check(f"a chat that opened with a greeting is named from the next message, then left alone {named} {title_asks}",
          named["second"] == "Biggest Files in Downloads" and named["third"] == "Biggest Files in Downloads"
          and len(title_asks) == 2 and "biggest files" in title_asks[1])
    # A reply gave no sign of what it had in mind from other chats, and the
    # server wasn't told which chat a turn was in.
    lately = pg.evaluate("""async () => {
      let body = null;
      const realFetch = window.fetch;
      window.fetch = (u, o) => {
        if (String(u) !== '/chat') return realFetch(u, o);
        body = JSON.parse(o.body);
        const lines = [{ type: 'context_used', threads: [{ topic: 'CBSE private form', where: 'Form submitted.', open: ['Fee payment'], ago: 'yesterday' }] },
          { type: 'assistant_chunk', content: 'The CBSE fee?' }, { type: 'assistant', content: 'The CBSE fee?' },
          { type: 'done', duration_ms: 5, iterations: 1, reason: 'finished' }];
        return Promise.resolve(new Response(lines.map(l => JSON.stringify(l)).join('\\n') + '\\n', { status: 200 }));
      };
      const c = newConversation();
      STATE.loadedModel = { id: 'x', state: 'loaded' };
      document.getElementById('input').value = 'I got something, guess';
      await send();
      await new Promise(r => setTimeout(r, 500));
      window.fetch = realFetch;
      const last = [...document.querySelectorAll('#chat-body .msg.assistant')].pop();
      const btn = last && last.querySelector('.a-mem');
      if (btn) btn.click();
      const pop = document.querySelector('.lately-pop');
      const out = { chat: body && body.chat === activeConvo().id, btn: !!btn,
                    rows: pop ? [...pop.querySelectorAll('.lp-row b')].map(b => b.textContent) : [],
                    open: pop ? (pop.querySelector('.lp-open') || {}).textContent : '',
                    kept: (activeConvo().messages.slice(-1)[0].context || []).length };
      if (pop) pop.remove();
      return out;
    }""")
    check(f"a reply that had other chats in mind shows what, and the turn says which chat it is in {lately}",
          lately["chat"] and lately["btn"] and lately["rows"] == ["CBSE private form"]
          and "Fee payment" in (lately["open"] or "") and lately["kept"] == 1)
    # The Memory tab shows each chat still going on; one can be put away, one forgotten.
    from hearth import memory_cards as _cards
    _cards.put("c_phone", {"goal": "Phone hunt", "where": "Still after the Edge 60 Pro.", "left": ["Hasn't bought it"]})
    _cards.put("c_trip", {"goal": "Goa trip", "left": ["Book the hotel"]}, now=time.time() - 60)
    seen_lately = pg.evaluate("""async () => {
      showView('memory');
      await refreshLately();
      const box = $('mem-lately');
      const out = { shown: !box.hidden, rows: [...box.querySelectorAll('.ml-row b')].map(b => b.textContent),
                    open: (box.querySelector('.ml-open') || {}).textContent };
      const gone = async (sel) => { for (let i = 0; i < 40 && box.querySelector(sel); i++) await new Promise(r => setTimeout(r, 100)); };
      box.querySelector('.ml-row[data-chat="c_phone"] .ml-done').click();
      await gone('.ml-row[data-chat="c_phone"]');
      box.querySelector('.ml-row[data-chat="c_trip"] .ml-del').click();
      await gone('.ml-row[data-chat="c_trip"]');
      out.after = !box.hidden;
      out.left = [...box.querySelectorAll('.ml-row')].map(r => r.dataset.chat);
      showView('chat');
      return out;
    }""")
    check(f"the Memory tab shows what's going on, and each can be put away or forgotten {seen_lately} {_cards.get('c_phone')}",
          seen_lately["shown"] and seen_lately["rows"] == ["Phone hunt", "Goa trip"]
          and "Hasn't bought it" in (seen_lately["open"] or "") and seen_lately["after"] is False
          and _cards.get("c_phone")["status"] == "done" and _cards.get("c_trip") is None)
    # Under a reply, a card it had in mind can be put away from there too.
    _cards.put("c_cbse", {"goal": "CBSE private form", "left": ["Fee payment"]})
    put_away = pg.evaluate("""async () => {
      const anchor = document.querySelector('#chat-body') || document.body;
      showLately(anchor, [{ kind: 'card', id: 'c_cbse', chat: 'c_cbse', chat_title: 'CBSE form', topic: 'CBSE private form', open: ['Fee payment'], ago: 'today' },
                          { kind: 'search', id: 'pixel', topic: 'Searched "pixel"', where: '2 found', chat: 'c_phone', chat_title: 'Phone hunt' }]);
      const pop = document.querySelector('.lately-pop');
      const out = { src: [...pop.querySelectorAll('.lp-src')].map(b => b.textContent),
                    acts: pop.querySelectorAll('.lp-acts').length };
      pop.querySelector('.lp-acts button[data-act="done"]').click();
      await new Promise(r => setTimeout(r, 400));
      out.after = (pop.querySelector('.lp-acts') || {}).textContent;
      pop.remove();
      return out;
    }""")
    check(f"each item names the chat it came from; a card can be put away from the reply {put_away}",
          put_away["src"] == ["From CBSE form", "From Phone hunt"] and put_away["acts"] == 1
          and put_away["after"] == "Put away." and _cards.get("c_cbse")["status"] == "done")
    # Each coding agent kept its own memory; Hearth's can be shared with them.
    shared_posts = []

    def _share(r):
        if r.request.method == "POST":
            shared_posts.append(json.loads(r.request.post_data or "{}"))
            r.fulfill(status=200, content_type="application/json", body='{"ok": true}')
        else:
            r.fulfill(status=200, content_type="application/json", body=json.dumps({"agents": [
                {"id": "claude-code", "label": "Claude Code", "installed": True, "on": False},
                {"id": "codex", "label": "Codex", "installed": True, "on": True},
                {"id": "cursor-agent", "label": "Cursor", "installed": False, "on": False}]}))
    pg.route("**/api/memory-share", _share)
    share = pg.evaluate("""async () => {
      showView('memory');
      $('mem-share').click();
      for (let i = 0; i < 40 && !document.querySelector('.mem-share-pop .ms-sw'); i++) await new Promise(r => setTimeout(r, 50));
      const pop = document.querySelector('.mem-share-pop');
      const rows = [...pop.querySelectorAll('.ms-row')].map(r => [r.textContent.trim(), r.querySelector('input').checked]);
      const cc = pop.querySelector('.ms-sw[data-agent="claude-code"]');
      cc.click();
      await new Promise(r => setTimeout(r, 300));
      pop.remove(); showView('chat');
      return rows;
    }""")
    pg.unroute("**/api/memory-share")
    check(f"the Memory tab shares it with the agents that are installed, each with a switch {share} {shared_posts}",
          share == [["Claude Code", False], ["Codex", True]] and shared_posts == [{"agent": "claude-code", "on": True}])
    # A helper's full log was tool names in mono, raw JSON, and check-mark glyphs.
    tx = pg.evaluate("""() => {
      const m = helperAsMessage([
        { kind: 'start', name: 'Sana', persona: 'OpenCode', prompt: 'read notes.txt' },
        { kind: 'assistant_tool_call', content: 'Looking.', calls: [{ name: 'read_file', args: '{"path": "notes.txt"}' }] },
        { kind: 'tool_result', name: 'read_file', preview: 'Buy milk on Friday' },
        { kind: 'assistant_final', text: 'It says buy milk.' },
        { kind: 'end', ok: true }]);
      const box = document.createElement('div'); box.className = 'chat-body'; document.body.appendChild(box);
      const div = renderMessage(m); box.appendChild(div); replayAssistant(div, m, box);
      const out = { tools: m.events.filter(e => e.kind === 'tool').map(e => [e.name, e.args.path, e.result]),
                    cards: box.querySelectorAll('.tool-card, .tool-run').length, text: box.innerText,
                    glyph: /[\u2713\u2717]|\uD83D\uDD27/.test(box.innerText) };
      box.remove();
      return out;
    }""")
    check(f"a helper's full log is drawn like a reply: its words and its steps as the chat's tool cards {tx}",
          tx["tools"] == [["read_file", "notes.txt", "Buy milk on Friday"]] and tx["cards"] >= 1
          and "It says buy milk." in tx["text"] and "Looking." in tx["text"] and not tx["glyph"])
    # Something said part-way had no style, so it read as the reply's own words.
    steer = pg.evaluate("""() => {
      const n = makeSteerNote('keep the Zoom one', false); document.body.appendChild(n);
      const cs = getComputedStyle(n), w = getComputedStyle(n.querySelector('.steer-who'));
      const out = { border: cs.borderLeftWidth, bg: cs.backgroundColor, who: w.display, size: w.fontSize,
                    taken: n.querySelector('.steer-who').textContent,
                    waiting: makeSteerNote('keep the Zoom one', true).querySelector('.steer-who').textContent };
      n.remove(); return out;
    }""")
    check(f"words said part-way sit in their own marked box, apart from the reply {steer}",
          steer["border"] != "0px" and steer["bg"] not in ("rgba(0, 0, 0, 0)", "transparent") and steer["who"] == "block"
          and steer["taken"] == "You steered" and steer["waiting"].startswith("You, steering"))
    # One call that never got a result made every later delegate on the Logs page read as running.
    pairs = pg.evaluate("""() => _lgItems([
      { ts: '2026-10-08T23:26:53', event: 'call', tool: 'delegate', args: { agent: 'gemini-cli' } },
      { ts: '2026-10-12T00:32:01', event: 'call', tool: 'delegate', args: { agent: 'opencode' } },
      { ts: '2026-10-12T00:32:01', event: 'result', tool: 'delegate', ok: true, preview: 'Handed to OpenCode' },
      { ts: '2026-10-12T14:13:58', event: 'call', tool: 'delegate', args: { agent: 'claude-code' }, id: 'b' },
      { ts: '2026-10-12T14:14:00', event: 'call', tool: 'delegate', args: { agent: 'codex' }, id: 'c' },
      { ts: '2026-10-12T14:20:00', event: 'result', tool: 'delegate', ok: true, preview: 'Handed to Claude Code', id: 'b' },
    ]).items.map(it => [it.args.agent, it.state])""")
    check(f"each result goes to its own call, and a call with none, long ago, isn't running {pairs}",
          pairs == [["gemini-cli", "gone"], ["opencode", "ok"], ["claude-code", "ok"], ["codex", "gone"]])
    # Opening a memory showed its file: the --- header, type:, tags: and all.
    fact = pg.evaluate("""async () => {
      const realApi = api;
      window.api = async (url, opts) => url.startsWith('/api/memory/') ? { body:
        '---\\nname: Exam on December 12\\ntype: project\\ndescription: Operating systems final on December 12\\n' +
        'tags: [cat:school]\\nupdated: 2026-10-14T09:00:00\\nrecall_count: 3\\n---\\n\\nOperating systems final on December 12.' } : realApi(url, opts);
      memCache = [{ name: 'Exam on December 12', type: 'project', description: 'Operating systems final on December 12' }];
      showView('memory'); MEM_VIEW = 'list'; renderMem('');
      document.querySelector('#mem-list .item').click();
      await new Promise(r => setTimeout(r, 100));
      const text = document.getElementById('modal-body').innerText;
      window.api = realApi; document.getElementById('modal').classList.remove('open'); showView('chat');
      return text;
    }""")
    check(f"an opened memory reads as what it says, when it was saved and how often it was recalled {fact!r}",
          "Operating systems final" in fact and "Recalled 3 times" in fact
          and "---" not in fact and "type:" not in fact and "tags:" not in fact)
    # Your bubble was always the same blue whatever the theme and accent.
    tints = pg.evaluate("""() => {
      const root = document.documentElement, was = [root.dataset.theme, root.dataset.accent];
      document.body.classList.add('chat-bubbles');
      const bubble = document.querySelector('#chat-body .msg.user .content');
      const out = {};
      for (const [theme, accent] of [['dark', 'sage'], ['dark', 'rose'], ['light', 'rose'], ['classic', 'sage']]) {
        root.dataset.theme = theme; root.dataset.accent = accent;
        out[theme + '/' + accent] = getComputedStyle(bubble).backgroundColor;
      }
      const actions = bubble.closest('.msg').querySelector('.actions').getBoundingClientRect(),
            name = bubble.closest('.msg').querySelector('.role-name').getBoundingClientRect();
      out.apart = actions.right <= name.left || actions.top >= name.bottom;
      document.body.classList.remove('chat-bubbles');
      root.dataset.theme = was[0]; if (was[1]) root.dataset.accent = was[1]; else delete root.dataset.accent;
      return out;
    }""")
    check(f"your bubble takes the accent and the theme, and its buttons stay off the name {tints}",
          len({tints["dark/sage"], tints["dark/rose"], tints["light/rose"]}) == 3
          and tints["classic/sage"] != tints["dark/sage"] and tints["apart"])
    # Typing a task in the Reminders box sent it into whatever chat was open.
    own = pg.evaluate("""async () => {
      const realSend = send, realModel = STATE.loadedModel;
      let got = null;
      window.send = async (text, shown, opts) => { got = { chat: STATE.activeId, view: STATE.view, shown, keep: !!(opts && opts.keepBox) }; };
      STATE.loadedModel = STATE.loadedModel || 'stand-in';
      STATE.conversations.unshift({ id: 'rm-open', title: 'Trip plans', messages: [{ role: 'user', content: 'plan my trip' }],
                                    created: Date.now(), updated: Date.now() });
      setActiveId('rm-open');
      showView('reminders');
      $('rm-new').value = 'Remind me to drink water in 5 minutes';
      $('rm-compose').requestSubmit();
      await new Promise(r => setTimeout(r, 150));
      const out = { got, after: STATE.activeId, box: $('rm-new').value,
                    open: STATE.conversations.find(c => c.id === 'rm-open').messages.length };
      window.send = realSend; STATE.loadedModel = realModel;
      return out;
    }""")
    check(f"a task typed in Reminders gets a chat of its own, the open chat is untouched and the page stays {own}",
          own["got"] and own["got"]["chat"] != "rm-open" and own["got"]["view"] == "reminders"
          and own["got"]["shown"] == "Remind me to drink water in 5 minutes" and own["got"]["keep"]
          and own["after"] == "rm-open" and own["open"] == 1 and own["box"] == "")
    pg.evaluate("showView('chat')")
    words = pg.evaluate("""() => [_lgLine('memory_forget', { name: 'old-note' }, true), _lgLine('memory_save', { name: 'tabs' }, true),
                                  _lgLine('memory_recall', { query: 'tabs' }, true)]""")
    check(f"forgetting and saving a memory are said as that, not as recalling {words}",
          words[0].startswith("Forgot") and words[1].startswith("Saved") and words[2].startswith("Recalled"))
    check("a tool with no verb is named plainly, one with a target says what it did",
          logs["plain"] == "System info" and logs["ran"].startswith("Ran echo hi"))
    check("the server log's requests are read into sizes and speeds",
          logs["srv"] == [{"id": "7", "inTok": 2000, "inRate": 2000, "outTok": 60, "outRate": 30, "total": 3}])

    # A status that wrapped to two lines made the card row taller, and the
    # room above it shrank and grew as JARVIS walked about.
    heights = pg.evaluate("""() => {
      const viewWas = STATE.view, roomBox = document.getElementById('tm-room'), hid = roomBox.hidden;
      showView('team'); roomBox.hidden = false;
      const strip = document.getElementById('tm-strip'), keep = strip.innerHTML;
      const card = word => `<div class="tm-card"><div class="tm-face"></div><div><b>JARVIS</b><small>on Claude Code</small><i class="working">${word}</i></div></div>`;
      const hs = ['on the way', 'Free · browsing the shelves by the window seat'].map(w => {
        strip.innerHTML = card(w);
        return strip.offsetHeight;
      });
      strip.innerHTML = keep; roomBox.hidden = hid; showView(viewWas || 'chat');
      return hs;
    }""")
    check(f"a card stays the same height whatever its status says {heights}", heights[0] == heights[1] and heights[0] > 0)

    # A turn running in the terminal left the window's room asleep at the fire.
    away = pg.evaluate("""() => {
      const was = window._mainState;
      window._mainState = 'idle';
      STATE.elsewhere = { surface: 'terminal', state: 'calling web_search', tool: 'web_search' };
      const busy = roomMainBusy(), line = roomMainLine();
      STATE.elsewhere = null;
      const after = roomMainLine();
      window._mainState = was;
      return { busy, line, after };
    }""")
    check(f"a turn running in the terminal shows in the window's room {away}",
          away["busy"] and away["line"].endswith("(in the terminal)") and away["after"] == "")

    # A file Codex changed showed as a path and nothing else; its patch is a
    # unified diff, and the card draws it like any other edit.
    card = pg.evaluate("""() => {
      const body = document.createElement('div');
      const ok = fillEditCard('edit_file', { path: 'notes.txt', diff: '@@ -3,2 +3,2 @@\\n keep\\n-old line\\n+new line\\n' }, body);
      return { ok, head: (body.querySelector('.dl-hdr') || {}).textContent,
               add: [...body.querySelectorAll('.dl-add')].map(r => r.textContent),
               del: [...body.querySelectorAll('.dl-del')].map(r => r.textContent) };
    }""")
    check(f"a change an agent program made shows as a diff {card}",
          card["ok"] and "+1" in card["head"] and "1" in card["head"]
          and card["add"] == ["4+new line"] and card["del"] == ["4-old line"])

    # The side walk read as skipping: the passing frames (back foot well up)
    # were on screen as long as the steps.
    gait = pg.evaluate("""() => {
      const seen = [];
      for (let t = 0; t < WALK_BEAT * 6; t += WALK_BEAT) seen.push(roomWalkStep('side', t + 1, 0));
      return { seen, speed: WALK_SPEED, beat: WALK_BEAT };
    }""")
    passing = sum(1 for n in gait["seen"] if n in (3, 4))
    check(f"side-on, each step is held twice as long as the legs passing {gait}",
          passing == 2 and len(gait["seen"]) == 6 and gait["seen"].count(1) == 2 and gait["seen"].count(2) == 2)
    check("the feet don't slide: a step covers about as much floor as it is drawn to",
          30 <= gait["speed"] * gait["beat"] * 3 / 1000 <= 42)

    # A helper that didn't finish stood by its desk with a hand up for good,
    # and came back after every restart. A working one could not be stopped.
    gone = pg.evaluate("""() => {
      const now = Date.now() / 1000;
      roomSync([{ id: 'sub_old_fail', persona: 'coder', status: 'failed', updated: now - 7200, preview: 'x' },
                { id: 'sub_new_fail', persona: 'coder', status: 'failed', updated: now - 5, preview: 'x' },
                { id: 'sub_busy', persona: 'coder', status: 'working', updated: now }]);
      const oldBack = ROOM.people.has('sub_old_fail');
      const fresh = ROOM.people.get('sub_new_fail');
      const waits = roomGoal(fresh, fresh.agent).pose;
      fresh.doneAt = Date.now() - ROOM_STAY - 1000;
      const later = roomGoal(fresh, fresh.agent);
      roomSelect('sub_busy', true);
      const stop = !!document.querySelector('#tm-side .tm-stop');
      roomSelect(null);
      for (const id of ['sub_old_fail', 'sub_new_fail', 'sub_busy']) ROOM.people.delete(id);
      return { old: oldBack, waits, leaves: !!later.leave, stop };
    }""")
    check(f"a helper that didn't finish waits a while, then goes home, and an old one doesn't come back {gone}",
          gone["old"] is False and gone["waits"] == "raise" and gone["leaves"])
    check("a working helper's panel has a Stop button", gone["stop"])

    # What a helper changed on disk had no place in its panel, and no way back.
    changes = pg.evaluate("""() => {
      const div = document.createElement('div');
      div.innerHTML = roomChangesHTML({ files: ['C:\\\\proj\\\\app.py', 'new.py'], stat: '2 files changed', state: 'waiting',
                                       separate: true, can_bring_in: true, can_discard: true });
      const waiting = [...div.querySelectorAll('[data-change]')].map(b => b.dataset.change);
      const files = [...div.querySelectorAll('.tm-change-files li')].map(l => l.textContent);
      div.innerHTML = roomChangesHTML({ files: ['notes.md'], state: 'changed', separate: false, can_undo: true });
      const direct = [...div.querySelectorAll('[data-change]')].map(b => b.dataset.change);
      const none = roomChangesHTML({ files: [] });
      return { waiting, files, direct, none };
    }""")
    # Helpers talking in the room was small talk only; a real message between two now shows as one.
    talk = pg.evaluate("""async () => {
      // Local time without a zone, the way transcripts write it.
      const d = new Date(), now = d.getTime() / 1000;
      const iso = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
      const rosa = { id: 'sub_t_rosa', persona: 'researcher', name: 'Rosa', status: 'working', updated: now };
      const old = { id: 'sub_t_old', persona: 'coder', name: 'Bo', status: 'working', updated: now,
                    talk: { to_id: 'sub_t_rosa', to: 'Rosa', text: 'from an hour ago', ts: '2020-01-01T00:00:00' } };
      const noah = { id: 'sub_t_noah', persona: 'coder', name: 'Noah', status: 'working', updated: now };
      roomSync([rosa, noah, old]);
      roomSync([rosa, old, { ...noah, talk: { to_id: 'sub_t_rosa', to: 'Rosa', text: 'does API X need the empty case?', ts: iso } }]);
      const n = ROOM.people.get('sub_t_noah'), r = ROOM.people.get('sub_t_rosa');
      const g = roomGoal(n, n.agent);
      const out = { said: (ROOM.says.get('sub_t_noah') || {}).text, near: Math.abs(g.at[0] - r.pos[0]) <= 80 && Math.abs(g.at[1] - r.pos[1]) <= 20,
                    oldSaid: (ROOM.says.get('sub_t_old') || {}).text || '' };
      for (const id of ['sub_t_rosa', 'sub_t_noah', 'sub_t_old']) { ROOM.people.delete(id); ROOM.says.delete(id); }
      return out;
    }""")
    # Finished helpers wandered from spot to spot for their whole stay, and nobody answered anybody.
    life = pg.evaluate("""async () => {
      const d = new Date(), now = d.getTime() / 1000;
      const iso = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
      roomSync([{ id: 'sub_l_rosa', persona: 'researcher', name: 'Rosa', status: 'working', updated: now }]);
      roomSync([{ id: 'sub_l_rosa', persona: 'researcher', name: 'Rosa', status: 'working', updated: now,
                  heard: { from: 'Noah', ts: iso } }]);
      await new Promise(r => setTimeout(r, 2900));
      const answered = (ROOM.says.get('sub_l_rosa') || {}).text;
      const r = ROOM.people.get('sub_l_rosa');
      r.agent = { ...r.agent, status: 'done' }; r.doneAt = Date.now() - 13000; r.reported = true;
      const realNow = Date.now;
      const spots = [];
      for (const later of [0, 41000, 82000, 120000]) {
        Date.now = () => realNow() + later;
        spots.push(JSON.stringify(roomGoal(r, r.agent).at));
      }
      Date.now = realNow;
      ROOM.people.delete('sub_l_rosa'); ROOM.says.delete('sub_l_rosa');
      return { answered, spots: new Set(spots).size };
    }""")
    check(f"a helper that hears from another answers it, and a finished one stays put on its break {life}",
          life["answered"] == "Got it, Noah." and life["spots"] == 1)
    check(f"a helper who messages another walks over and says it; an old message isn't replayed {talk}",
          talk["said"] == "Rosa, does API X need the empty case?" and talk["near"] and talk["oldSaid"] == "")
    check(f"a helper's panel lists what it changed, with what can be done about it {changes}",
          changes["waiting"] == ["show", "bring_in", "discard"] and changes["files"] == ["app.py", "new.py"]
          and changes["direct"] == ["undo"] and changes["none"] == "")

    # JARVIS headed for a desk only while a write was running, which is over
    # long before it could get there, so it never sat down to type.
    desk = pg.evaluate("""async () => {
      // The room has to be loaded and JARVIS in it before he can sit anywhere.
      if (!ROOM.people.has('__main')) { await roomLoad(); roomSync(teamVisible()); }
      for (let i = 0; i < 50 && !ROOM.people.has('__main'); i++) await new Promise(r => setTimeout(r, 100));
      const m = ROOM.people.get('__main'); ROOM.intent = null; ROOM.pinUntil = 0; m.wasBusy = false; m.pokeUntil = 0;
      window._mainState = 'thinking'; roomMainGoal(m);
      window._mainState = 'calling write_file'; const writing = roomMainGoal(m);
      window._mainState = 'thinking'; const after = roomMainGoal(m);
      window._mainState = 'idle'; roomMainGoal(m);
      return [writing.pose, !!writing.seated, after.pose, !!after.seated];
    }""")
    check(f"after writing something JARVIS sits down at a desk and stays there while it works {desk}",
          desk == ["type", True, "type", True])
    idle = pg.evaluate("(() => { ROOM.intent = null; const m = ROOM.people.get('__main'); m.path = []; m.holdUntil = 0; return roomMainIdleWord(m); })()")
    check(f"with nothing asked of it, JARVIS is said to be free, not reading for you {idle!r}", idle.startswith("Free"))

    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat checks passed")
