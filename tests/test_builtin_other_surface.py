"""Hearth's own server, started by another Hearth window or the terminal.

The window only knew a server it had started itself. With the terminal's
server on port 1234, loading in the window failed with "port taken by another
LLM server (most likely LM Studio or Ollama)", and pointing the window at that
address announced "JARVIS now thinks with LM Studio".

A stand-in server process answers /v1/models as llama-server does.
Uses a throwaway home folder.

    .\\.venv\\Scripts\\python.exe tests\\test_builtin_other_surface.py
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HOME = tempfile.mkdtemp(prefix="hearth-othersurface-")
os.environ["USERPROFILE"] = os.environ["HOME"] = HOME
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver as L                                    # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


SERVER = r'''
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
key = sys.argv[2]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        ok = True              # llama-server lists its models without the key
        body = b'{"data":[{"id":"m"}]}' if ok else b'{}'
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(key):
    port = free_port()
    p = subprocess.Popen([sys.executable, "-c", SERVER, str(port), key])
    atexit.register(p.kill)                # gone even when a check crashes
    url = f"http://127.0.0.1:{port}/v1"
    for _ in range(50):
        if L.external_server_running(url, timeout=0.3, api_key=key or None):
            break
        time.sleep(0.1)
    return p, port, url


model = os.path.join(HOME, "chat.gguf")
open(model, "wb").write(b"GGUF")

# The terminal's server: it wrote the run-file, as start_builtin does.
term, port, url = serve(L.BUILTIN_API_KEY)
L._RUNFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
L._RUNFILE_PATH.write_text(json.dumps({"url": url.replace("127.0.0.1", "localhost"), "pid": term.pid,
                                       "model_path": model}), encoding="utf-8")
check("a server the terminal started is known as Hearth's own", bool(L.ours_at(url)))
r = L.start_builtin(model, port=port, n_gpu_layers=0)       # CPU: no engine needed here
check(f"loading the same model in the window uses it instead of failing on the port {r}",
      r.get("ok") is True and r.get("adopted") is True)

# Another program on its own port is not Hearth's.
other, oport, ourl = serve("")
check("another program serving models is not taken for Hearth's own", L.ours_at(ourl) == {})
r = L.start_builtin(model, port=oport, n_gpu_layers=0)
check(f"and loading there says so without naming a program it can't know {r.get('error')!r}",
      r.get("ok") is False and "most likely" not in (r.get("error") or ""))
other.kill()

# Stopping the terminal's server, as a load of a different model does first.
check("the other surface's server can be stopped from here", L._stop_other_surface() is True)
term.wait(10)
check("and it is gone, with its run-file", term.poll() is not None and not L._RUNFILE_PATH.exists())

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all other-surface server checks passed")
