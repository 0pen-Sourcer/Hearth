"""Whether the model can see, as the terminal decides it.

The terminal kept its own check: LM Studio's /api/v0, then the model's name.
Hearth's built-in llama.cpp server has no /api/v0, so on it the name decided:
a vision model whose name lacks "vl" was treated as blind even with its
projector loaded, and a "gemma-3" with no projector as seeing. llama.cpp's
/props says which is true, and the tools already asked it.

    .\\.venv\\Scripts\\python.exe tests\\test_vision_check.py
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading

os.environ["JARVIS_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-vision-")
os.environ["JARVIS_NO_ONBOARDING"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import hearth_cli as C                                               # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


STATE = {"vision": True}


class _LlamaCpp(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        if self.path.startswith("/props"):
            body = {"modalities": {"vision": STATE["vision"], "audio": False}}
            code = 200
        else:
            body, code = {"error": "not found"}, 404
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _LlamaCpp)
threading.Thread(target=srv.serve_forever, daemon=True).start()
C.LOCAL_API_BASE = f"http://127.0.0.1:{srv.server_address[1]}/v1"


def sees(model):
    cli = object.__new__(C.JarvisCLI)
    cli.current_model = model
    return cli._is_vision_capable()


STATE["vision"] = True
check("a model with its projector loaded can see, whatever its name says",
      sees("qwen35-9b-q4_k_m") is True)
STATE["vision"] = False
check("a vision-named model with no projector loaded cannot", sees("gemma-3-4b-it") is False)
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all vision check checks passed")
