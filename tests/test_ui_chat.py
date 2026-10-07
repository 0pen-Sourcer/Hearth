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

    slash = pg.evaluate("""async () => {
      const steered = [];
      const real = window.steerMessage;
      window.steerMessage = (t) => steered.push(t);
      STATE.streaming = true;
      document.getElementById('input').value = '/help';
      await send();
      const modal = !!document.querySelector('.modal-bg, .modal');
      document.getElementById('input').value = 'actually use tabs';
      await send();
      STATE.streaming = false;
      window.steerMessage = real;
      return { steered, modal };
    }""")
    check(f"a slash command typed mid-reply is not sent to the model {slash}",
          slash["steered"] == ["actually use tabs"] and slash["modal"])

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
      showView('team');
      await wait(2500);
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
      out.mark = !!document.querySelector('#tm-labels .tm-label .hx-mini svg');
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
      showView('chat');
      out.stripBack = !document.getElementById('view-team').contains(strip);
      return out;
    }""")
    web._save_settings({"llm_provider": "local"})
    check(f"in the room JARVIS says what it is doing, not dots {room}",
          room["doing"] == "Reading a file…" and room["thinking"] == "Thinking…")
    check("a permission question is asked in the room, with the approval strip right there",
          room["asking"] == "I need to run a command for this. Okay?" and room["stripInRoom"] and room["stripBack"])
    check("JARVIS's tag carries the mark of the brain it is running on", room["mark"])
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

    # JARVIS headed for a desk only while a write was running, which is over
    # long before it could get there, so it never sat down to type.
    desk = pg.evaluate("""() => {
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
