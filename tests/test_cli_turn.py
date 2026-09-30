"""A real turn through the terminal's agent loop, against a stand-in model.

The terminal runs its own agent loop, separate from the one the window uses,
and nothing exercised it: the other terminal test only checks flags and end of
input, which never reach a model. That let a change leave the per-turn setup
stranded after a return statement, so the loop guard was never created and the
first tool call of every session crashed the terminal with an AttributeError.
It also meant one interrupt, never cleared, cut every later reply short.

This runs the terminal as a process against a small local server that speaks
the OpenAI streaming format: the first request is answered with one tool call
(get_time, which only reads the clock), the second with a plain reply. Nothing
leaves the machine and no model is loaded.

    .\\.venv\\Scripts\\python.exe tests\\test_cli_turn.py
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "hearth_cli.py")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


MODEL = "stand-in-model"
SEEN = {"chat": 0, "tool_result_seen": False}


def _chunk(delta, finish=None):
    return ("data: " + json.dumps({
        "id": "x", "object": "chat.completion.chunk", "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }) + "\n\n").encode()


class _Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list",
                                    "data": [{"id": MODEL, "object": "model"}]})
        return self._json(404, {"error": "not here"})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": "not here"})
        SEEN["chat"] += 1
        msgs = req.get("messages") or []
        if any(m.get("role") == "tool" for m in msgs):
            SEEN["tool_result_seen"] = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if not SEEN["tool_result_seen"]:
            self.wfile.write(_chunk({"role": "assistant", "tool_calls": [{
                "index": 0, "id": "call_1", "type": "function",
                "function": {"name": "get_time", "arguments": "{}"}}]}))
            self.wfile.write(_chunk({}, "tool_calls"))
        else:
            self.wfile.write(_chunk({"role": "assistant",
                                     "content": "It is time to test."}))
            self.wfile.write(_chunk({}, "stop"))
            # The server's own count of what it received, far from any estimate.
            self.wfile.write(("data: " + json.dumps({
                "id": "x", "object": "chat.completion.chunk", "model": MODEL,
                "choices": [], "usage": {"prompt_tokens": 7777,
                                         "completion_tokens": 5, "total_tokens": 7782},
            }) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
threading.Thread(target=srv.serve_forever, daemon=True).start()

env = dict(os.environ)
env.update({
    "JARVIS_WORKSPACE": tempfile.mkdtemp(prefix="hearth-cli-turn-"),
    "LOCAL_API_BASE": f"http://127.0.0.1:{srv.server_address[1]}/v1",
    "LOCAL_MODEL": MODEL,
    "LOCAL_API_KEY": "test",
    "JARVIS_NO_ONBOARDING": "1",
    "HEARTH_NO_UPDATE_CHECK": "1",
    "PYTHONIOENCODING": "utf-8",
})
try:
    p = subprocess.run([sys.executable, "-X", "utf8", CLI],
                       input=b"what time is it\n/tokens\n", capture_output=True,
                       timeout=180, env=env, cwd=ROOT)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    code = p.returncode
except subprocess.TimeoutExpired as e:
    out = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace")
    code = None
srv.shutdown()

_tok = next((l for l in out.splitlines() if "tokens /" in l), "")
check(f"/tokens reports what the server measured, not an estimate: {_tok.strip()!r}",
      "measured" in _tok)
check("the terminal did not crash", "fatal" not in out.lower()
      and "Traceback" not in out)
check(f"and exited cleanly (exit {code})", code == 0)
check("the model was asked twice: once for the call, once after its result",
      SEEN["chat"] >= 2)
check("the tool's result went back to the model", SEEN["tool_result_seen"])
check("and the final reply reached the terminal", "It is time to test." in out)
check("an earlier reply was not cut short as interrupted",
      "interrupted" not in out.lower())

if FAILS:
    print("\n--- terminal output (tail) ---")
    print(out[-2500:])
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall terminal turn checks passed")
