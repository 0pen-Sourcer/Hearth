"""Chat apps share one way of answering: pairing, the conversation, questions
before risky steps, messages mid-reply, voice notes.

A stand-in app and a stand-in model; nothing reaches the internet.

    .\\.venv\\Scripts\\python.exe tests\\test_channel_core.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-channels-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "approval_mode": "ask"}, f)
sys.path.insert(0, ROOT)

from hearth import channel_core as cc, chat_sync, headless, listen  # noqa: E402

cc.ACCESS_FILE = os.path.join(WS, "channel_access.json")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class FakeApp(cc.Adapter):
    name, label, max_len = "telegram", "Telegram", 50

    def __init__(self):
        self.sent, self.edits, self.n = [], [], 0

    def send(self, chat, text, reply_to=None):
        self.n += 1
        self.sent.append((chat, text))
        return self.n

    def edit(self, chat, msg_id, text):
        self.edits.append((msg_id, text))
        return True


def wait_idle(hub, secs=10):
    end = time.time() + secs
    while hub._busy and time.time() < end:
        time.sleep(0.05)
    for t in hub.threads:
        t.join(2)


app = FakeApp()
hub = cc.Hub(app, configured_senders=["111"], persona="JARVIS")

# ------------------------------------------------------------ strangers pair
check("a stranger's message isn't run", hub.receive("900", "900", "hi", name="Sam") == "pairing")
code = cc.pending("telegram")[0]["code"]
check("they're given an 8-character code without look-alikes",
      len(code) == 8 and not set(code) & set("0O1I") and code in app.sent[-1][1])
hub.receive("900", "900", "hello?")
check("asking again gives the same code, not a new one", len(cc.pending("telegram")) == 1 and code in app.sent[-1][1])
for s in ("901", "902"):
    hub.receive(s, s, "hi")
check("a fourth waiting stranger is ignored", hub.receive("903", "903", "hi") == "ignored" and len(cc.pending()) == 3)
check("a wrong code isn't approved", not cc.approve("telegram", "ZZZZZZZZ")["ok"])
check("the right one is", cc.approve("telegram", code.lower())["ok"] and cc.is_allowed("telegram", "900"))
check("and is no longer waiting", code not in [p["code"] for p in cc.pending()])

# ------------------------------------------------------------ a conversation
asked = {}


async def model(prompt, *, emit, history=None, chat_id=None, permission_check=None, drain_steer=None, **k):
    asked.setdefault("prompts", []).append(prompt)
    asked["history"] = history
    asked["chat_id"] = chat_id
    if "delete" in prompt:
        emit("tool_call", name="run_command", args={"command": "del notes.txt"})
        verdict = permission_check("run_command", {"command": "del notes.txt"})
        asked["verdict"] = verdict
        emit("assistant", content="Deleted it." if verdict == "allow" else "Left it alone.")
        return 0
    if "slow" in prompt:
        for _ in range(40):
            time.sleep(0.05)
            got = drain_steer() if drain_steer else []
            if got:
                asked["steered"] = got
                break
        emit("assistant", content="done slowly")
        return 0
    emit("assistant", content="Hello from the stand-in. " * 4)
    return 0

headless.run_once = model
titles_asked: list = []


def fake_titler(prompt):
    titles_asked.append(prompt)
    return "Testing the stand-in" if "and again" in prompt else "NONE"


cc._ask_brain = fake_titler
app.sent.clear()
check("a known sender's message runs", hub.receive("111", "111", "hi there") == "ran")
wait_idle(hub)
check("the reply comes back split to the app's length",
      len(app.sent) >= 2 and all(len(t) <= 50 for _c, t in app.sent))
saved = chat_sync.load(cc.chat_id_for("telegram", "111")) or {}
check("the chat is one of Hearth's own, marked with the app",
      saved.get("source") == "telegram" and [m["role"] for m in saved.get("messages", [])] == ["user", "assistant"]
      and saved["messages"][0].get("via") == "telegram")
hub.receive("111", "111", "and again")
wait_idle(hub)
check("the next turn carries the conversation on",
      any("hi there" in str(m.get("content")) for m in asked["history"]) and asked["chat_id"] == "telegram_111")
end = time.time() + 5
while (chat_sync.load("telegram_111") or {}).get("title") != "Testing the stand-in" and time.time() < end:
    time.sleep(0.05)
got_title = (chat_sync.load("telegram_111") or {}).get("title")
check(f"a greeting alone leaves the chat unnamed, and the next turn names it, by the brain ({got_title!r})",
      len(titles_asked) == 2 and got_title == "Testing the stand-in")
chat_sync.retitle("telegram_111", "My own name", was="Testing the stand-in")
n = len(titles_asked)
cc.name_chat("telegram_111", "more", "ok", ask=fake_titler)
time.sleep(0.3)
check("a chat already named isn't asked about again",
      len(titles_asked) == n and chat_sync.load("telegram_111")["title"] == "My own name")

# ------------------------------------------------------------ a risky step asks in the chat
app.sent.clear()
hub.receive("111", "111", "delete notes.txt")
end = time.time() + 5
while not any("Reply yes" in t for _c, t in app.sent) and time.time() < end:
    time.sleep(0.05)
check("a risky step asks first, in the chat", any("del notes.txt" in t and "Reply yes" in t for _c, t in app.sent))
check("the next message answers it", hub.receive("111", "111", "yes go ahead") == "answered")
wait_idle(hub)
check("and yes lets it go on", asked.get("verdict") == "allow")
hub.receive("111", "111", "delete notes.txt")
end = time.time() + 5
while not hub._busy.get("111", {}).get("question") and time.time() < end:
    time.sleep(0.05)
hub.receive("111", "111", "no wait")
wait_idle(hub)
check("anything else stops it", asked.get("verdict") == "deny")

# ------------------------------------------------------------ more while it works
hub.receive("111", "111", "slow job")
time.sleep(0.2)
check("a message mid-reply joins it", hub.receive("111", "111", "also the other thing") == "joined")
wait_idle(hub)
check("and the reply sees it", asked.get("steered") == ["also the other thing"])

# ------------------------------------------------------------ voice notes and files
listen.transcribe = lambda path: "what's on my calendar"
hub.receive("111", "111", voice=os.path.join(WS, "note.ogg"))
wait_idle(hub)
check("a voice note is written down and run", asked["prompts"][-1] == "what's on my calendar")
pic = cc.save_upload(b"\x89PNG fake", "holiday photo.png")
hub.receive("111", "111", "what's this", files=[pic])
wait_idle(hub)
check("a photo is kept and the model told where", os.path.isfile(pic) and pic in asked["prompts"][-1]
      and "view_image" in asked["prompts"][-1])

check("splitting breaks at a space, not mid-word", cc.split("aaaa bbbb cccc", 9) == ["aaaa bbbb", "cccc"])

# ------------------------------------------------------------ writing first, and the tool
import urllib.request  # noqa: E402
from hearth import reminders, tools  # noqa: E402

configs = {"telegram": {"bot_token": "123:abc"}}
cc.config_of = lambda ch: dict(configs.get(ch) or {})
check("the owner is the first one let in by pairing when no id was typed", cc.owner_of("telegram") == "900")
posted = []
real_urlopen = urllib.request.urlopen
urllib.request.urlopen = lambda req, timeout=0: posted.append(req.full_url) or type("R", (), {"read": lambda s: b"{}"})()
out = tools._send_to_phone({"message": "your build finished"})
check(f"send_to_phone goes to the connected app's owner ({out})", out == "Sent to your Telegram." and "bot123:abc/sendMessage" in posted[-1])
check("WhatsApp is said to only answer", cc.push("whatsapp", "x").startswith("Error"))
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "approval_mode": "ask", "phone_reminders": False}, f)
n = len(posted)
reminders._push_phone("Stretch", "stand up")
check("reminders stay off the phone unless asked", len(posted) == n)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "approval_mode": "ask", "phone_reminders": True}, f)
check("and go to it when asked", reminders._push_phone("Stretch", "stand up") and len(posted) == n + 1)
# The Inglen app on a paired phone (Plus) gets it too, through the phone bridge.
from hearth import live_bus, relay_bridge  # noqa: E402
_bus, _phones = [], relay_bridge.phones
real_publish = live_bus.publish
live_bus.publish = lambda ev: _bus.append(ev)
relay_bridge.phones = lambda: [{"id": "p1", "name": "Phone"}]
reminders._push_phone("Stretch", "stand up", reminders.chat_id_for({"id": "r9"}))
got = [e for e in _bus if e.get("kind") == "phone_notice"]
check(f"a paired phone's app is sent the reminder ({got})",
      bool(got) and got[-1]["body"] == "stand up" and got[-1]["c"] == "rem_r9")
check("where it goes is said", reminders.phone_targets() == {"app": 1, "chat_apps": ["telegram"]})
told = []
_br = relay_bridge.Bridge.__new__(relay_bridge.Bridge)
_br.adapter = type("A", (), {"rooms": {"p1": object()}, "name": "relay",
                             "notify": lambda s, chat, obj, urgent=False: told.append((chat, obj, urgent))})()
if got:
    _br.on_window_event(got[-1])
for _ in range(50):
    if told:
        break
    __import__("time").sleep(0.05)
check(f"the phone bridge shows it as a notification ({told})",
      bool(told) and told[0][0] == "p1" and told[0][1]["kind"] == "reminder" and told[0][1]["title"] == "Stretch")
live_bus.publish, relay_bridge.phones = real_publish, _phones
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "approval_mode": "ask", "phone_reminders": False}, f)
urllib.request.urlopen = real_urlopen

check("looking at the connectors never asks", tools.mode_allows("connectors", {"action": "status"})
      and not tools.mode_allows("connectors", {"action": "approve"}))
cc.IN_A_CHAT_APP = False             # as in the window's process, which runs no chat app
c2 = cc.request_code("discord", "555", "Ada")
check("JARVIS can let someone in from the PC", "Let in Ada" in tools._connectors(
    {"action": "approve", "channel": "discord", "code": c2}))
cc.IN_A_CHAT_APP = True
c3 = cc.request_code("discord", "556", "Eve")
check("but not from a chat app", tools._connectors({"action": "approve", "channel": "discord", "code": c3}).startswith("Error")
      and not cc.is_allowed("discord", "556"))
check("looking still works there", "telegram" in tools._connectors({"action": "status"}))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all channel core checks passed")
