"""The Engine tab's build lists: kept answers from GitHub, and the image engine beside the chat one.

GitHub allows 60 unsigned API requests an hour per network, and every app on the
network shares them; the build list asked each time the tab opened and said it
couldn't reach GitHub once the limit was hit. Now the answer is kept, asked
again with its ETag, and a kept one is shown when GitHub says no.

    .\\.venv\\Scripts\\python.exe tests\\test_engine_builds.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-builds-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.path.join(HOME, "Jarvis")

from hearth import llmserver as L, sdserver as S  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Resp(io.BytesIO):
    def __init__(self, data, etag="W/1"):
        super().__init__(json.dumps(data).encode())
        self.headers = {"ETag": etag}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


calls = []
mode = {"now": "ok"}
REL = [{"tag_name": "master-990-abc", "published_at": "2026-10-30T00:00:00Z", "html_url": "u990",
        "assets": [{"name": "sd-master-990-abc-bin-win-cuda12-x64.zip", "size": 300_000_000},
                   {"name": "cudart-sd-bin-win-cu12-x64.zip", "size": 400_000_000},
                   {"name": "sd-master-990-abc-bin-win-vulkan-x64.zip", "size": 50_000_000}]},
       {"tag_name": "master-987-a77d362", "published_at": "2026-10-20T00:00:00Z", "html_url": "u987",
        "assets": [{"name": "sd-master-987-a77d362-bin-win-cuda12-x64.zip", "size": 300_000_000},
                   {"name": "cudart-sd-bin-win-cu12-x64.zip", "size": 400_000_000}]}]


def fake_urlopen(req, timeout=0):
    calls.append(dict(req.headers))
    if mode["now"] == "limited":
        raise urllib.error.HTTPError(req.full_url, 403, "rate limit exceeded", {}, None)
    if mode["now"] == "same" and req.headers.get("If-none-match"):
        raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
    return Resp(REL)


import urllib.request  # noqa: E402
urllib.request.urlopen = fake_urlopen

rels, got = L.github_releases("leejet/stable-diffusion.cpp", 10)
check("the first answer comes from GitHub", len(rels) == 2 and not got["stale"] and len(calls) == 1)
rels, got = L.github_releases("leejet/stable-diffusion.cpp", 10)
check("the next look within hours asks nothing", len(rels) == 2 and len(calls) == 1)
rels, got = L.github_releases("leejet/stable-diffusion.cpp", 10, ttl=0)
check("past its time it asks with the ETag", calls[-1].get("If-none-match") == "W/1")
mode["now"] = "same"
rels, got = L.github_releases("leejet/stable-diffusion.cpp", 10, ttl=0)
check("an unchanged answer (304) keeps the kept one", len(rels) == 2 and not got["stale"])
mode["now"] = "limited"
rels, got = L.github_releases("leejet/stable-diffusion.cpp", 10, ttl=0)
check(f"when GitHub's hourly limit is hit the kept list is shown, marked old ({got})",
      len(rels) == 2 and got["stale"] and got["limited"])
rels, got = L.github_releases("ggml-org/llama.cpp", 4)
check("with nothing kept, it says it was the limit, not the network", rels == [] and got["limited"])
out = L.list_llama_builds()
check("the chat engine's list says so too", out["builds"] == [] and out["limited"] and not out["ok"])

# The image engine, as the Engine tab shows it.
mode["now"] = "ok"
S._backend = lambda: "cuda"
S.running = lambda: False
for tag in ("master-987-a77d362", "master-990-abc"):
    d = S.builds_dir() / tag
    d.mkdir(parents=True, exist_ok=True)
    (d / S.SERVER).write_bytes(b"MZ" + b"\0" * 2_000_000)
    time.sleep(0.05)
L._usable_server_exe = lambda p: os.path.isfile(p)
L.engine_build_of = lambda p: "cuda"
b = S.list_builds()
check(f"each recent build for this card, with its download size ({[(x['tag'], x['size_mb']) for x in b['builds']]})",
      [x["tag"] for x in b["builds"]] == ["master-990-abc", "master-987-a77d362"] and b["builds"][0]["size_mb"] == 700)
check("the newest installed one is in use", b["current"]["tag"] == "master-990-abc" and b["builds"][0]["current"])
S.running = lambda: False
check("an older build can be picked", S.use_build("master-987-a77d362")["ok"]
      and S.installed()[0].parent.name == "master-987-a77d362" and S.list_builds()["current"]["pinned"])
check("and one that isn't installed can't", not S.use_build("master-1-x")["ok"])
S.use_build("")
check("back to the newest", S.installed()[0].parent.name == "master-990-abc")

# Discover's image rows: a plain line of what each does, never the engine's notes.
check("a family says what it does in plain words",
      S.blurb({"id": "wan22-i2v", "defaults": {"video_frames": 33}}) == "Brings a picture to life as a short video"
      and S.blurb({"id": "qwen-image-edit-2511"}) == "Edits pictures you give it"
      and S.blurb({"id": "z-image-turbo"}) == "Makes pictures from text, in a few steps")
with open(S._SETS_PATH, "w", encoding="utf-8") as f:
    json.dump({"at": time.time(), "ids": ["z"], "sets": [
        {"id": "z", "name": "Z", "video": False, "blurb": "b", "url": "", "ok": True, "repo": "r", "downloads": 9,
         "parts": [{"slot": "vae", "kind": "VAE", "label": "VAE", "repo": "r", "path": "split_files/vae/ae.safetensors",
                    "category": "vae", "name": "ae.safetensors", "size": 300},
                   {"slot": "llm", "kind": "Text encoder", "label": "T", "repo": "r", "path": "p",
                    "category": "text_encoders", "name": "t.safetensors", "size": 700}]}]}, f)
mine = tempfile.mkdtemp()
os.makedirs(os.path.join(mine, "vae"))
open(os.path.join(mine, "vae", "ae.safetensors"), "w").close()
c = S.catalog_sets(roots=[mine])
z = c["sets"][0]
check("kept sets are served at once, with what's on this PC marked",
      c["ready"] and [p["have"] for p in z["parts"]] == [True, False] and z["bytes"] == 700)

print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
