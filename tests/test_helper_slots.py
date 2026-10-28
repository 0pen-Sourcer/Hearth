"""Many helpers, one loaded model.

Asking for a helper model that is already the main brain started a second
llama-server on the same file: two copies of the weights. Helpers on one server
were left for the server to place, so one could land on the chat's slot and
push the chat's cache out. And with --parallel set, llama.cpp keeps a fixed KV
share per slot, held in VRAM even while that helper sits idle.

Now: the same file is never loaded twice (helpers use the main server); the
chat keeps slot 0 and each helper a slot of its own (id_slot); with more than
one slot the server gets --kv-unified when its build lists it, so an idle
slot's cache goes to the RAM prompt cache and its room to whoever is working.

Runs a stand-in llama-server (tests/_standin_llama_server.py) that records the
flags and requests it gets.

    .\\.venv\\Scripts\\python.exe tests\\test_helper_slots.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
WORK = tempfile.mkdtemp(prefix="hearth-slots-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WORK
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
LOG = os.path.join(WORK, "standin.jsonl")
os.environ["STANDIN_LOG"] = LOG

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def records():
    try:
        with open(LOG, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


# A llama-server stand-in that runs as a program, as the real one does.
script = os.path.join(ROOT, "tests", "_standin_llama_server.py")
if os.name == "nt":
    EXE = os.path.join(WORK, "llama-server.cmd")
    with open(EXE, "w") as f:
        f.write(f'@"{sys.executable}" "{script}" %*\n')
else:
    EXE = os.path.join(WORK, "llama-server")
    with open(EXE, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    os.chmod(EXE, 0o755)

from hearth import kv_slots, llmserver as L, subagents as S       # noqa: E402

L.engine_for = lambda _p: {"exe": EXE, "label": "standin"}
L.find_native_llama_server = lambda: {"exe": EXE, "label": "standin"}
MODEL = os.path.join(WORK, "helper.gguf")
OTHER = os.path.join(WORK, "main.gguf")
for p in (MODEL, OTHER):
    with open(p, "wb") as f:
        f.write(b"GGUF")

# ---------------------------------------------------------------- which flags a build gets
os.environ["STANDIN_FLAGS"] = "--port --parallel --ctx-size --kv-unified --no-kv-unified --cache-ram"
check("the options a build lists are read from its --help",
      {"--kv-unified", "--cache-ram"} <= L.engine_flags(EXE) and "--slot-save-path" not in L.engine_flags(EXE))
check("one slot gets no shared KV", L._shared_kv_args(EXE, 1, []) == [])
check("several slots share one KV buffer", L._shared_kv_args(EXE, 3, []) == ["--kv-unified"])
check("a choice in the Advanced flags is left alone", L._shared_kv_args(EXE, 3, ["--no-kv-unified"]) == [])
os.environ["STANDIN_FLAGS"] = "--port --parallel --ctx-size"
L._FLAGS.clear()
check("a build from before --kv-unified isn't given it, so it still starts", L._shared_kv_args(EXE, 3, []) == [])
os.environ["STANDIN_FLAGS"] = "--port --parallel --ctx-size --kv-unified --no-kv-unified --cache-ram"
L._FLAGS.clear()

# ---------------------------------------------------------------- a helper server of its own
L.HELPER_PORT = free_port()
r = L.start_helper(MODEL, ctx=4096, slots=2, wait_s=30)
argv = next((x["argv"] for x in records() if "argv" in x), [])
check(f"a helper model starts with two slots and one shared KV buffer {r.get('error') or ''}",
      r.get("ok") and argv[argv.index("--parallel") + 1] == "2" and "--kv-unified" in argv
      and argv[argv.index("--ctx-size") + 1] == "8192")

os.environ["STANDIN_DELAY"] = "0.6"
seen: list = []


async def two_helpers():
    def persona(i):
        return {"name": f"h{i}", "slug": f"h{i}", "allowed_tools": ["read_file"], "cost_class": "standard",
                "_route": (L.helper_base(), L.BUILTIN_API_KEY, "standin")}
    return await asyncio.gather(*(S._run_subagent_async(persona(i), "say done", 1, f"slot-test-{i}") for i in (1, 2)))

# The stand-in already started with STANDIN_DELAY unset; restart it so the two requests overlap.
L.stop_helper()
open(LOG, "w").close()
L.start_helper(MODEL, ctx=4096, slots=2, wait_s=30)
outs = asyncio.run(two_helpers())
asks = [x["body"] for x in records() if x.get("path", "").endswith("/chat/completions")]
check(f"both helpers answered {[o.get('ok') for o in outs]}", all(o.get("ok") for o in outs))
check(f"two helpers working at once each keep a slot of their own {[a.get('id_slot') for a in asks]}",
      sorted(a.get("id_slot") for a in asks) == [0, 1])
check("a finished helper lets its slot go", kv_slots.held().get(L.HELPER_PORT, {}) == {})
L.stop_helper()

# ---------------------------------------------------------------- the same file twice
MAIN_PORT = free_port()
open(LOG, "w").close()
main = subprocess.Popen([EXE, "--port", str(MAIN_PORT), "--parallel", "3"])
url = f"http://127.0.0.1:{MAIN_PORT}/v1"
for _ in range(60):
    if L.external_server_running(url, timeout=0.3):
        break
    time.sleep(0.2)
L._proc = main
L._proc_info = {"url": url, "model_path": OTHER, "slots": 3, "engine": "native", "port": MAIN_PORT}
started = len([x for x in records() if "argv" in x])
r = L.start_helper(OTHER, ctx=4096, slots=2, wait_s=10)
check(f"asking for a helper model that is already the main brain loads no second copy {r}",
      r.get("ok") and r.get("shared") and "2 at once" in r.get("note", "") and len([x for x in records() if "argv" in x]) == started
      and not L.helper_status()["running"])
os.environ["LOCAL_API_BASE"] = url
base, _key, _m = S._route_for_cost_class("standard")
check("helpers then go to the main server", base == url)

# ---------------------------------------------------------------- slots on the main server
check("the chat keeps slot 0", kv_slots.pin(url, kv_slots.MAIN) == {"id_slot": 0})
a, b, c = kv_slots.pin(url, "a"), kv_slots.pin(url, "b"), kv_slots.pin(url, "c")
check(f"helpers get the other slots, and one too many is left to the server {a, b, c}",
      {a.get("id_slot"), b.get("id_slot")} == {1, 2} and c == {})
check("asking again keeps the same slot", kv_slots.pin(url, "a") == a)
kv_slots.release("a"); kv_slots.release("b")
check("a helper picked up again gets its own slot back", kv_slots.pin(url, "b") == b)
kv_slots.release("b")
check("nothing is pinned on a server that isn't Hearth's own",
      kv_slots.pin("http://127.0.0.1:1/v1", "x") == {} and kv_slots.pin("https://api.openai.com/v1", kv_slots.MAIN) == {})
L._proc_info = dict(L._proc_info, slots=1)
check("nothing is pinned with one slot", kv_slots.pin(url, kv_slots.MAIN) == {})

main.terminate(); main.wait(timeout=5)
L._proc, L._proc_info = None, {}
print("\n" + ("all helper slot checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
