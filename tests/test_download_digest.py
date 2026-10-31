"""Downloads with a known sha256 are refused when the bytes don't match; Kokoro is pinned.

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\test_download_digest.py
"""
from __future__ import annotations

import hashlib
import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTH_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-digest-")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import llmserver as L, voice      # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


BODY = b"engine bytes " * 5000
GOOD = hashlib.sha256(BODY).hexdigest()


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/f.zip"
d = Path(tempfile.mkdtemp(prefix="hearth-digest-dl-"))

bad = d / "bad.zip"
try:
    L._download_with_resume(url, bad, d / "bad.zip.part", sha256="sha256:" + "0" * 64)
    raised = False
except L.DigestMismatch:
    raised = True
except TypeError:
    raised = False
check("a file that doesn't match its published sha256 is refused", raised)
check("and nothing is left behind to be used", not bad.exists() and not (d / "bad.zip.part").exists())

good = d / "good.zip"
L._download_with_resume(url, good, d / "good.zip.part", sha256="sha256:" + GOOD.upper())
check("a matching file is kept", good.exists() and good.read_bytes() == BODY)
check("a cached file is checked against the asset's digest",
      L.cached_matches(good, {"digest": "sha256:" + GOOD}) and not L.cached_matches(good, {"digest": "sha256:" + "1" * 64}))
check("an asset with no digest (older uploads) isn't refused for that", L.cached_matches(good, {}))

check("Kokoro comes from one fixed release, not releases/latest",
      "/latest/" not in voice.KOKORO_MODEL_URL and "/latest/" not in voice.KOKORO_VOICES_URL)
check("both Kokoro files carry a sha256",
      all(len(getattr(voice, "KOKORO_SHA256", {}).get(u, "")) == 64 for u in (voice.KOKORO_MODEL_URL, voice.KOKORO_VOICES_URL)))
src = open(os.path.join(ROOT, "install.ps1"), encoding="utf-8").read()
check("install.ps1 pulls the same pinned files", voice.KOKORO_MODEL_URL in src and voice.KOKORO_SHA256[voice.KOKORO_MODEL_URL] in src)

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all download digest checks passed")
