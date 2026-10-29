"""The window and the phone see each other's changes as they happen.

The window looked for a phone's messages every 5 s and for a changed brain every
12 s; the phone never heard about changes made on the PC; a turn from the phone
that failed was saved as if the model had said the error. Now the window's
server keeps one stream of what changes (GET /api/events), every change says so
(settings, chats, replies as they are written, from any process), and a failed
turn is saved as the window saves its own errors.

    .\\.venv\\Scripts\\python.exe tests\\test_live_sync.py
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
sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-live-")
os.environ["HOME"] = os.environ["USERPROFILE"] = WORK
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(WORK, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import chat_sync, live_bus, web                         # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}"
got: list = []
stop = []
threading.Thread(target=live_bus.listen, args=(url, got.append, lambda: bool(stop)), daemon=True).start()
time.sleep(1.0)


def wait_for(pred, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        hit = [e for e in got if pred(e)]
        if hit:
            return hit[-1], time.time()
        time.sleep(0.02)
    return None, None


t0 = time.time()
web._save_settings({"approval_mode": "auto", "llm_key": "secret-should-not-travel"})
ev, at = wait_for(lambda e: e.get("kind") == "settings")
check(f"a settings change is said at once ({ev}, {(at - t0) * 1000 if at else 0:.0f} ms)",
      ev and "approval_mode" in ev["keys"] and at - t0 < 1.0)
check("the names of key settings aren't said", ev and not any("key" in k for k in ev["keys"]))

t0 = time.time()
chat_sync.post("relay_abc", "Hello", "relay", [{"role": "user", "content": "hi", "at": 1}])
ev, at = wait_for(lambda e: e.get("kind") == "chat" and e.get("chat") == "relay_abc")
check(f"a chat saved by a bridge is said at once ({(at - t0) * 1000 if at else 0:.0f} ms)", ev and at - t0 < 1.0)

# Another process (a bridge, the terminal) hands its events to the window, which passes them on.
req = urllib.request.Request(url + "/api/events", method="POST", headers={"Content-Type": "application/json"},
                             data=json.dumps({"kind": "turn", "chat": "relay_abc", "from": "relay", "turn": "t1",
                                              "type": "chunk", "text": "Hel"}).encode())
urllib.request.urlopen(req, timeout=3).read()
ev, _ = wait_for(lambda e: e.get("kind") == "turn" and e.get("text") == "Hel")
check("an event from another process reaches every listener", bool(ev))
req = urllib.request.Request(url + "/api/events", method="POST", headers={"Content-Type": "application/json"},
                             data=json.dumps({"kind": "anything-else", "x": 1}).encode())
urllib.request.urlopen(req, timeout=3).read()
time.sleep(0.3)
check("only the known kinds are passed on", not any(e.get("kind") == "anything-else" for e in got))

# A reply as it is written: the events a turn emits, said the same way from every surface.
ev = live_bus.turn_event("c1", "window", "t2", "tool_call", {"name": "run_command", "args": {"command": "git status"}})
check(f"a step says its words and what it ran ({ev})", ev["type"] == "step" and ev["words"] == "Ran git status" and ev["cmd"] == "git status")
check("bookkeeping isn't passed on", live_bus.turn_event("c1", "window", "t2", "context_state", {}) is None)

# A turn from the phone that failed is kept as the window keeps its own errors.
from hearth import channel_core as cc, headless                       # noqa: E402


class Phone(cc.Adapter):
    name, label, max_len = "relay", "Phone", 4000
    sent: list = []

    def send(self, chat, text, reply_to=None):
        self.sent.append(text)


async def broke(prompt, *, emit, **k):
    emit("error", category="quota", message="OpenCode is out of usage on your plan.")
    return 1

headless.run_once = broke
hub = cc.Hub(Phone(), configured_senders=["me"])
cc.ACCESS_FILE = os.path.join(WORK, "access.json")
cid = cc.chat_id_for("relay", "me")
hub._turn("me", "Hello!", None, {"key": "me"})
saved = chat_sync.load(cid) or {}
reply = (saved.get("messages") or [{}])[-1]
check(f"a failed phone turn is saved as an error, not as something the model said ({reply})",
      reply.get("role") == "assistant" and reply.get("content") == ""
      and any(e.get("kind") == "error" and "out of usage" in e.get("text", "") for e in reply.get("events") or []))

# The window shows a reply the phone asked for, as it is written, in the chat open there.
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
# The terminal, started on its own, finds the window through what the window announced.
saved_env = os.environ.pop("HEARTH_WINDOW_URL", None)
live_bus.announce_window("http://127.0.0.1:59999/")
check(f"a process the window didn't start finds it ({live_bus.window_url()})", live_bus.window_url() == "http://127.0.0.1:59999")
if saved_env is not None:
    os.environ["HEARTH_WINDOW_URL"] = saved_env

# The phone shows a turn the terminal (or a chat app) runs, not only the window's.
from hearth import relay_bridge as RB  # noqa: E402
pushed = []


class _Phone:
    name, rooms = "relay", {"room1": object()}

    def _push(self, chat, obj):
        pushed.append(obj)

    def live(self, chat, text):
        pushed.append({"type": "live", "text": text})

    def think(self, chat, text):
        pushed.append({"type": "think", "text": text})

    def _turn_done(self):
        pass


br = RB.Bridge.__new__(RB.Bridge)
br.adapter, br._mirrored, br._said = _Phone(), {}, {}
for ev in ({"type": "start", "text": "run the tests"}, {"type": "chunk", "text": "All 40 pass."},
           {"type": "said", "text": "All 40 pass."}, {"type": "end"}):
    br.on_window_event(dict({"kind": "turn", "chat": "term_chat", "from": "terminal", "turn": "k1"}, **ev))
check(f"a terminal turn reaches the phone as it goes ({[p['type'] for p in pushed]})",
      [p["type"] for p in pushed] == ["user", "live", "text"] and pushed[0]["via"] == "terminal")
pushed.clear()
br.on_window_event({"kind": "turn", "chat": "x", "from": "relay", "turn": "k2", "type": "start", "text": "mine"})
check("but not the phone's own turns back to it", pushed == [])

if sync_playwright:
    web._save_settings(dict(web._load_settings(), onboarded=True))
    chat_sync.post("relay_live", "From the phone", "relay", [{"role": "user", "content": "earlier", "at": 1},
                                                             {"role": "assistant", "content": "Earlier reply.", "at": 2}])
    with sync_playwright() as pw:
        b = None
        for kw in ({"channel": "msedge"}, {}):
            try:
                b = pw.chromium.launch(**kw)
                break
            except Exception:
                pass
        pg = b.new_page(viewport={"width": 1200, "height": 800})
        pg.goto(url + "/", wait_until="domcontentloaded")
        pg.wait_for_function("typeof setActiveId === 'function' && STATE.conversations.some(c => c.id === 'relay_live')", timeout=30000)
        pg.evaluate("""async () => { const c = STATE.conversations.find(c => c.id === 'relay_live'); await ensureConvoLoaded(c);
          setActiveId('relay_live'); renderChat(); }""")
        pg.wait_for_timeout(1500)

        def say(**ev):
            r = urllib.request.Request(url + "/api/events", method="POST", headers={"Content-Type": "application/json"},
                                       data=json.dumps(dict({"kind": "turn", "chat": "relay_live", "from": "relay", "turn": "p1"}, **ev)).encode())
            urllib.request.urlopen(r, timeout=3).read()
        t0 = time.time()
        say(type="start", text="what's on my desktop?")
        say(type="step", words="Listed ~/Desktop", tool="list_dir", cmd="~/Desktop")
        say(type="chunk", text="Three files: ")
        pg.wait_for_function("document.body.innerText.includes('Three files:')", timeout=5000)
        mid = time.time() - t0
        shown = pg.evaluate("document.body.innerText")
        check(f"the window shows the phone's message and the reply as it's written ({mid:.2f} s)",
              "what's on my desktop?" in shown and mid < 2)
        say(type="chunk", text="a.txt, b.txt and c.txt.")
        say(type="said", text="Three files: a.txt, b.txt and c.txt.")
        say(type="end")
        pg.wait_for_timeout(600)
        check("and the rest of it", "a.txt, b.txt and c.txt." in pg.evaluate("document.body.innerText"))
        pg.screenshot(path=os.path.join(WORK, "window_live.png"))
        print("screenshot:", os.path.join(WORK, "window_live.png"))
        # A phone turn saved later replaces the live copy; the window never saves the live one itself.
        pg.evaluate("persistConvo(STATE.conversations.find(c => c.id === 'relay_live'))")
        pg.wait_for_timeout(500)
        kept = [m.get("content") for m in (chat_sync.load("relay_live") or {}).get("messages") or []]
        check(f"the window never saves the copy it showed live ({kept})", "what's on my desktop?" not in kept)
        # A chat just started on the phone, which this window has never seen: shown as it goes.
        for ev in ({"type": "start", "text": "plan my week"}, {"type": "chunk", "text": "Monday: gym."}):
            say(**dict(ev, chat="relay_new", turn="p2"))
        pg.wait_for_function("STATE.conversations.some(c => c.id === 'relay_new' && (c.messages || []).some(m => (m.content || '').includes('Monday: gym.')))", timeout=5000)
        got = pg.evaluate("STATE.conversations.find(c => c.id === 'relay_new').title")
        check(f"a turn in a chat started on the phone shows in the window as it goes, the chat listed ({got!r})", got == "plan my week")
        b.close()

stop.append(1)
srv.shutdown()
print("\n" + ("all live sync checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
