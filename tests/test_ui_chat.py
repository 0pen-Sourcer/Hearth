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
      out.saving = [roomMainLine(), JSON.stringify(roomMainGoal(me).at) === JSON.stringify(ROOM.spots.board)];
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

    check(f"the page raised no errors {errors[:2]}", not errors)
    browser.close()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat checks passed")
