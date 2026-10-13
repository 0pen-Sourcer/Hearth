"""A model's details for the model menu's card, from its own source first.

Nothing told what a model takes in, whether it reasons, or how much context it
holds. The card asks the source that offers the model, then models.dev (the
open catalog OpenCode reads) for what the source leaves out.

Uses a throwaway home folder with a catalog written here, so nothing is fetched.

    .\\.venv\\Scripts\\python.exe tests\\test_model_facts.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="hearth-facts-")
os.environ["USERPROFILE"] = os.environ["HOME"] = HOME
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

Path(HOME, ".hearth").mkdir()
Path(HOME, ".hearth", "models_dev.json").write_text(json.dumps({
    "google": {"models": {"gemini-9-flash": {"name": "Gemini 9 Flash", "reasoning": True,
                                              "modalities": {"input": ["text", "image", "audio"]},
                                              "limit": {"context": 1048576}}}},
    "acme": {"models": {"Big-Model": {"name": "Big Model", "reasoning": False,
                                      "modalities": {"input": ["text"]}, "limit": {"context": 200000},
                                      "cost": {"input": 0, "output": 0}}}},
}), encoding="utf-8")

from hearth import model_facts as F, headless                       # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


g = F.from_catalog("gemini", "models/gemini-9-flash")
check(f"Hearth's provider names are matched to the catalog's {g}",
      g.get("inputs") == ["text", "image", "audio"] and g.get("reasoning") is True and g.get("ctx") == 1048576)
b = F.from_catalog("openrouter", "acme/big-model")
check(f"a vendor/model name is found under the vendor, any case {b}",
      b.get("ctx") == 200000 and b.get("reasoning") is False and b.get("free") is True)
check("a model the catalog doesn't have gives nothing, not a guess", F.from_catalog("acme", "unknown-7b") == {})
m = F.merge({"ctx": 128000, "inputs": [], "where": "Acme"}, "acme", "big-model")
check(f"what the source itself says wins over the catalog; what it leaves out is filled {m}",
      m["ctx"] == 128000 and m["inputs"] == ["text"] and m["where"] == "Acme")

headless._LIST_FACTS["https://openrouter.ai/api/v1"] = {"x/y": {"inputs": ["text", "image"], "reasoning": True}}
headless._LIST_SIZES["https://openrouter.ai/api/v1"] = (0, {"x/y": 64000})
check("a provider's own list is read for its models",
      headless.listed_facts("https://openrouter.ai/api/v1/", "x/y") == {"inputs": ["text", "image"], "reasoning": True, "ctx": 64000})

# The think menu offered Off/On/Low/Medium/High to every cloud model; Anthropic's
# own list says which effort levels a model takes and whether thinking turns off.
import http.server, threading                                         # noqa: E401,E402
from http.server import ThreadingHTTPServer                           # noqa: E402


class _List(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "claude-x", "max_input_tokens": 1000000, "capabilities": {
            "effort": {"supported": True, "low": {"supported": True}, "high": {"supported": True},
                       "max": {"supported": True}, "medium": {"supported": False}},
            "thinking": {"supported": True, "types": {"adaptive": {"supported": True},
                                                      "disabled": {"supported": False}}}}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), _List)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
headless.provider_models(base, "k")
lf = headless.listed_facts(base, "claude-x")
check(f"a provider's effort levels and whether thinking turns off are read from its list {lf}",
      lf.get("efforts") == ["low", "high", "max"] and lf.get("can_off") is False)
check("off sends nothing to a model that can't turn thinking off, and a listed level is sent as asked",
      headless._cloud_reasoning_effort(base, False, "claude-x") is None
      and headless._cloud_reasoning_effort(base, True, "claude-x", "max") == "max"
      and headless._cloud_reasoning_effort(base, True, "claude-x", "medium") is None)
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all model facts checks passed")
