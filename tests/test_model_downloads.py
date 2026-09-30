"""Model downloads keep going without the window and resume after a restart.

A download used to live on the window's HTTP request: closing the window to
the tray dropped the connection and stopped it. Only one resume note was kept
(repo + file), and finishing any other download erased it, so an interrupted
download lost its repo and could not resume on its own.

    .\\.venv\\Scripts\\python.exe tests\\test_model_downloads.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver                                         # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


tmp = Path(tempfile.mkdtemp(prefix="hearth_dl_"))
llmserver.MODELS_DIR = tmp
llmserver._PENDING_DL_PATH = str(tmp / "pending_download.json")
llmserver.record_model_source = lambda *a, **k: None
gate = {}


def fake_download(url, dest, part, on_progress, cancel=None):
    """Writes the part file in steps; a gate per file controls when it finishes."""
    ev = gate.setdefault(dest.name, threading.Event())
    total = 100
    part.write_bytes(b"x" * 10)
    for i in range(1, 400):
        if cancel and cancel():
            raise llmserver.DownloadCancelled()
        if on_progress:
            on_progress(min(i, total), total)
        if ev.wait(0.01):
            part.replace(dest)
            return total
    raise RuntimeError("never released")


llmserver._download_with_resume = fake_download
llmserver._hf_resolve_url = lambda repo, fn: f"https://example.invalid/{repo}/{fn}"


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


# Two downloads at once, each remembered with its own repo.
llmserver.start_model_download("org/a", "a.gguf")
llmserver.start_model_download("org/b", "b.gguf")
pend = llmserver._read_pending()
check("both downloads are remembered with their repos",
      pend.get("a.gguf", {}).get("repo") == "org/a" and pend.get("b.gguf", {}).get("repo") == "org/b")

# The download runs in the backend: nobody is reading progress, and it still finishes.
gate.setdefault("b.gguf", threading.Event()).set()
check("a download finishes with no window attached",
      wait_for(lambda: (llmserver.model_download_state("b.gguf") or {}).get("status") == "done"))
check("finishing one download keeps the other's resume note",
      llmserver._read_pending().get("a.gguf", {}).get("repo") == "org/a"
      and "b.gguf" not in llmserver._read_pending())

# Starting the same file again attaches to the running job instead of a second writer.
job1 = llmserver._dl_jobs["a.gguf"]
check("starting a running download again reuses it",
      llmserver.start_model_download("org/a", "a.gguf") is job1)

# Pause stops it, keeps the part file, and marks it so it isn't auto-resumed.
check("pause stops a running download", llmserver.pause_model_download("a.gguf"))
check("the download reports paused",
      wait_for(lambda: (llmserver.model_download_state("a.gguf") or {}).get("status") == "paused"))
check("the partial file is kept", (tmp / "a.gguf.part").is_file())
check("a paused download is not resumed on start", llmserver.resume_interrupted_downloads() == [])

# An interrupted (not paused) download resumes on start.
llmserver.save_pending_download("org/c", "c.gguf")
(tmp / "c.gguf.part").write_bytes(b"x")
started = llmserver.resume_interrupted_downloads()
check("an interrupted download resumes on start", started == ["c.gguf"])
parts = {p["filename"]: p for p in llmserver.list_partial_downloads()}
check("the partial list shows it running and its repo",
      parts.get("c.gguf", {}).get("active") is True and parts["c.gguf"]["repo"] == "org/c")
gate.setdefault("c.gguf", threading.Event()).set()
wait_for(lambda: (llmserver.model_download_state("c.gguf") or {}).get("status") == "done")

# A resume note whose part file is gone is dropped, not retried forever.
llmserver.save_pending_download("org/d", "d.gguf")
check("a note with no partial file is dropped",
      llmserver.resume_interrupted_downloads() == [] and "d.gguf" not in llmserver._read_pending())

# The single-entry note older versions wrote still reads.
Path(llmserver._PENDING_DL_PATH).write_text('{"repo": "org/old", "filename": "old.gguf"}', encoding="utf-8")
check("an older single-download note still reads",
      llmserver._read_pending() == {"old.gguf": {"repo": "org/old", "paused": False}})

# Discarding stops the running job before deleting its part file.
llmserver.start_model_download("org/e", "e.gguf")
wait_for(lambda: (tmp / "e.gguf.part").is_file())
r = llmserver.discard_partial("e.gguf")
check("discard stops a running download and deletes its part",
      r.get("ok") and not (tmp / "e.gguf.part").exists()
      and (llmserver.model_download_state("e.gguf") or {}).get("status") != "downloading")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all model download checks passed")
