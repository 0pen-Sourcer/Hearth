"""Inglen's own engine comes first and has its own port: it never meets LM Studio on
1234, and LM Studio, Ollama and llama.cpp servers are still found when they run.

Owner, 2026-10-29 (C23): "don't rely on LM Studio anymore, that was our cheap move when
Inglen wasn't a product".

    .\\.venv\\Scripts\\python.exe tests\\test_engine_port.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from hearth import headless, llmserver, tools, web
desc = next(t["description"] for t in tools.TOOL_DEFINITIONS if t["name"] == "list_models")
print(json.dumps({"builtin": llmserver.BUILTIN_PORT, "helper": llmserver.HELPER_PORT, "headless": headless.LOCAL_API_BASE,
                  "web": web.LOCAL_API_BASE, "known": llmserver.KNOWN_LOCAL_SERVERS, "env": os.environ.get("HEARTH_BUILTIN_PORT") or "",
                  "list_models": desc}))
"""


def probe(settings=None, **env):
    home = tempfile.mkdtemp(prefix="hearth-engine-port-")
    ws = os.path.join(home, "Inglen")
    os.makedirs(ws)
    if settings:
        with open(os.path.join(ws, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f)
    e = {k: v for k, v in os.environ.items() if not k.startswith(("HEARTH_", "JARVIS_", "LOCAL_API"))}
    e.update(HOME=home, USERPROFILE=home, HEARTH_WORKSPACE=ws, JARVIS_WORKSPACE=ws, HEARTH_NO_ACTIVITY_LOG="1",
             HEARTH_NO_BRIDGES="1", **env)
    out = subprocess.run([sys.executable, "-c", PROBE, ROOT], env=e, capture_output=True, text=True, timeout=180)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(out.stderr[-1500:])
        return {}


got = probe()
check(f"the built-in engine has a port of its own, not LM Studio's 1234 ({got.get('builtin')}, helper {got.get('helper')})",
      got.get("builtin") not in (None, 1234, 11434, 8080) and got.get("helper") == got.get("builtin", 0) + 1)
want = f"http://localhost:{got.get('builtin')}/v1"
check(f"a fresh install talks to Inglen's own engine first ({got.get('headless')}, {got.get('web')})",
      got.get("headless") == want and got.get("web") == want)
known = dict(got.get("known") or [])
check(f"LM Studio, Ollama and llama.cpp are still found ({sorted(known)})",
      known.get("LM Studio") == "http://localhost:1234/v1" and "Ollama" in known and "llama.cpp" in known)
lm = got.get("list_models") or ""
check(f"list_models names Inglen's engine first, not LM Studio ({lm[:90]!r})",
      lm and "built-in engine" in lm and lm.find("built-in") < (lm.find("LM Studio") if "LM Studio" in lm else 10**6))

got = probe(settings={"server_port": 9123})
check(f"Settings' port is the one it starts on ({got.get('builtin')}, {got.get('env')})", got.get("builtin") == 9123)
got = probe(settings={"server_port": 1234})
check(f"a 1234 saved before the engine had its own port reads as the new default ({got.get('builtin')})",
      got.get("builtin") not in (None, 1234))
got = probe(settings={"server_port": 9123}, HEARTH_BUILTIN_PORT="9555")
check(f"BUILTIN_PORT set before start wins ({got.get('builtin')})", got.get("builtin") == 9555)

src = open(os.path.join(ROOT, "hearth", "tools.py"), encoding="utf-8").read()
check("tool texts don't send people to LM Studio", "is one loaded in LM Studio" not in src and "Is LM Studio (or your endpoint)" not in src)

print("\n" + ("all engine port checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
