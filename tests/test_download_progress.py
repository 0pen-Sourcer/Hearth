"""Resuming a download, and reporting how a model load is going.

Two things that leave a person staring at an interface that has stopped saying
anything.

A part file left behind at or past the end of the file makes the server answer
416 to every resume request, so the download fails identically forever and the
interface offers no way to clear it.

And a load bar driven by log lines: the engine prints its metadata, its
architecture summary and its device list before it reads a single tensor, so a
percentage taken from where a line falls in the log jumps most of the way up in
the first second and then holds still for the entire real wait.

    .\\.venv\\Scripts\\python.exe tests\\test_download_progress.py
"""
from __future__ import annotations

import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver as L                                  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# --------------------------------------------------------------- a resumable
BODY = b"x" * 4096


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(BODY):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(BODY)}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Length", str(len(BODY) - start))
            self.send_header("Content-Range",
                             f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
            self.end_headers()
            self.wfile.write(BODY[start:])
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


_srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{_srv.server_port}/f.bin"


def _fresh():
    d = Path(tempfile.mkdtemp())
    return d / "f.bin", d / "f.bin.part"


dest, tmp = _fresh()
L._download_with_resume(URL, dest, tmp)
check("a download with nothing on disk completes", dest.read_bytes() == BODY)

dest, tmp = _fresh()
tmp.write_bytes(BODY[:1000])
L._download_with_resume(URL, dest, tmp)
check("a half-finished part resumes to the right bytes", dest.read_bytes() == BODY)

# The wedge. A part longer than the file is what an abandoned attempt at a
# different build leaves behind, and it is unreachable from the interface.
dest, tmp = _fresh()
tmp.write_bytes(b"y" * (len(BODY) + 500))
try:
    L._download_with_resume(URL, dest, tmp)
    ok = dest.exists() and dest.read_bytes() == BODY
except Exception as e:
    print("      raised:", type(e).__name__, e)
    ok = False
check("a part past the end of the file starts over instead of wedging", ok)

dest, tmp = _fresh()
tmp.write_bytes(BODY)
try:
    L._download_with_resume(URL, dest, tmp)
    ok = dest.read_bytes() == BODY
except Exception as e:
    print("      raised:", type(e).__name__, e)
    ok = False
check("a part that is exactly the whole file also recovers", ok)

_srv.shutdown()

# ------------------------------------------------- what the load bar is told
# Everything in this group is printed from the header, before the read.
EARLY = [
    "0.02.313 I srv    load_model: loading model 'x.gguf'",
    "llama_model_loader: - kv  12: general.name str",
    "print_info: arch             = qwen3",
    "llm_load_print_meta: n_ctx  = 8192",
    "ggml_cuda_init: found 1 CUDA devices",
    "load_tensors: offloading 33 repeating layers to GPU",
]
# And everything here is printed once the weights are resident.
LATE = [
    ("0.33 I cmn init: llama threadpool init, n_threads = 16", 90),
    ("0.35 I srv load_model: initializing, n_slots = 1, n_ctx_slot = 81920", 93),
    ("0.35 I srv  llama_server: model loaded", 97),
    ("0.35 I srv  llama_server: listening on http://127.0.0.1:1234", 100),
]

for ln in EARLY:
    got = L._phase_for_line(ln)
    check(f"a header line stays at the bottom of the bar: {ln[:38]!r}",
          got is not None and got[1] <= 10)

for ln, want in LATE:
    got = L._phase_for_line(ln)
    check(f"a post-read line reports {want}: {ln[:38]!r}",
          got is not None and got[1] == want)

check("an uninteresting line moves nothing",
      L._phase_for_line("W model has unused tensor blk.32.attn_norm.weight") is None)

# ---------------------------------------------- the measurement that fills in
check("a single-file model sizes as itself",
      L._model_total_bytes(__file__) == os.path.getsize(__file__))
check("an unreadable path disables the measurement rather than raising",
      L._model_total_bytes("Z:/nope/nope.gguf") == 0)
check("a process that is gone yields no reading",
      L._weight_load_fraction(0x7FFFFFFF, 1000) is None)
check("no denominator yields no reading",
      L._weight_load_fraction(os.getpid(), 0) is None)
_f = L._weight_load_fraction(os.getpid(), 10 ** 12)
check("a live process reads as a fraction between nothing and everything",
      _f is not None and 0.0 <= _f <= 1.0)
check("a denominator smaller than the process clamps instead of overshooting",
      L._weight_load_fraction(os.getpid(), 1) == 1.0)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all download and load-progress checks passed")
