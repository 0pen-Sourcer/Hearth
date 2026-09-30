"""Pausing a model download, and closing the window during one.

The download runs in the backend, so closing the window (which drops the
progress connection) must not stop it, while Pause must stop it and keep the
part file. Resume, or a second start for the same file, must never put two
writers on one part file.

This runs the real endpoints against a deliberately slow local file server
and watches the part file.

    .\\.venv\\Scripts\\python.exe tests\\test_download_cancel.py
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver, web                                  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------- a slow file server
SIZE = 6 * 1024 * 1024          # 6 MB, served in small slow chunks
CHUNK = 64 * 1024
BODY = os.urandom(SIZE)


class _Slow(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{SIZE - 1}/{SIZE}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(SIZE - start))
        self.end_headers()
        try:
            for off in range(start, SIZE, CHUNK):
                self.wfile.write(BODY[off:off + CHUNK])
                self.wfile.flush()
                time.sleep(0.05)       # about 1.3 MB/s, so the whole file takes ~5s
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


files = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
threading.Thread(target=files.serve_forever, daemon=True).start()
FILE_URL = f"http://127.0.0.1:{files.server_address[1]}/model.gguf"

# Point the downloader at the slow server and a scratch models folder.
MODELS = tempfile.mkdtemp(prefix="hearth-dl-cancel-")
_real_dir = llmserver.MODELS_DIR
llmserver.MODELS_DIR = type(_real_dir)(MODELS)
_real_url = getattr(llmserver, "_hf_resolve_url", None)
llmserver._hf_resolve_url = lambda repo, filename: FILE_URL
_real_pending = llmserver._PENDING_DL_PATH
llmserver._PENDING_DL_PATH = os.path.join(MODELS, "pending_download.json")


class _Quiet(web.HearthHandler):
    def log_message(self, *_a, **_kw):
        pass


api = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
threading.Thread(target=api.serve_forever, daemon=True).start()
PORT = api.server_address[1]
PART = os.path.join(MODELS, "model.gguf.part")
FINAL = os.path.join(MODELS, "model.gguf")


def start_download():
    """Open the download stream the way the window does and return the socket."""
    body = json.dumps({"repo": "test/repo", "filename": "model.gguf"})
    s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    s.sendall((f"POST /api/llmserver/download-hf HTTP/1.1\r\n"
               f"Host: 127.0.0.1:{PORT}\r\nOrigin: http://127.0.0.1:{PORT}\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
               + body).encode())
    return s


def part_size():
    try:
        return os.path.getsize(PART)
    except OSError:
        return 0


# ---------------------------------- close the window, then pause for real
sock = start_download()
deadline = time.time() + 10
while part_size() < 512 * 1024 and time.time() < deadline:
    time.sleep(0.05)
check("the download starts and writes a part file", part_size() > 0)

sock.close()                    # the window closes; the download should carry on
time.sleep(0.5)
before = part_size()
time.sleep(1.0)
check(f"closing the window does not stop the download ({before} -> {part_size()} bytes)",
      part_size() > before)

c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
c.request("POST", "/api/llmserver/download-pause", json.dumps({"filename": "model.gguf"}),
          {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
c.getresponse().read()
c.close()
time.sleep(1.0)
size_at_pause = part_size()
time.sleep(2.0)
size_later = part_size()
check(f"after Pause the part file stops growing "
      f"({size_at_pause} -> {size_later} bytes)", size_later == size_at_pause)
check("and it is not quietly finished in the background",
      not os.path.exists(FINAL))
check("the partial is kept, so Resume can continue from it", size_later > 0)

# ------------------------------------------------------- resume finishes it
sock = start_download()
sock.settimeout(30)
buf = b""
t0 = time.time()
while b'"type": "done"' not in buf and time.time() - t0 < 30:
    try:
        chunk = sock.recv(65536)
    except socket.timeout:
        break
    if not chunk:
        break
    buf += chunk
sock.close()
check("resuming completes the download", os.path.exists(FINAL))
if os.path.exists(FINAL):
    with open(FINAL, "rb") as f:
        got = f.read()
    check(f"and the file is byte for byte what the server sent "
          f"({len(got)} of {SIZE} bytes)", got == BODY)

# --------------------------------------------- two downloads of one file at once
for fn in (PART, FINAL):
    try:
        os.remove(fn)
    except OSError:
        pass
a = start_download()
deadline = time.time() + 10
while part_size() < 256 * 1024 and time.time() < deadline:
    time.sleep(0.05)
b = start_download()            # a second start for the same file, while the first runs
b.settimeout(40)
buf = b""
t0 = time.time()
while b'"type": "done"' not in buf and time.time() - t0 < 40:
    try:
        chunk = b.recv(65536)
    except socket.timeout:
        break
    if not chunk:
        break
    buf += chunk
a.close()
b.close()
time.sleep(1.0)
if os.path.exists(FINAL):
    with open(FINAL, "rb") as f:
        got = f.read()
    check(f"two overlapping starts still leave one intact file "
          f"({len(got)} of {SIZE} bytes)", got == BODY)
else:
    check("two overlapping starts still leave one intact file (none was written)", False)

api.shutdown()
files.shutdown()
llmserver.MODELS_DIR = _real_dir
llmserver._PENDING_DL_PATH = _real_pending
if _real_url is not None:
    llmserver._hf_resolve_url = _real_url

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all download cancel checks passed")
