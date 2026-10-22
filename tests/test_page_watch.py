"""The window asks for its page again when it never arrives.

    .\\.venv\\Scripts\\python.exe tests\\test_page_watch.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth.desktop_attach import reload_if_stalled  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        for h in self.handlers:
            h()


class _Win:
    def __init__(self):
        self.events = type("E", (), {})()
        self.events.loaded = _Event()
        self.asked = []

    def load_url(self, url):
        self.asked.append(url)


stalled = _Win()
reload_if_stalled(stalled, "http://127.0.0.1:1/", wait_s=1.0, tries=2)
time.sleep(3.5)                           # reloads due at 1 s and 2 s; room for a busy machine
check(f"a page that never arrives is asked for again, twice at most ({len(stalled.asked)})", stalled.asked == ["http://127.0.0.1:1/"] * 2)

fine = _Win()
reload_if_stalled(fine, "http://127.0.0.1:1/", wait_s=0.15, tries=2)
fine.events.loaded.fire()
time.sleep(0.5)
check(f"a page that arrived is left alone ({len(fine.asked)})", fine.asked == [])

# Half a wait either side of the first reload, so a busy machine can't move it.
late = _Win()
reload_if_stalled(late, "http://127.0.0.1:1/", wait_s=1.0, tries=2)
time.sleep(1.5)
late.events.loaded.fire()
time.sleep(1.5)
check(f"once the second try arrives there is no third ({len(late.asked)})", late.asked == ["http://127.0.0.1:1/"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all page watch checks passed")
