"""One set of saved keys: a key replaced in the window is the one the terminal
and the window's model list use, even with an older one in brain_keys.json, and
a key saved with /brain reaches the window's store.

    .\\.venv\\Scripts\\python.exe tests\\test_one_keystore.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import json
import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-keys-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1",
                  HEARTH_NO_ONBOARDING="1")
for k in ("LOCAL_API_KEY", "OPENAI_API_KEY", "LOCAL_API_BASE"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def write(name, d):
    with open(os.path.join(work, name), "w", encoding="utf-8") as f:
        json.dump(d, f)


write("settings.json", {"llm_provider": "anthropic", "llm_url": "https://api.anthropic.com/v1/",
                        "llm_key": "sk-ant-NEW", "provider_keys": {"anthropic": "sk-ant-NEW", "gemini": "g-1"},
                        "onboarded": True})
write("brain_keys.json", {"anthropic": {"url": "https://api.anthropic.com/v1/", "key": "sk-ant-OLD"},
                          "grok": {"key": "x-1"}})

import hearth_cli  # noqa: E402
check(f"the terminal uses the key saved in the window ({hearth_cli._read_brain_key('anthropic')})",
      hearth_cli._read_brain_key("anthropic") == "sk-ant-NEW")
check("a key only the terminal ever saved still works", hearth_cli._read_brain_key("grok") == "x-1")

from hearth import web  # noqa: E402
sent = []
web._http_get_json = lambda url, timeout=3, headers=None: sent.append(dict(headers or {})) or None
os.environ["LOCAL_API_KEY"] = "sk-ant-OLD"            # left over in the environment
web._models_cache.update(ts=0, data=None)
try:
    web._list_models() if hasattr(web, "_list_models") else web.list_models()
except Exception:
    pass
used = [h.get("x-api-key") or h.get("Authorization", "") for h in sent]
check(f"the window's model list asks with the saved brain's key, not one left in the environment ({used[:2]})",
      used and all("NEW" in u for u in used))

# /brain saving a key writes the window's store too (forget removes it there).
cli = hearth_cli.JarvisCLI()
if cli is not None:
    import asyncio
    try:
        asyncio.run(cli.handle_command("/brain gemini AIzaSyTESTTESTTESTTESTTESTTESTTESTTE12"))
    except Exception:
        pass
    from hearth import keystore
    vault = keystore.read_settings(os.path.join(work, "settings.json")).get("provider_keys") or {}
    check(f"/brain with a new key replaces it in the window's store ({vault.get('gemini')})", vault.get("gemini") == "AIzaSyTESTTESTTESTTESTTESTTESTTESTTE12")

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall key store checks passed")
