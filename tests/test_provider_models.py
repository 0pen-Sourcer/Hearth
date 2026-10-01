"""Which models a cloud provider has is read from the provider.

Anthropic's list needs its own two headers; asked with only the usual Bearer
token it answers 400, and the model menu then showed the one saved model as if
the provider had no others. A model name written into Hearth as a default goes
stale, so one that the provider no longer lists is swapped for one it does,
while a name the person typed is left alone.

A small local server stands in for the provider.

    .\\.venv\\Scripts\\python.exe tests\\test_provider_models.py
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["JARVIS_WORKSPACE"] = os.environ["HEARTH_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-prov-")

from hearth import headless as hl                                    # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


url, headers = hl.model_list_request("https://api.anthropic.com/v1/", "sk-test")
check("Anthropic's list is asked with its own headers, and for more than twenty",
      url == "https://api.anthropic.com/v1/models?limit=100" and headers.get("x-api-key") == "sk-test"
      and headers.get("anthropic-version") and headers.get("Authorization") == "Bearer sk-test")
url, headers = hl.model_list_request("https://api.openai.com/v1", "sk-test")
check("everyone else gets the plain request", url == "https://api.openai.com/v1/models"
      and headers == {"Authorization": "Bearer sk-test"})
check("a trailing slash doesn't double up",
      hl.model_list_request("https://generativelanguage.googleapis.com/v1beta/openai/", "k")[0].endswith("/openai/models"))

req = hl.model_list_req("https://api.anthropic.com/v1/", "sk-test")
check("the ready-made request is a GET that carries those headers",
      req.get_method() == "GET" and req.get_header("X-api-key") == "sk-test" and req.data is None)

SEEN = []


class _Provider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append(dict(self.headers))
        if self.headers.get("Authorization") != "Bearer good":
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({"data": [{"id": "big-embedding-3"}, {"id": "newest-chat"}, {"id": "older-chat"},
                                    {"id": "imagen-4"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/v1"

check("the chat models a provider lists, in its order, without the ones that can't chat",
      hl.provider_models(base, "good") == ["newest-chat", "older-chat"])
check("a provider that refuses the key has no list", hl.provider_models(base, "bad") is None)
check("a name the provider lists is kept, whatever its case", hl.settle_model(base, "good", "Older-Chat") == "older-chat")
check("a name it no longer lists gives way to one it does", hl.settle_model(base, "good", "retired-model") == "newest-chat")
check("no name at all gets the first it lists", hl.settle_model(base, "good", "") == "newest-chat")
check("when the list can't be had, the name asked for stands", hl.settle_model(base, "bad", "retired-model") == "retired-model")

# The model menu lists every provider a key is saved for and reads each one's
# models with that saved key, so nothing has to be pasted again to look or switch.
import socket                                                        # noqa: E402
import urllib.request                                                # noqa: E402

from hearth import web                                               # noqa: E402

# Hearth takes 127.0.0.1 for a local server, so the stand-in answers on another
# loopback address to be treated as the cloud provider it plays. macOS has
# only the one, so the switch itself is checked on Windows and Linux.
try:
    far = http.server.ThreadingHTTPServer(("127.0.0.2", 0), _Provider)
    threading.Thread(target=far.serve_forever, daemon=True).start()
    base = f"http://127.0.0.2:{far.server_address[1]}/v1"
except OSError:
    far = None
hl.PROVIDERS["openai"] = ("OpenAI", base)
with open(os.path.join(web.WORKSPACE, "brain_keys.json"), "w", encoding="utf-8") as f:
    json.dump({"grok": {"url": "https://api.x.ai/v1", "key": "from-the-terminal"}}, f)
web._save_settings(dict(web._load_settings(), onboarded=True, llm_provider="gemini", llm_key="in-use",
                        llm_url=hl.PROVIDERS["gemini"][1], llm_model="some-gemini", provider_keys={"openai": "good"}))
check("a provider's key is found wherever it was saved",
      [web._provider_key(p) for p in ("gemini", "openai", "grok", "anthropic", "")]
      == ["in-use", "good", "from-the-terminal", "", ""])
saved = web._saved_providers()
check("the saved providers are listed with the one in use first, and no keys",
      [(p["id"], p["active"]) for p in saved] == [("gemini", True), ("openai", False), ("grok", False)]
      and saved[0]["model"] == "some-gemini" and saved[1]["label"] == "OpenAI"
      and not any(k in json.dumps(saved) for k in ("in-use", "good", "from-the-terminal")))

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
app = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=app.serve_forever, daemon=True).start()


def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


SEEN.clear()
code, got = post("/api/providers/models", {"id": "openai"})
check("a saved provider's models are read with its saved key",
      code == 200 and got == {"models": ["newest-chat", "older-chat"]} and SEEN[-1].get("Authorization") == "Bearer good")
check("a provider with no key saved is not asked", post("/api/providers/models", {"id": "anthropic"})[0] == 404)
if far:
    code, got = post("/api/llm-endpoint", {"provider": "openai", "url": base, "key": "", "model": "", "settle": True})
    after = web._load_settings()
    check("switching with no key given uses the saved one, and settles on a model the provider lists",
          code == 200 and got.get("model") == "newest-chat" and after["llm_key"] == "good"
          and after["llm_model"] == "newest-chat" and after["provider_keys"].get("gemini") == "in-use")
    far.shutdown()
app.shutdown()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all provider model checks passed")
