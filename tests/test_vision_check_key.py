"""Asking Hearth's own server whether it can see uses Hearth's key first.

The check tried the configured key before the built-in one, so every screenshot
left "unauthorized: Invalid API Key" in the built-in server's log. Stand-in
server replies.

    .\\.venv\\Scripts\\python.exe tests\\test_vision_check_key.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error

work = tempfile.mkdtemp(prefix="hearth-vkey-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1",
                  LOCAL_API_BASE="http://127.0.0.1:1234/v1", LOCAL_API_KEY="sk-some-cloud-key", LOCAL_MODEL="")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import llmserver, tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


sent = []


def fake_urlopen(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    auth = req.headers.get("Authorization", "") if hasattr(req, "headers") else ""
    sent.append((url, auth))
    if url.endswith("/props"):
        if auth != "Bearer hearth-builtin":
            raise urllib.error.HTTPError(url, 401, "Invalid API Key", {}, None)
        return _Resp(json.dumps({"modalities": {"vision": True}}).encode())
    raise urllib.error.URLError("no")


import urllib.request  # noqa: E402
urllib.request.urlopen = fake_urlopen

llmserver.ours_at = lambda url: {"url": "http://127.0.0.1:1234/v1"}
sees = tools._loaded_model_is_vision()
props = [a for u, a in sent if u.endswith("/props")]
check(f"Hearth's own server is asked once, with its own key ({props})",
      sees is True and props == ["Bearer hearth-builtin"])

sent.clear()
llmserver.ours_at = lambda url: {}
tools._loaded_model_is_vision()
props = [a for u, a in sent if u.endswith("/props")]
check(f"another server still gets the configured key first ({props})",
      props[:1] == ["Bearer sk-some-cloud-key"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all vision key checks passed")
