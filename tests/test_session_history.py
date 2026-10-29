"""A long coding-agent session opens on the phone with its history, and Show
earlier reads further back.

The owner opened a Codex session and could scroll only a few pixels up: the
reader took the file's last 4 MB, and Codex keeps every picture it views in the
file, so a few pictures filled it and one message was left to show.

    .\\.venv\\Scripts\\python.exe tests\\test_session_history.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-session-history-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import agent_sessions as S  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


GAME = os.path.join(HOME, "game")
os.makedirs(GAME)
SID = "77777777-7777-7777-7777-777777777777"
T = "2026-10-29T08:00:00Z"
path = os.path.join(HOME, ".codex", "sessions", "2026", "10", "29", f"rollout-x-{SID}.jsonl")
os.makedirs(os.path.dirname(path))
picture = {"timestamp": T, "type": "response_item", "payload": {"type": "message", "role": "user", "content": [
    {"type": "input_image", "image_url": "data:image/png;base64," + "A" * (2 * 1024 * 1024)}]}}
with open(path, "w", encoding="utf-8") as f:
    f.write(json.dumps({"timestamp": T, "type": "session_meta", "payload": {"id": SID, "cwd": GAME}}) + "\n")
    for i in range(150):
        f.write(json.dumps({"timestamp": T, "type": "event_msg", "payload": {"type": "user_message", "message": f"ask {i}"}}) + "\n")
        f.write(json.dumps({"timestamp": T, "type": "event_msg", "payload": {"type": "agent_message", "message": f"answer {i}"}}) + "\n")
    for _ in range(4):                                   # it looked at four pictures at the end
        f.write(json.dumps(picture) + "\n")
    f.write(json.dumps({"timestamp": T, "type": "event_msg", "payload": {"type": "agent_message", "message": "Traced it."}}) + "\n")
check(f"the session file is mostly pictures ({os.path.getsize(path) // 1024 // 1024} MB)", os.path.getsize(path) > 8 * 1024 * 1024)

cid = S.chat_id("codex", SID)
got = S.transcript(cid, 60)
texts = [m["text"] for m in got["messages"]]
check(f"it opens with a screenful of its history, not one message ({len(texts)}, ends {texts[-2:]})",
      len(texts) == 60 and texts[-1].endswith("Traced it."))
check(f"and says there is more ({got.get('more')}, earlier {got.get('earlier')})", got.get("more", 0) > 0 or got.get("earlier"))
more = S.transcript(cid, 300)
texts = [m["text"] for m in more["messages"]]
check(f"Show earlier reads back to the start ({len(texts)}, first {texts[:1]})", texts[0] == "ask 0" and not more.get("earlier"))
check("the session is still listed with its title", any(r["id"] == SID and r["title"] == "ask 0" for r in S.listing(20)))

print("\n" + ("all session history checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
