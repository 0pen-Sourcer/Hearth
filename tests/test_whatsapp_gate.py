"""Who the WhatsApp bridge answers, and what it sends back.

With allow_self_chat on, the bridge answered every message the linked number
sent, in whatever chat it was sent: a line typed to a friend ran as an
instruction and the agent's reply landed in the friend's chat. Only the
number's own "you" chat may drive it.

The bridge also told the model it sends any file the reply names, and sent
none. And the path finder every bridge shares stopped at the first space, so
a file under "New folder" or a OneDrive folder never went out on any of them.

Drives the real message handler through a stand-in for neonize.

    .\\.venv\\Scripts\\python.exe tests\\test_whatsapp_gate.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


OWN_PHONE, OWN_LID, FRIEND, STRANGER = "919000000001", "80000000001", "919000000002", "15550000003"
TMP = tempfile.mkdtemp(prefix="hearth-wa-")
REPORT = os.path.join(TMP, "New folder", "Hearth Report.pdf")
CHART = os.path.join(TMP, "chart.png")
os.makedirs(os.path.dirname(REPORT))
for _p in (REPORT, CHART):
    with open(_p, "w") as _f:
        _f.write("x")


def jid(user):
    return types.SimpleNamespace(User=user)


class Event:
    def __init__(self, *, chat, sender, from_me, text, mid, sender_alt=""):
        src = types.SimpleNamespace(Chat=jid(chat), Sender=jid(sender), SenderAlt=jid(sender_alt),
                                    IsFromMe=from_me, IsGroup=False)
        self.Info = types.SimpleNamespace(MessageSource=src, ID=mid)
        self.Message = types.SimpleNamespace(conversation=text, extendedTextMessage=None)


class MessageEv: pass
class ConnectedEv: pass
class QREv: pass
class PairStatusEv: pass
class LoggedOutEv: pass


class Client:
    instance = None

    def __init__(self, _db):
        self.handlers, self.sent, self.files, self._n = {}, [], [], 0
        Client.instance = self

    def event(self, kind):
        def deco(fn):
            self.handlers[kind] = fn
            return fn
        return deco

    def _resp(self):
        self._n += 1
        return types.SimpleNamespace(ID=f"SENT{self._n}")

    def send_message(self, chat, text):
        self.sent.append((chat.User, text))
        return self._resp()

    def send_image(self, chat, path):
        self.files.append((chat.User, "image", path))
        return self._resp()

    def send_document(self, chat, path, filename=None):
        self.files.append((chat.User, "document", path))
        return self._resp()

    def get_me(self):
        return types.SimpleNamespace(JID=jid(OWN_PHONE), LID=jid(OWN_LID))

    def connect(self):
        pass


sys.modules["neonize"] = types.ModuleType("neonize")
_client_mod = types.ModuleType("neonize.client"); _client_mod.NewClient = Client
_events_mod = types.ModuleType("neonize.events")
for _c in (MessageEv, ConnectedEv, QREv, PairStatusEv, LoggedOutEv):
    setattr(_events_mod, _c.__name__, _c)
sys.modules["neonize.client"], sys.modules["neonize.events"] = _client_mod, _events_mod

from hearth import whatsapp_bridge as W                               # noqa: E402

W._SESSION_DB = os.path.join(TMP, "session", "s.sqlite3")
W._load_config = lambda: {"allowed_numbers": [FRIEND], "allow_self_chat": True}
PROMPTS: list = []


async def fake_run(prompt, history):
    PROMPTS.append(prompt)
    return f"done: {prompt}"


W._run = fake_run
W.run()
C = Client.instance
handle = C.handlers[MessageEv]


def deliver(**kw):
    before_p, before_s = len(PROMPTS), len(C.sent)
    handle(C, Event(**kw))
    return len(PROMPTS) > before_p, [t for _u, t in C.sent[before_s:]]


# ------------------------------------------------------------- who is answered
ran, out = deliver(chat=FRIEND, sender=OWN_PHONE, from_me=True, text="see you at 6", mid="A1")
check("a message the owner sends to a friend is not run as an instruction", not ran)
check("and nothing is posted into the friend's chat", out == [])

ran, out = deliver(chat=OWN_PHONE, sender=OWN_PHONE, from_me=True, text="what's my battery", mid="A2")
check("the owner's own chat is answered", ran and out and out[0].startswith("done"))

ran, _ = deliver(chat=OWN_LID, sender=OWN_LID, from_me=True, text="hi", mid="A3", sender_alt=OWN_PHONE)
check("including when WhatsApp addresses that chat by its LID", ran)

ran, _ = deliver(chat=FRIEND, sender=FRIEND, from_me=False, text="hello", mid="A4")
check("an allowlisted number is answered", ran)

ran, _ = deliver(chat=STRANGER, sender=STRANGER, from_me=False, text="hello", mid="A5")
check("a number not on the allowlist is not", not ran)

ran, _ = deliver(chat=OWN_PHONE, sender=OWN_PHONE, from_me=True, text="done: hi", mid="SENT1")
check("a reply the bridge sent itself is never read back as a new message", not ran)

W._load_config = lambda: {"allowed_numbers": [FRIEND], "allow_self_chat": False}
W.run()
C = Client.instance
handle = C.handlers[MessageEv]
ran, _ = deliver(chat=OWN_PHONE, sender=OWN_PHONE, from_me=True, text="hi", mid="B1")
check("with allow_self_chat off, the owner's own chat is left alone", not ran)

# ------------------------------------------------------------- files it sends
W._load_config = lambda: {"allowed_numbers": [FRIEND], "allow_self_chat": True}
W.run()
C = Client.instance
handle = C.handlers[MessageEv]


async def reply_with_files(prompt, history):
    return f"Here you go: `{REPORT}` and the chart {CHART}."


W._run = reply_with_files
deliver(chat=OWN_PHONE, sender=OWN_PHONE, from_me=True, text="send me the report", mid="C1")
check(f"a file the reply names is sent back: {C.files}",
      (OWN_PHONE, "document", REPORT) in C.files)
check("an image goes as an image", (OWN_PHONE, "image", CHART) in C.files)

# ------------------------------------------------------- the shared path finder
from hearth import bridge_status as B                                  # noqa: E402
text = (f"Saved to \u201c{REPORT}\u201d, again `{REPORT}`, the chart at {CHART}. "
        f"And C:\\nowhere\\missing.pdf")
check("a quoted path with spaces is found, once, in order",
      B.files_in(text, 10**6) == [REPORT, CHART])
check("a file over the size cap is skipped", B.files_in(text, 0) == [])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all whatsapp bridge checks passed")
