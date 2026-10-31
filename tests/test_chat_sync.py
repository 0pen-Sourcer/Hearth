"""The window and the terminal share one set of chats.

Before, a terminal chat lived in its own history file the window never
listed, and a chat from the window couldn't be carried on in the terminal."""

from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import asyncio
import io
import json
import os
import re
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-chats-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hearth_cli                     # noqa: E402
from hearth import chat_sync          # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
cli.messages = [{"role": "system", "content": "persona"}]
cli._chat_resume()
cli.messages += [
    {"role": "user", "content": "how big is my Downloads folder?"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function",
     "function": {"name": "disk_usage", "arguments": json.dumps({"path": "~/Downloads"})}}]},
    {"role": "tool", "tool_call_id": "t1", "content": "12.4 GB"},
    {"role": "assistant", "content": "About 12.4 GB."},
]
cli._chat_push()
cid = cli._chat["id"]
d = chat_sync.load(cid)
check(f"a terminal chat is saved as a chat the window lists {cid}",
      d and d["source"] == "terminal" and d["title"].startswith("how big is my Downloads")
      and any(c["id"] == cid for c in chat_sync.listing()))
reply = d["messages"][-1]
check(f"its tool call shows as the window shows tools {reply}",
      reply["content"] == "About 12.4 GB." and reply["events"][0]["name"] == "disk_usage"
      and reply["events"][0]["result"] == "12.4 GB")

# Carried on in the window: two turns added to the same file.
d["messages"] += [{"role": "user", "content": "and Videos?"}, {"role": "assistant", "content": "Videos is 3 GB."}]
chat_sync.atomic_write_json(chat_sync._path(cid), d)
cli._chat_pull()
check("the next message in the terminal knows what was said in the window",
      cli.messages[-1] == {"role": "assistant", "content": "Videos is 3 GB."})
cli.messages += [{"role": "user", "content": "thanks"}, {"role": "assistant", "content": "Any time."}]
cli._chat_push()
kept = [m["content"] for m in chat_sync.load(cid)["messages"]]
check(f"saving from the terminal keeps the window's turns {kept}",
      kept[-4:] == ["and Videos?", "Videos is 3 GB.", "thanks", "Any time."] and len(kept) == 6)

# A chat started in the window, carried on in the terminal.
win = {"id": "c_window1", "title": "Plan the trip", "created": 1, "updated": int(time.time() * 1000) + 5000,
       "pinned": False, "messages": [{"role": "user", "content": "plan a trip to Goa"},
                                     {"role": "assistant", "content": "Three days: beach, fort, market."}]}
chat_sync.atomic_write_json(chat_sync._path("c_window1"), win)
ansi = re.compile(r"\x1b\[[0-9;]*m")


def run(cmd):
    buf = io.StringIO()
    with redirect_stdout(buf):
        asyncio.run(cli.handle_command(cmd))
    return ansi.sub("", buf.getvalue())


# The window saved its older copy of the chat right after the terminal added a
# turn, and the terminal's turn was gone.
import socket, threading, urllib.request    # noqa: E401,E402
from hearth import web                         # noqa: E402
stale = chat_sync.load(cid)
cli.messages += [{"role": "user", "content": "one more from the terminal"}, {"role": "assistant", "content": "Done there."}]
cli._chat_push()
sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
stale["messages"] += [{"role": "user", "content": "from the window"}, {"role": "assistant", "content": "Window reply."}]
req = urllib.request.Request(f"http://127.0.0.1:{port}/api/conversations", data=json.dumps(stale).encode(),
                             headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
urllib.request.urlopen(req, timeout=10).read()
srv.shutdown()
order = [m["content"] for m in chat_sync.load(cid)["messages"]][-4:]
check(f"the window saving an older copy keeps the terminal's newest turn, in its place {order}",
      order == ["one more from the terminal", "Done there.", "from the window", "Window reply."])

listed = run("/chats")
check(f"/chats lists chats from both places {listed!r}", "Plan the trip" in listed and "window" in listed and "terminal" in listed)
out = run("/chat Plan the trip")
check(f"/chat carries a window chat on in the terminal {out!r}",
      "Plan the trip" in out and cli.messages[-1]["content"] == "Three days: beach, fort, market."
      and cli._chat["id"] == "c_window1")
run("/clear")
check("/clear starts a new chat", cli._chat["id"] is None)

# A second terminal opened while the first is still running starts its own chat;
# before, both carried on the same one and wrote over each other's history file.
run("/chat Plan the trip")
held = json.load(open(cli._CHAT_STATE, encoding="utf-8"))
held["pid"] = os.getppid()                          # a process that is still running
json.dump(held, open(cli._CHAT_STATE, "w", encoding="utf-8"))
before = open(hearth_cli.HISTORY_FILE, encoding="utf-8").read() if os.path.exists(hearth_cli.HISTORY_FILE) else ""
two = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
two.messages = [{"role": "system", "content": "persona"}, {"role": "user", "content": "old turn"}]
two._chat_resume()
check(f"a second terminal doesn't take the chat the first is on {two._chat}",
      two._chat["id"] is None and [m["role"] for m in two.messages] == ["system"])
two.messages += [{"role": "user", "content": "from terminal two"}, {"role": "assistant", "content": "Hi from two."}]
two.save_history()
after = open(hearth_cli.HISTORY_FILE, encoding="utf-8").read() if os.path.exists(hearth_cli.HISTORY_FILE) else ""
mine = chat_sync.load(two._chat["id"]) if two._chat["id"] else None
check("its turns go to a chat of its own, and the first terminal's history file is left alone",
      after == before and mine and [m["content"] for m in mine["messages"]] == ["from terminal two", "Hi from two."]
      and json.load(open(cli._CHAT_STATE, encoding="utf-8"))["id"] == "c_window1")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat sync checks passed")
