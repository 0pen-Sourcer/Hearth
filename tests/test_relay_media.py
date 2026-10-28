"""Files for the phone: a file the agent sends goes as a small card (name, size,
type, a thumbnail for a picture), never the file itself, so a phone that's away
gets cards in its mailbox instead of the file pushing out its messages. The
phone opens a file by asking the PC for it in parts, only by the card's id; a
chat rebuilt from the PC carries its files' cards.

    .\\.venv\\Scripts\\python.exe tests\\test_relay_media.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-media-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from PIL import Image  # noqa: E402

from hearth import chat_sync, relay_bridge as RB, relay_media as M  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


pic = os.path.join(HOME, "chart.png")
Image.effect_noise((1600, 1200), 80).convert("RGB").save(pic)
big = os.path.join(HOME, "video.mp4")
with open(big, "wb") as f:
    f.write(os.urandom(12 * 1024 * 1024))


class _Room:
    def __init__(self):
        self.sent = []

    async def deliver(self, obj):
        self.sent.append(obj)


loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
relay = RB.Relay(loop)
room = relay.rooms["p1"] = _Room()

relay.send_file("p1", pic)
relay.send_file("p1", big)
cards = [o for o in room.sent if o.get("type") == "file"]
sizes = [len(json.dumps(o)) for o in cards]
check(f"a file goes to the phone as a card, not the file ({sizes} bytes)",
      len(cards) == 2 and all("data" not in c for c in cards) and max(sizes) < 60_000)
p, v = cards
check(f"a picture's card has its size, type and a small thumbnail ({len(p['thumb'])} chars)",
      p["kind"] == "image" and p["size"] == os.path.getsize(pic) and p["thumb"].startswith("data:image/jpeg;base64,")
      and len(p["thumb"]) < 60_000)
check("a 12 MB video's card is a few hundred bytes", v["kind"] == "video" and not v["thumb"] and len(json.dumps(v)) < 1000)

# Opening: the phone asks for parts by the card's id.
got, off = b"", 0
while True:
    r = RB.answer({"what": "media", "media": v["media"], "offset": off})
    if r.get("error"):
        break
    piece = base64.urlsafe_b64decode(r["data"] + "=" * (-len(r["data"]) % 4))
    got += piece
    off += len(piece)
    if r["done"]:
        break
with open(big, "rb") as f:
    check(f"the phone gets the whole file in parts ({len(got) // 1024} KB, parts of {M.CHUNK // 1024} KB)", got == f.read())
r = RB.answer({"what": "media", "media": v["media"], "offset": 0})
check(f"each part fits one relay frame ({len(json.dumps(r)) // 1024} KB)", len(json.dumps(r)) < RB.PART)
check("an id the PC never carded gets nothing", RB.answer({"what": "media", "media": "0" * 32}).get("error"))
check("a path in place of an id gets nothing", RB.answer({"what": "media", "media": pic}).get("error"))
other = os.path.join(HOME, "secret.txt")
with open(other, "w") as f:
    f.write("private")
check("nor does the id of a file it never sent", RB.answer({"what": "media", "media": M._id_for(other)}).get("error"))

# A chat rebuilt from the PC carries its files: one the person sent, one a reply named.
cid = "c_media"
up = os.path.join(HOME, "notes.pdf")
with open(up, "wb") as f:
    f.write(b"%PDF-1.4 test")
chat_sync.post(cid, "media", "relay", [
    {"role": "user", "content": f"look\n[They sent notes.pdf, saved at {up}. Look at it with view_image if it's a picture, "
                                f"or read_file, before answering about it.]", "at": 1},
    {"role": "assistant", "content": f"Here is the chart: {pic}", "at": 2}])
msgs = RB.answer({"what": "open", "chat": cid})["messages"]
check(f"a rebuilt chat carries the file the person sent ({[c['name'] for c in msgs[0].get('files', [])]})",
      [c["name"] for c in msgs[0].get("files", [])] == ["notes.pdf"] and msgs[0]["files"][0]["kind"] == "pdf"
      and msgs[0]["text"].startswith("look"))
check("and the file a reply named, as a card it can open", [c["media"] for c in msgs[1].get("files", [])] == [p["media"]])
import time as _t  # noqa: E402
t0 = _t.time()
for _ in range(20):
    RB.answer({"what": "open", "chat": cid})
each = (_t.time() - t0) / 20
check(f"reopening a chat with a picture doesn't redraw its thumbnail each time ({each * 1000:.1f} ms an open)", each < 0.02)
check("ids stay the same across restarts (kept on disk)", M._id_for(pic) == p["media"] and os.path.isfile(M.STORE))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all relay media checks passed")
