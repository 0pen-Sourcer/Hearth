"""Sign every entry in announcements.json (repo root) with your private key, in
place. Run this before you commit + push a new announcement.

    python scripts/sign_announcement.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hearth import announcements as a  # noqa: E402

PRIV = os.path.join(os.path.expanduser("~"), ".hearth", "announce_private.key")
FEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "announcements.json")

if not os.path.exists(PRIV):
    print("No private key found — run scripts/gen_announce_key.py first.")
    sys.exit(1)
if not os.path.exists(FEED):
    print("No announcements.json in the repo root to sign.")
    sys.exit(1)

d = json.load(open(FEED, encoding="utf-8"))
entries = d.get("announcements") if isinstance(d, dict) else d
signed = [a.sign_entry(e, PRIV) for e in entries]
json.dump({"announcements": signed}, open(FEED, "w", encoding="utf-8"), indent=2)
print(f"Signed {len(signed)} announcement(s). Now commit + push announcements.json.")
