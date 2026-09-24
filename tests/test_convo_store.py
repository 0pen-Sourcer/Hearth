"""Saving a conversation, and what a killed save leaves behind.

A conversation is written to a temporary beside its target and renamed onto it,
so a crash mid-write leaves the previous version rather than a half-written
one. Two things that were not handled.

Killing the process between the write and the rename leaves the temporary
there, and the cleanup on the way out cannot run for a kill. It ends in .json,
so the lister read it as a conversation: a complete second copy of the chat it
was about to become, sitting in the sidebar. One had been doing that for a
month on the machine this was found on.

And the rename itself can fail on Windows while antivirus or the search indexer
still holds the just-closed file, which lost the turn with nothing said.

    .\\.venv\\Scripts\\python.exe tests\\test_convo_store.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import web                                              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


CONVO = {"id": "c_real", "title": "a real chat", "updated": 100,
         "created": 1, "messages": [{"role": "user", "content": "hi"}]}

_real_dir = web.CONVOS_DIR
d = tempfile.mkdtemp()
web.CONVOS_DIR = d

with open(os.path.join(d, "c_real.json"), "w", encoding="utf-8") as f:
    json.dump(CONVO, f)

listed = web._list_convos()
check("a real conversation is listed", [c["id"] for c in listed] == ["c_real"])

# What a kill between the write and the rename leaves: a complete, valid,
# parseable conversation under a temporary name.
for name in (".tmp-abc123.json", web._TMP_PREFIX + "xyz789.json"):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(CONVO, f)

listed = web._list_convos()
check("a leftover temporary is not a second copy of the conversation",
      [c["id"] for c in listed] == ["c_real"])
check("and both naming styles are recognised as temporary", len(listed) == 1)

# The sweep leaves a write that is happening right now alone.
os.utime(os.path.join(d, ".tmp-abc123.json"), None)
fresh = os.path.join(d, web._TMP_PREFIX + "inflight.json")
with open(fresh, "w", encoding="utf-8") as f:
    f.write("{}")
old = os.path.join(d, web._TMP_PREFIX + "stale.json")
with open(old, "w", encoding="utf-8") as f:
    f.write("{}")
os.utime(old, (time.time() - 3600, time.time() - 3600))

removed = web._sweep_stale_temp_files(d)
check("the sweep takes the stale temporary", not os.path.exists(old))
check("and leaves one that is still being written", os.path.exists(fresh))
check("it reports what it took", removed == 1)
check("a real conversation is never swept",
      os.path.exists(os.path.join(d, "c_real.json")))
check("sweeping a directory that is not there is not an error",
      web._sweep_stale_temp_files(os.path.join(d, "nope")) == 0)

# ------------------------------------------------- the write itself still works
target = os.path.join(d, "c_written.json")
_before = set(os.listdir(d))
web._atomic_write_json(target, CONVO, indent=2)
with open(target, encoding="utf-8") as f:
    check("an atomic write lands the whole thing", json.load(f)["id"] == "c_real")
check("and leaves no temporary behind",
      set(os.listdir(d)) - _before == {"c_written.json"})

# A rename refused because the file is briefly held open is retried, not lost.
_real_replace = os.replace
_calls = {"n": 0}


def _flaky(src, dst):
    _calls["n"] += 1
    if _calls["n"] < 3:
        raise PermissionError(32, "used by another process")
    return _real_replace(src, dst)


os.replace = _flaky
try:
    web._atomic_write_json(os.path.join(d, "c_retry.json"), CONVO)
    ok = True
except Exception as e:
    print("      raised:", type(e).__name__, e)
    ok = False
finally:
    os.replace = _real_replace
check("a rename held off by a file lock is retried rather than lost", ok)
check("and it took more than one attempt to get there", _calls["n"] >= 3)

# ------------------------------------------- the chat search index agrees
# The index keeps a timestamp per conversation id and asks whether any file on
# disk is newer. A staged write carries the .json extension and an id that was
# never indexed, so it read as an unindexed conversation and the index reported
# itself stale on every single query, rebuilding from scratch each time.
from hearth import session_search as S                              # noqa: E402

_sd = tempfile.mkdtemp()
S.CONVOS_DIR, S.INDEX_PATH = _sd, os.path.join(_sd, "idx.db")
with open(os.path.join(_sd, "c_one.json"), "w", encoding="utf-8") as f:
    json.dump({"id": "c_one", "title": "t", "updated": 1, "created": 1,
               "messages": [{"role": "user", "content": "hello world"}]}, f)
S.rebuild_index()
check("a freshly built index is not stale", S._needs_reindex() is False)

with open(os.path.join(_sd, ".tmp-leftover.json"), "w", encoding="utf-8") as f:
    json.dump({"id": "c_one", "messages": []}, f)
check("a staged write does not make the index look stale forever",
      S._needs_reindex() is False)

S.rebuild_index()
_hits = S.search("hello world")
check("and the staged write is not indexed as a second conversation",
      len({getattr(h, "conversation_id", None) for h in _hits}) <= 1)

_src = open(os.path.join(ROOT, "hearth", "web.py"), encoding="utf-8").read()
check("the server clears stale temporaries when it starts",
      "_sweep_stale_temp_files(_d)" in _src)

web.CONVOS_DIR = _real_dir

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all conversation store checks passed")
