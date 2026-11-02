"""provider_keys in settings.json that isn't a dict once opened (JSON text, a
sealed value that opens to a string, one that can't be opened, a stray string)
reads as a dict, so the window's saved-providers list and the brain switch
keep working instead of failing on every request.

    .\\.venv\\Scripts\\python.exe tests\\test_provider_keys_shape.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile

home = tempfile.mkdtemp(prefix="hearth-pk-home-")
work = os.path.join(home, "Jarvis")
os.makedirs(os.path.join(home, ".hearth"))
os.makedirs(work)
os.environ.update(HOME=home, USERPROFILE=home, JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1", HEARTH_NO_ONBOARDING="1",
                  HEARTH_SECRETS_DIR=os.path.join(home, "secret-dir"))
for k in ("LOCAL_API_KEY", "OPENAI_API_KEY", "LOCAL_API_BASE", "HEARTH_KEYSTORE"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


XAI = "xai-STANDIN000000000000000000000000"
settings = os.path.join(work, "settings.json")

from hearth import keystore  # noqa: E402
from hearth import web  # noqa: E402

web.SETTINGS_PATH = settings

CASES = [
    ("JSON text of the keys", json.dumps({"grok": XAI}), {"grok": XAI}),
    ("a sealed value that opens to JSON text", keystore.seal(json.dumps({"grok": XAI}), "pk-test"), {"grok": XAI}),
    ("a sealed value that can't be opened", "sealed:dpapi:AAAA", {}),
    ("a stray string", "not keys at all", {}),
    ("a list", ["x"], {}),
]

for label, stored, want in CASES:
    with open(settings, "w", encoding="utf-8") as f:
        json.dump({"llm_provider": "local", "provider_keys": stored}, f)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        got = keystore.read_settings(settings).get("provider_keys")
        try:
            listed = [p["id"] for p in web._saved_providers()]
            key = web._provider_key("grok")
            raised = ""
        except Exception as e:
            listed, key, raised = [], "", f"{type(e).__name__}"
    check(f"{label}: read as a dict", isinstance(got, dict) and got == want)
    check(f"{label}: the saved-providers list doesn't fail{(' (' + raised + ')') if raised else ''}", not raised)
    check(f"{label}: grok's key found only when there is one", (key == XAI) == bool(want))
    check(f"{label}: grok listed only when it has a key", ("grok" in listed) == bool(want))
    check(f"{label}: no key value in the log", XAI not in err.getvalue())
    if not want:
        check(f"{label}: a line in the log says it was read as none", "provider_keys" in err.getvalue())

if FAILS:
    print(f"\n{len(FAILS)} failed")
    sys.exit(1)
print("\nall passed")
