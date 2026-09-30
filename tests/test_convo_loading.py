"""Opening the window with many chats.

The window fetched every conversation in full at start, only to list titles in
the sidebar: 23 MB and about a second for 52 chats, growing with each one kept.
The server parsed every file again on each listing. Chats now arrive as stubs
and load when opened, and the listing re-reads only files that changed.

The risk in that change is a stub being saved, which would erase the chat on
disk, so the window's own functions are run here under Node against a stand-in
API.

    .\\.venv\\Scripts\\python.exe tests\\test_convo_loading.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

WS = tempfile.mkdtemp(prefix="hearth-convos-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ----------------------------------------------------------------- the server
from hearth import web                                               # noqa: E402

web.CONVOS_DIR = os.path.join(WS, "conversations")
os.makedirs(web.CONVOS_DIR, exist_ok=True)


def write(cid, title, n, updated):
    with open(os.path.join(web.CONVOS_DIR, cid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": cid, "title": title, "updated": updated, "created": 1,
                   "messages": [{"role": "user", "content": "x" * 50}] * n}, f)


write("a", "First", 3, 100)
write("b", "Second", 5, 200)
first = web._list_convos()
check("the listing carries titles and message counts, newest first",
      [(c["id"], c["msg_count"]) for c in first] == [("b", 5), ("a", 3)])
time.sleep(0.05)
write("a", "First, renamed", 4, 300)
again = web._list_convos()
check("a chat saved since is listed with its new title and count",
      again[0]["title"] == "First, renamed" and again[0]["msg_count"] == 4)
os.remove(os.path.join(web.CONVOS_DIR, "b.json"))
check("a deleted chat drops out", [c["id"] for c in web._list_convos()] == ["a"])

# ----------------------------------------------------------------- the window
node = shutil.which("node")
if not node:
    print("skip  no node on this machine, window checks not run")
else:
    html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()

    def func(name):
        m = re.search(r"(async\s+)?function " + name + r"\(", html)
        i = html.index("{", m.end())
        depth = 0
        for j in range(i, len(html)):
            depth += {"{": 1, "}": -1}.get(html[j], 0)
            if depth == 0:
                return html[m.start(): j + 1]
        raise ValueError(name)

    js = "\n".join(func(n) for n in
                   ("loadConversations", "ensureConvoLoaded", "convoMsgCount", "persistConvo"))
    harness = js + r'''
const STATE = {};
const calls = { list: 0, full: 0, saved: [] };
const FULL = { id: "a", title: "First", messages: [{ role: "user", content: "hi" },
                                                   { role: "assistant", content: "yo" }] };
async function api(url, opts) {
  if (opts && opts.method === "POST") { calls.saved.push(JSON.parse(opts.body)); return {}; }
  if (url === "/api/conversations") {
    calls.list++;
    return { conversations: [{ id: "a", title: "First", msg_count: 2, updated: 5 }] };
  }
  calls.full++;
  return JSON.parse(JSON.stringify(FULL));
}
(async () => {
  await loadConversations();
  const c = STATE.conversations[0];
  const out = { fetchedFullAtStart: calls.full, countAtStart: convoMsgCount(c) };
  await persistConvo(c);
  out.savedWhileStub = calls.saved.length;
  await Promise.all([ensureConvoLoaded(c), ensureConvoLoaded(c)]);
  out.fetchedOnOpen = calls.full;
  out.messagesAfterOpen = c.messages.length;
  await persistConvo(c);
  out.savedAfterOpen = calls.saved.length;
  out.savedMessages = calls.saved.length ? calls.saved[0].messages.length : -1;
  console.log(JSON.stringify(out));
})();
'''
    path = os.path.join(WS, "harness.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(harness)
    r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        got = {}
        print(r.stdout, r.stderr)
    check("starting the window fetches no chat in full", got.get("fetchedFullAtStart") == 0)
    check("the sidebar still knows how many messages each chat has",
          got.get("countAtStart") == 2)
    check("a chat that has not been opened is never saved, so it cannot be erased",
          got.get("savedWhileStub") == 0)
    check("opening it fetches it once, even when asked twice at the same moment",
          got.get("fetchedOnOpen") == 1 and got.get("messagesAfterOpen") == 2)
    check("once open it saves with its messages",
          got.get("savedAfterOpen") == 1 and got.get("savedMessages") == 2)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all conversation loading checks passed")
