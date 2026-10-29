"""Slash commands from the phone and chat apps are answered on the PC, not sent to
the model. Owner, 2026-10-29: /status from the phone went to the model as a message
("I'll check the current session status").

    .\\.venv\\Scripts\\python.exe tests\\test_remote_commands.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="hearth-remote-commands-")
WS = os.path.join(HOME, "Inglen")
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
os.makedirs(WS)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "approval_mode": "ask"}, f)
sys.path.insert(0, ROOT)

from hearth import channel_core as cc, headless, relay_bridge as RB, web  # noqa: E402

cc.ACCESS_FILE = os.path.join(WS, "channel_access.json")
RB.WINDOW_URL = ""                                   # no window open: settings go straight to the file
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class App(cc.Adapter):
    name, label, max_len = "telegram", "Telegram", 4000

    def __init__(self):
        self.out = []

    def send(self, chat, text, reply_to=None):
        self.out.append((text, dict(cc.turn_context())))
        return len(self.out)

    def edit(self, chat, msg_id, text):
        return True


asked: list = []


async def model(prompt, *, emit, chat_id=None, **k):
    asked.append((prompt, chat_id))
    time.sleep(0.6)
    emit("assistant", content="Sure.")
    return 0

headless.run_once = model
app = App()
hub = cc.Hub(app, configured_senders=["me"], persona="Inglen")


def say(text):
    n = len(app.out)
    got = hub.receive("me", "me", text)
    return got, [t for t, _ in app.out[n:]]


def settle():
    end = time.time() + 10
    while hub._busy and time.time() < end:
        time.sleep(0.05)
    for t in hub.threads:
        t.join(3)


got, said = say("/status")
check(f"/status is answered on the PC, not sent to the model ({got}, {said[:1]})",
      got == "command" and said and said[0].startswith("Thinking with") and "Before acting: Ask" in said[0] and not asked)
got, said = say("/mode auto")
check(f"/mode auto changes the setting ({said}, {web._load_settings().get('approval_mode')})",
      said == ["Before acting: Auto"] and web._load_settings().get("approval_mode") == "auto")
got, said = say("/think high")
s = web._load_settings()
check(f"/think high turns thinking on at that level ({said})", s.get("think_default") is True and s.get("think_level") == "high")
got, said = say("/foo")
check(f"an unknown command says so in one line ({said})", said == ["No /foo here. /help lists them."] and not asked)
got, said = say("/help")
check(f"/help lists them ({said[0].splitlines()[:2] if said else said})",
      said and "/status" in said[0] and "/new" in said[0] and len(said[0].splitlines()) == 6)
got, said = say("/home/me/notes.txt what is in this file?")
settle()
check(f"a path isn't a command: it goes to the model ({got})", got == "ran" and asked and asked[-1][0].startswith("/home/me"))
first_chat = asked[-1][1]
say("/new")
say("hello again")
settle()
check(f"/new starts a fresh chat for this person ({first_chat} -> {asked[-1][1]})",
      asked[-1][1] and asked[-1][1] != first_chat and asked[-1][1].startswith(first_chat))
hub.receive("me", "me", "a long job")
time.sleep(0.1)
got, said = say("/status")
check(f"a command sent while a turn runs is answered, not joined to it ({got})", got == "command")
settle()
check("the phone's / menu lists what it offers, without /new (it has its own button)",
      [c["name"] for c in RB.answer({"what": "commands"})["commands"]] == ["/status", "/brain", "/mode", "/think", "/help"])

print("\n" + ("all remote command checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
