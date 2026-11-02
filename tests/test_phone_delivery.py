"""What the PC does for the phone app, checked without a relay:

- Each message from the phone is answered with "got" once it is here and its files are saved,
  and one sent again (its answer was lost) is taken once, not twice.
- A chat read back for the phone has its reply's words and steps in the order they came, as
  the window draws a saved turn, not every step on top and the words under them.
- An agent's work as the phone shows it: real keys (shapes, and the person's saved ones) read
  "[key hidden]"; a sample written by hand in a test stays as written; the old marker reads plainly.
- Read aloud speaks in the voice saved now, not the one this bridge started with, and a voice
  picked on the phone applies to this process at once.

    .\\.venv\\Scripts\\python.exe tests\\test_phone_delivery.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-phone-delivery-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HOME"] = os.environ["USERPROFILE"] = WS
with open(os.path.join(WS, "settings.json"), "w") as f:
    json.dump({"voice_name": "am_adam", "voice_speed": 1.2}, f)

from hearth import redact, relay_bridge as RB, voice  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------------ "got", once per message
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
frames: list = []


class Room:
    chat = "p1"

    async def deliver(self, obj):
        frames.append(obj)


class Hub:
    def __init__(self):
        self.got = []

    def receive(self, chat, who, text, **k):
        self.got.append((text, [os.path.basename(p) for p in k.get("files") or []]))


bridge = RB.Bridge.__new__(RB.Bridge)
bridge.loop, bridge.hub, bridge._got = loop, Hub(), []
room = Room()
pic = base64.b64encode(b"\x89PNG not really").decode()
msg = {"type": "user", "mid": "m1", "text": "look", "chat": "c_x", "files": [{"name": "a.png", "data": pic}]}
bridge.on_message(room, dict(msg))
bridge.on_message(room, dict(msg))          # its "got" was lost, so the phone sent it again
acks = [f.get("mid") for f in frames if f.get("type") == "got"]
check(f"a message from the phone is answered \"got\" ({acks})", acks[:1] == ["m1"])
check(f"and one sent again is answered again but taken once ({bridge.hub.got})",
      len(acks) == 2 and bridge.hub.got == [("look", ["a.png"])])
check("its file was saved before the answer", os.path.isfile(os.path.join(WS, "uploads", "a.png")))

# ------------------------------------------------------------ words and steps in order
steps = [{"w": "Searched the web", "tool": "web_search", "_pos": 12}, {"w": "Read a page", "tool": "web_fetch", "_pos": 12},
         {"w": "Wrote notes.md", "tool": "write_file", "_pos": 35}]
text = "Let me look." + " Found three good ones." + " Saved them for you."
parts = RB.in_order(text, steps)
shape = [("steps", len(p["steps"])) if "steps" in p else ("text", p["text"]) for p in parts]
check(f"a reply's words and steps come in the order they happened ({shape})",
      shape == [("text", "Let me look."), ("steps", 2), ("text", "Found three good ones."), ("steps", 1),
                ("text", "Saved them for you.")] and all("_pos" not in s for p in parts for s in p.get("steps", [])))

# The phone's "open" of a saved chat carries them.
from hearth import chat_sync  # noqa: E402
cid = "c_order"
os.makedirs(os.path.dirname(chat_sync._path(cid)), exist_ok=True)
chat_sync.atomic_write_json(chat_sync._path(cid), {"id": cid, "title": "t", "messages": [
    {"role": "user", "content": "find me notes apps", "at": 1},
    {"role": "assistant", "content": text, "at": 2, "events": [
        {"kind": "tool", "name": "web_search", "args": {"query": "notes apps"}, "result": "", "pos": 12},
        {"kind": "tool", "name": "write_file", "args": {"path": "notes.md"}, "result": "", "pos": 35}]}]})
got = RB.answer({"what": "open", "chat": cid}, "relay:p1")
reply = (got.get("messages") or [{}])[-1]
kinds = ["steps" if "steps" in p else "text" for p in reply.get("parts") or []]
check(f"and the phone gets them that way when it opens the chat ({kinds})",
      kinds == ["text", "steps", "text", "steps", "text"] and all("_pos" not in s for s in reply.get("steps") or []))

# ------------------------------------------------------------ keys hidden, samples left alone
real = "xai-" + "Q7v" * 9 + "Zk2"
sample = "sk-ant-abcdefghijklmnopqrstuvwxyzABCD"
shown = redact.for_display(f'$env:LOCAL_API_KEY = "{real}"; check("{sample}") -> <REDACTED_KEY>')
check(f"a real key reads [key hidden], a made-up sample stays, the old marker reads plainly ({shown})",
      real not in shown and sample in shown and shown.count("[key hidden]") == 2 and "<REDACTED" not in shown)
ses = redact.clean_session({"messages": [{"role": "assistant", "text": f"used {real}",
                                          "steps": [{"w": "Ran a command", "cmd": f"curl -H 'Authorization: Bearer {real}'"}],
                                          "parts": []}]})
check("a session read back is cleaned the same way, its steps too",
      real not in json.dumps(ses) and "[key hidden]" in ses["messages"][0]["steps"][0]["cmd"])

# ------------------------------------------------------------ the voice saved now
spoken: list = []


class Speaker:
    def __init__(self, **k):
        pass

    def speak(self, text, blocking=False, voice=None, speed=None):
        spoken.append((voice, speed))
        return "ok"

    def stop(self):
        pass


class Adapter:
    def _push(self, chat, obj):
        pass


class SayRoom(RB.Room):
    chat = "p1"


real_speaker, voice.Speaker = voice.Speaker, Speaker
voice._PICKED.pop("voice", None)
r = SayRoom.__new__(SayRoom)
r.saying, r.bridge = None, type("B", (), {"adapter": Adapter()})()
r.on_say({"type": "say.start", "text": "Hello there.", "ref": "x"})
end = time.time() + 5
while not spoken and time.time() < end:
    time.sleep(0.05)
voice.Speaker = real_speaker
check(f"Read aloud speaks in the voice saved now, at its speed ({spoken}), not this process's ({voice.current_voice()})",
      spoken == [("am_adam", 1.2)])

real_window, RB._window = RB._window, (lambda *a, **k: {"ok": True, "voice_name": "am_adam"})
real_list, voice.list_voices = voice.list_voices, (lambda: ["am_adam", "af_heart"])
voice._PICKED.pop("voice", None)
try:
    RB.voice_view({"voice": "am_adam"})
finally:
    RB._window, voice.list_voices = real_window, real_list
check(f"a voice picked on the phone applies to this process at once, for calls ({voice.current_voice()})",
      voice.current_voice() == "am_adam")

loop.call_soon_threadsafe(loop.stop)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all phone delivery checks passed")
