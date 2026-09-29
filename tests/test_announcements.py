"""Broadcasts reaching the people they are written for.

Two separate failures, either of which meant no announcement ever reached
anyone.

The feed file was gitignored and had never been committed, so the address every
install polls answered 404 for the feed's whole life.

And the rule that stops a brand-new install from being shown old announcements
was "on the first successful fetch, mark whatever is there as seen". A fetch
that failed returned before recording anything, and every fetch had failed, so
no install had ever recorded one. The first announcement ever published would
have been treated as backlog and swallowed on every machine.

Signing uses a throwaway key made here, so this needs no real key and publishes
nothing.

    .\\.venv\\Scripts\\python.exe tests\\test_announcements.py
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization                         # noqa: E402

from hearth import announcements as A                                            # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------ the feed is shipped
ignored = subprocess.run(["git", "check-ignore", "-q", "announcements.json"],
                         cwd=ROOT).returncode == 0
check("the feed file is not ignored, so it can be committed and served", not ignored)

# -------------------------------------------------- a throwaway signing key
priv = Ed25519PrivateKey.generate()
key_file = os.path.join(tempfile.mkdtemp(), "k")
with open(key_file, "wb") as f:
    f.write(base64.b64encode(priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())))
A.ANNOUNCE_PUBKEY_B64 = base64.b64encode(priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def entry(eid, created):
    return A.sign_entry({"id": eid, "title": "t", "body": "b", "kind": "info",
                         "created": created}, key_file)


def fresh_machine():
    d = tempfile.mkdtemp()
    A._SEEN_PATH = os.path.join(d, "seen.json")
    A._SINCE_PATH = os.path.join(d, "since.txt")
    return d


FEED = {"announcements": []}


def serve(*entries):
    FEED["announcements"] = list(entries)


A._fetch = lambda url, timeout=10: FEED

# --------------------------------------------- the case that swallowed everything
fresh_machine()
now = time.time()


def failing(url, timeout=10):
    raise OSError("HTTP Error 404")


A._fetch = failing
check("a fetch that fails returns nothing", A.fetch_new() == [])
A._fetch = lambda url, timeout=10: FEED
serve(entry("first-ever", int(time.time()) + 5))
got = [e["id"] for e in A.fetch_new()]
check("the first announcement published after a failed poll is shown, not "
      "swallowed as backlog", got == ["first-ever"])
check("and shown once", A.fetch_new() == [])

# ------------------------------------------------- a new install skips old news
fresh_machine()
serve(entry("old-news", int(now) - 86400))
check("a brand-new install is not shown announcements written before it existed",
      A.fetch_new() == [])
serve(entry("old-news", int(now) - 86400), entry("fresh", int(time.time()) + 5))
check("but is shown the ones that come after",
      [e["id"] for e in A.fetch_new()] == ["fresh"])

# ------------------------------------------ an existing user sees what is unseen
fresh_machine()
A._save_seen({"demo-toast"})             # a seen list from an older build
serve(entry("demo-toast", int(now) - 999), entry("while-away", int(now) - 3600))
check("an upgrading user is shown what they have not seen",
      [e["id"] for e in A.fetch_new()] == ["while-away"])

# ------------------------------------------------------------- forgeries
fresh_machine()
forged = entry("forged", int(time.time()) + 5)
forged["body"] = "changed after signing"
serve(forged)
check("an entry altered after signing is never shown", A.fetch_new() == [])
unsigned = {"id": "unsigned", "title": "t", "body": "b", "created": int(time.time()) + 5}
serve(unsigned)
check("an unsigned entry is never shown", A.fetch_new() == [])
backdated = entry("backdated", int(now) - 86400)
backdated["created"] = int(time.time()) + 60
serve(backdated)
check("moving an entry's date forward breaks its signature", A.fetch_new() == [])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all announcement checks passed")
