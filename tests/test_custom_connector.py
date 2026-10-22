"""A connector of the person's own, from the workspace's connectors folder,
runs with everything the built-in apps get. Uses the worked example from
docs/CONNECTORS.md as written there.

    .\\.venv\\Scripts\\python.exe tests\\test_custom_connector.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-own-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_INBOX"] = os.path.join(WS, "inbox")
sys.path.insert(0, ROOT)

from hearth import channel_core as cc, custom_connector as own, headless  # noqa: E402

cc.ACCESS_FILE = os.path.join(WS, "channel_access.json")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


doc = open(os.path.join(ROOT, "docs", "CONNECTORS.md"), encoding="utf-8").read()
example = re.search(r"```python\n(.*?)```", doc, re.S).group(1)
os.makedirs(own.folder())
with open(os.path.join(own.folder(), "folder.py"), "w", encoding="utf-8") as f:
    f.write(example)
check("the connectors folder lists it", own.available() == ["folder"])
cls = own.load("folder")
check("its class is found", cls is not None and cls.label == "Inbox folder")


async def model(prompt, *, emit, **k):
    emit("assistant", content=f"Tomorrow: milk and eggs. (you asked: {prompt.strip()})")
    return 0

headless.run_once = model
threading.Thread(target=own.run, args=("folder",), daemon=True).start()
time.sleep(0.5)
with open(os.path.join(os.environ["HEARTH_INBOX"], "groceries.txt"), "w", encoding="utf-8") as f:
    f.write("what's on my list for tomorrow?")
reply = os.path.join(os.environ["HEARTH_INBOX"], "groceries.reply.txt")
end = time.time() + 15
while not os.path.exists(reply) and time.time() < end:
    time.sleep(0.2)
time.sleep(0.5)
text = open(reply, encoding="utf-8").read() if os.path.exists(reply) else ""
check(f"a note dropped in the folder is answered beside it ({text.strip()[:60]!r})", "milk and eggs" in text)
from hearth import chat_sync  # noqa: E402
saved = chat_sync.load(cc.chat_id_for("folder", "groceries")) or {}
check("and the conversation is one of Hearth's chats", saved.get("source") == "folder")
check("a missing connector says so", "no connector" in (own.run("nope") or ""))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all custom connector checks passed")
