"""A stand-in for llama-server, for tests: lists the options in STANDIN_FLAGS
for --help, writes each start's arguments and each request it gets to
STANDIN_LOG (one JSON line each), and answers chat requests with "done"
after STANDIN_DELAY seconds."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("STANDIN_LOG", "")


def log(rec):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


args = sys.argv[1:]
if "--help" in args:
    print("usage: llama-server [options]\n")
    for flag in os.environ.get("STANDIN_FLAGS", "").split():
        print(f"{flag} N        an option")
    sys.exit(0)
log({"argv": args})
port = int(args[args.index("--port") + 1])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "standin", "object": "model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        log({"port": port, "path": self.path, "body": body})
        time.sleep(float(os.environ.get("STANDIN_DELAY", "0")))
        self._send({"id": "x", "object": "chat.completion", "created": 0, "model": "standin",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
