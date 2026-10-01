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
          setup["titles"] == ["Claude Code"] and setup["picked"] == {"kind": "agent", "id": "claude-code"})
    check("it names the other agents found as helpers, leaves out one its account refused, and says which could be added",
          "OpenCode" in setup["tip"] and "Gemini CLI" not in setup["tip"] and "Codex" in setup["tip"])
    check("on Lite it points at the agent instead of a dead end", "Pick an agent below" in setup["banner"])

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
    check(f"with two tools running at once, each result lands on its own call {paired}",
          paired == [["read_file", "what a.txt says"], ["grep_search", "3 matches"]])

    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat checks passed")
