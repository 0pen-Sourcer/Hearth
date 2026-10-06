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
        # Only the one list a provider has; LM Studio's own address is not here.
        if self.headers.get("Authorization") != "Bearer good" or not self.path.split("?")[0].endswith("/v1/models"):
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({"data": [{"id": "big-embedding-3"}, {"id": "newest-chat", "max_input_tokens": 1000000}, {"id": "older-chat", "context_length": 262144},
                                    {"id": "imagen-4"}, {"id": "vendor/slashed-chat"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/v1"

check("the chat models a provider lists, in its order, without the ones that can't chat",
      hl.provider_models(base, "good") == ["newest-chat", "older-chat", "vendor/slashed-chat"])
check("a provider that refuses the key has no list", hl.provider_models(base, "bad") is None)
check("a name the provider lists is kept, whatever its case", hl.settle_model(base, "good", "Older-Chat") == "older-chat")
check("a name it no longer lists gives way to one it does", hl.settle_model(base, "good", "retired-model") == "newest-chat")
check("no name at all gets the first it lists", hl.settle_model(base, "good", "") == "newest-chat")
check("when the list can't be had, the name asked for stands", hl.settle_model(base, "bad", "retired-model") == "retired-model")
check("a name given without its prefix is the listed one", hl.settle_model(base, "good", "slashed-chat") == "vendor/slashed-chat")
# The context size came from a table written into Hearth, which called every
# Claude 200K; the provider's own list says what each model takes.
check("a model's context size is the one its provider lists",
      hl.listed_context(base, "good", "newest-chat") == 1000000 and hl.listed_context(base, "good", "older-chat") == 262144
      and hl.listed_context(base, "good", "unknown-model") is None)
check("a stale guess gives way to its nearest relative, not the first model listed",
      hl.settle_model(base, "good", "vendor/slashed-chat-3.5") == "vendor/slashed-chat")

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
      code == 200 and got == {"models": ["newest-chat", "older-chat", "vendor/slashed-chat"]}
      and SEEN[-1].get("Authorization") == "Bearer good")
check("a provider with no key saved is not asked", post("/api/providers/models", {"id": "anthropic"})[0] == 404)
if far:
    code, got = post("/api/llm-endpoint", {"provider": "openai", "url": base, "key": "", "model": "", "settle": True})
    after = web._load_settings()
    check("switching with no key given uses the saved one, and settles on a model the provider lists",
          code == 200 and got.get("model") == "newest-chat" and after["llm_key"] == "good"
          and after["llm_model"] == "newest-chat" and after["provider_keys"].get("gemini") == "in-use")
    # Moving to a local server banked the provider's key under "local", and the
    # next move banked whatever llm_key held under the next name.
    post("/api/llm-endpoint", {"provider": "local", "url": "http://localhost:1234/v1", "key": ""})
    web._save_settings({"llm_key": "left-over-cloud-key"})          # as an older switch left it
    post("/api/llm-endpoint", {"provider": "openai", "url": base, "key": "", "model": "newest-chat"})
    check("a key is never filed under a local server's name", "local" not in web._load_settings()["provider_keys"])

    # Picking a model on this PC while a provider was the brain saved the file's
    # name as that provider's model: nothing loaded, and every message would
    # have asked the provider for a .gguf.
    web._save_settings({"llm_model": "Local-9B-Q4_K_M.gguf"})
    check("a local file's name is not saved as a provider's model", web._load_settings()["llm_model"] == "")
    left = dict(web._load_settings(), llm_model="Local-9B-Q4_K_M.gguf")      # what that left on disk
    web._atomic_write_json(web.SETTINGS_PATH, left, indent=2)
    web._models_cache["ts"] = 0
    listed = web._list_models()
    check("a provider left with one takes the first model it lists, and shows that one in use",
          web._load_settings()["llm_model"] == "newest-chat"
          and [m["id"] for m in listed if m["state"] == "loaded"] == ["newest-chat"])
    check("a provider's model ids are kept whole", "vendor/slashed-chat" in [m["id"] for m in listed])
    web._save_settings({"llm_model": "slashed-chat"})
    web._models_cache["ts"] = 0
    check("a model saved by its short name is still the one marked in use",
          [m["id"] for m in web._list_models() if m["state"] == "loaded"] == ["vendor/slashed-chat"])
    # A list worked out while the brain was being switched finished after the
    # switch and was served as the new brain's for a few seconds.
    import time                                                      # noqa: E402
    web._models_cache.update(ts=time.time(), base="https://the-brain-before.invalid/v1",
                             data=[{"id": "the-brain-before", "state": "loaded"}])
    check("a model list read from another endpoint is not served as this one's",
          "the-brain-before" not in [m["id"] for m in web._list_models()])
    far.shutdown()
app.shutdown()
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all provider model checks passed")
