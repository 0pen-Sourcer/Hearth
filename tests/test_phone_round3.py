"""What the PC gives the phone and the window, checked without a relay:

- An id in a path (a session folder, a UUID) is never masked; a real key beside it still is.
- What Claude Code's harness writes beside a tool's result (an agent launched in the background,
  where its output goes) is that step's detail, never a message from the person.
- An empty chat offers ideas only: things to ask and reminders to set, each a fresh message, never
  a row that opens an old chat. They are made from memory's note on each chat; each knows the chat it
  came from; one called "Not useful" goes and is never suggested again.

    .\\.venv\\Scripts\\python.exe tests\\test_phone_round3.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-round3-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HOME"] = os.environ["USERPROFILE"] = WS

from hearth import agent_sessions as S, channel_core as cc, offers, redact, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------------ ids in paths
path = r"C:\Users\me\.claude\projects\c--code\e31b850f-2348-4531-9dbc-5854676f0ac3\tasks\b9l845pqq.output"
key = "gsk_" + "Rt7Kq2Lm9Vx4Np8Zs3Wd6Yc1"
shown = [cc.detail_of({"command": f"type {path}"}), cc.out_of(f"wrote {path}"), redact.redact_secrets_only(path)[0]]
check(f"a session folder's id in a path is never masked ({shown[0][-60:]!r})",
      all("e31b850f-2348-4531-9dbc-5854676f0ac3" in x for x in shown) and not any("REDACTED" in x for x in shown))
check("a real key beside it still is", key not in cc.detail_of({"command": f"curl -H 'k: {key}' {path}"}))

# ------------------------------------------------------------ the harness's note beside a tool result
tpath = os.path.join(WS, "s.jsonl")
lines = [
    {"type": "user", "cwd": WS, "timestamp": "2026-11-02T10:00:00Z", "message": {"role": "user", "content": "check the build"}},
    {"type": "assistant", "timestamp": "2026-11-02T10:00:05Z", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"description": "Check the build", "prompt": "x"}}]}},
    {"type": "user", "timestamp": "2026-11-02T10:00:06Z", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "Async agent launched successfully."}]},
        {"type": "text", "text": "output_file: C:\\tmp\\a.output\nDo NOT Read or tail this file."}]}},
    {"type": "assistant", "timestamp": "2026-11-02T10:00:09Z", "message": {"role": "assistant", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "Started it."}]}},
]
with open(tpath, "w", encoding="utf-8") as f:
    f.write("\n".join(json.dumps(x) for x in lines) + "\n")
got = S._claude_read(tpath, False)
mine = [m["text"] for m in got["messages"] if m["role"] == "user"]
outs = [st.get("out") or "" for m in got["messages"] for st in m.get("steps") or []]
check(f"the harness's note beside a tool's result isn't a message from the person ({mine})", mine == ["check the build"])
check("it is that step's detail, with the result", any("Async agent launched" in o and "Do NOT Read" in o for o in outs))

# ------------------------------------------------------------ ideas
logs = os.path.join(WS, "logs")
os.makedirs(logs, exist_ok=True)
with open(os.path.join(logs, "reminder_ideas.json"), "w", encoding="utf-8") as f:
    json.dump({"at": 1, "ideas": [{"title": "Exam prep", "ask": "Remind me Friday 6pm to revise physics", "kind": "reminder"}]}, f)
rows = offers.rows()
check(f"an empty chat offers ideas only, never a row that opens a chat ({[r['kind'] for r in rows]})",
      rows and all(r["kind"] in ("idea", "reminder") for r in rows) and offers.sources()["threads"] == [])

asked: list = []
web.one_shot = lambda prompt, **k: (asked.append(prompt) or json.dumps([
    {"kind": "idea", "title": "Ship the relay", "ask": "Check the relay deploy logs for errors", "from": "[c_relay]"},
    {"kind": "reminder", "title": "Record video", "ask": "Remind me at 8pm to record the founder video", "from": "c_film"},
    {"kind": "idea", "title": "Made up", "ask": "Something", "from": "c_nowhere"}]))
from hearth import memory_cards  # noqa: E402
memory_cards.everything = lambda now=None: [
    {"chat": "c_relay", "goal": "Deploy the relay tonight", "left": ["read the logs"], "title": "Relay launch"},
    {"chat": "c_film", "goal": "Record the founder video", "left": [], "title": "Founder film"}]
web._list_convos = lambda: [{"id": "c_relay", "title": "Relay launch", "updated": 5}, {"id": "c_film", "title": "Founder film", "updated": 6}]
made = web.reminder_ideas(fresh=True)
check("ideas are made from memory's note on each chat", asked and "Deploy the relay tonight" in asked[-1] and "Notes on their chats" in asked[-1])
check(f"each says what kind it is and the chat it came from, when it's a real one ({[(i['kind'], i.get('chat')) for i in made]})",
      [(i["kind"], i.get("chat")) for i in made] == [("idea", "c_relay"), ("reminder", "c_film"), ("idea", None)]
      and made[0]["chat_title"] == "Relay launch")
web.dismiss_idea("Check the relay deploy logs for errors")
left = [r["ask"] for r in offers.rows()]
check(f"\"Not useful\" takes it away at once ({left})", "Check the relay deploy logs for errors" not in left and left)
asked.clear()
again = web.reminder_ideas(fresh=True)
check("and it is never suggested again", "Not useful to them" in asked[-1]
      and all(i["ask"] != "Check the relay deploy logs for errors" for i in again))

# ------------------------------------------------------------ a file changed on the PC
from hearth import relay_media  # noqa: E402
doc = os.path.join(WS, "FINAL_SUBMIT.md")
with open(doc, "w", encoding="utf-8") as f:
    f.write("# Plan\n\nfirst draft\n")
card = relay_media.card(doc)
first = relay_media.chunk(card["media"], 0, 4096)
try:
    st = relay_media.chunk(card["media"], stat=True)
except TypeError:
    st = {}
check(f"a file the phone opens says which version it is, and a look at it alone sends no data ({sorted(st)})",
      bool(first.get("version")) and st.get("version") == first.get("version") and "data" not in st
      and st.get("size") == first.get("size"))
with open(doc, "w", encoding="utf-8") as f:
    f.write("# Plan\n\nsecond draft, edited on the PC\n")
os.utime(doc, (os.path.getatime(doc), os.path.getmtime(doc) + 5))
again = relay_media.chunk(card["media"], 0, 4096)
check(f"edited on the PC, it is a new version, so the phone fetches it again ({first.get('version')} -> {again.get('version')})",
      bool(again.get("version")) and again["version"] != first.get("version"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all round 3 checks passed")
