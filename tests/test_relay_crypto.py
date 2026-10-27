"""The PC side of the relay's encryption: two parties built from relay_crypto
pair through a pairing payload, show the same six digits, derive matching keys
and read each other's frames; a replayed or tampered frame is refused, a
mailbox message opens once, and the room name reveals nothing.

    .\\.venv\\Scripts\\python.exe tests\\test_relay_crypto.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import relay_crypto as R  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


pc, phone = R.new_private(), R.new_private()
ps = os.urandom(16)
payload = R.pairing_payload("wss://relay.example", R.raw_public(pc), ps)
check("the room name doesn't contain the secret or the key",
      R.b64(ps) not in payload["room"] and payload["pc"] not in payload["room"])
check("the room follows from the key and the secret alone",
      payload["room"] == R.room_for(R.raw_public(pc), ps))

proof = R.phone_proof(ps, R.raw_public(phone))
check("a phone that read the code proves it", R.proof_ok(ps, R.raw_public(phone), proof))
check("a proof made without the code is refused", not R.proof_ok(os.urandom(16), R.raw_public(phone), proof))

pc_shared = R.dh(pc, R.raw_public(phone))
phone_shared = R.dh(phone, R.unb64(payload["pc"]))
check("both screens show the same six digits",
      R.check_digits(pc_shared, payload["room"]) == R.check_digits(phone_shared, payload["room"]))

pc_eph, phone_eph = R.new_private(), R.new_private()
a = R.session_keys(pc_shared, R.dh(pc_eph, R.raw_public(phone_eph)), payload["room"],
                   R.raw_public(pc_eph), R.raw_public(phone_eph))
b = R.session_keys(phone_shared, R.dh(phone_eph, R.raw_public(pc_eph)), payload["room"],
                   R.raw_public(pc_eph), R.raw_public(phone_eph))
check("both sides derive the same connection keys", a == b and a[0] != a[1])

on_pc, on_phone = R.Channel(a[0], a[1]), R.Channel(b[1], b[0])
frame = on_pc.seal({"type": "reply", "text": "Done. Your report is on the desktop."})
check("the phone reads the PC's message", on_phone.open(frame) == {"type": "reply",
                                                                   "text": "Done. Your report is on the desktop."})
check("and refuses the same frame a second time", on_phone.open(frame) is None)
back = on_phone.seal({"type": "user", "text": "नमस्ते from the phone"})
bad = dict(back)
bad["c"] = bad["c"][:-4] + ("AAAA" if not bad["c"].endswith("AAAA") else "BBBB")
check("a tampered frame is refused", on_pc.open(bad) is None)
check("the PC reads the phone's message", on_pc.open(back) == {"type": "user", "text": "नमस्ते from the phone"})

box_key = R.mailbox_key(pc_shared, payload["room"])
check("the mailbox key needs only the pairing", box_key == R.mailbox_key(phone_shared, payload["room"]))
seen: set = set()
box = R.seal_box(box_key, {"x": 1})
check("a mailbox message is delivered once",
      R.open_box(box_key, box, seen) == {"x": 1} and R.open_box(box_key, box, seen) is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all relay crypto checks passed")
