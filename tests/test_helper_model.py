"""A second, smaller model for helpers.

The helper model runs on its own port next to the main one. While it runs,
helpers and sub-agents use it and the main model stays on the conversation;
premium work stays on the main model. A stand-in server plays llama-server.

    .\\.venv\\Scripts\\python.exe tests\\test_helper_model.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver, subagents                              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


d = tempfile.mkdtemp(prefix="hearth-helper-")
model = os.path.join(d, "tiny.gguf")
open(model, "wb").write(b"GGUF" + b"\0" * 64)
stand_in = os.path.join(d, "fake_server.py")
open(stand_in, "w", encoding="utf-8").write("""import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index('--port') + 1])
if '--die' in sys.argv: sys.exit(3)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps({"data": [{"id": "tiny"}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
HTTPServer(('127.0.0.1', port), H).serve_forever()
""")
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
llmserver.HELPER_PORT = port
llmserver.WORKSPACE = __import__("pathlib").Path(d)
llmserver.detect_gpu_compute_cap = lambda: 8.9
llmserver.find_native_llama_server = lambda: {"exe": sys.executable, "dll_dirs": []}
real_popen = llmserver.subprocess.Popen
extra = {"args": []}


def popen(cmd, **kw):
    # llama-server's arguments go to the stand-in script instead.
    return real_popen([sys.executable, stand_in] + cmd[1:] + extra["args"], **kw)


llmserver.subprocess.Popen = popen

check("a missing model file is refused", not llmserver.start_helper(os.path.join(d, "nope.gguf")).get("ok"))
r = llmserver.start_helper(model, ctx=4096, wait_s=20)
check("the helper model starts on its own port", r.get("ok") and str(port) in r.get("url", ""))
st = llmserver.helper_status()
check("its status says running, with the model name", st["running"] and st["model"] == "tiny")
os.environ["LOCAL_API_BASE"] = "http://127.0.0.1:1234/v1"
base, _key, _m = subagents._route_for_cost_class("standard")
check("a normal helper is routed to it", base == llmserver.helper_base())
check("so is a cheap one", subagents._route_for_cost_class("cheap")[0] == llmserver.helper_base())
check("premium work stays on the main model", subagents._route_for_cost_class("premium")[0] == "http://127.0.0.1:1234/v1")
check("the Models status reports it", llmserver.status().get("helper", {}).get("running") is True)
llmserver.stop_helper()
check("stopping it ends routing to it",
      not llmserver.helper_status()["running"] and subagents._route_for_cost_class("standard")[0] == "http://127.0.0.1:1234/v1")

extra["args"] = ["--die"]
r = llmserver.start_helper(model, wait_s=10)
check("a helper that dies while loading is reported, not left running",
      not r.get("ok") and "stopped while loading" in (r.get("error") or "") and not llmserver.helper_status()["running"])

llmserver.subprocess.Popen = real_popen
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all helper model checks passed")
