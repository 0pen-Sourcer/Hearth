"""Images and videos are made with whatever key is saved, on any brain.

Making an image needed the brain in use to be Grok, OpenAI or Gemini; on a
local model or an agent brain it said to switch brains even with a Grok key
saved. A video was polled with the key of the brain in use at the time of the
poll, and nobody heard when it finished unless they asked.

    .\\.venv\\Scripts\\python.exe tests\\test_media_route.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-media-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["LOCAL_API_BASE"] = "http://127.0.0.1:9/v1"   # a local model
os.environ["LOCAL_API_KEY"] = "local-not-a-key"
os.environ.pop("OPENAI_API_KEY", None)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "llm_provider": "builtin",
               "provider_keys": {"grok": "xai-saved-key"}}, f)
sys.path.insert(0, ROOT)

from hearth import imagine, subagents, tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


sent = []
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode()


def fake_post(url, headers, body, timeout=0):
    sent.append((url, headers.get("Authorization"), body))
    if url.endswith("/videos/generations"):
        return {"request_id": "vid-1"}
    return {"data": [{"b64_json": PNG}]}


imagine._post_json = fake_post
out = tools._generate_image({"prompt": "a lighthouse at dusk"})
check(f"on a local model an image goes to the saved Grok key ({out.splitlines()[0][:70]})",
      bool(sent) and sent[0][0].startswith("https://api.x.ai/v1/") and sent[0][1] == "Bearer xai-saved-key")
check("and is shown in the chat", "__JARVIS_IMAGE__" in out)

sent.clear()
v = imagine.start_video("waves rolling in")
check(f"a video starts on the saved Grok key too ({v.get('ok')}, {v.get('error', '')[:60]})",
      v.get("ok") and bool(sent) and sent[0][1] == "Bearer xai-saved-key")

polled = []
imagine._get_json = lambda url, headers, timeout=0: (polled.append(headers.get("Authorization")) or {"status": "pending"})
os.environ["LOCAL_API_KEY"] = "some-other-brain-key"
imagine.check_video_task(v.get("task_id") or "")
check(f"it is polled with the key of the provider that took it ({polled})",
      polled == ["Bearer xai-saved-key"])

subagents.drain_pending_notifications()
real = imagine.check_video_task
imagine.check_video_task = lambda tid: {"ok": True, "status": "done", "path": "x.mp4"}
tools._watch_video("vid-1", "waves rolling in", every=0.05, limit=5)
end = time.time() + 3
got = []
while time.time() < end and not got:
    got = [n for n in subagents.peek_pending_notifications() if n.get("persona") == "video"]
    time.sleep(0.05)
imagine.check_video_task = real
check("a finished video reports back on its own", bool(got) and got[0].get("status") == "completed")
if got:
    text = subagents.format_notification_as_user_message(got[0])
    check("and the report reads as a video, not a helper", "A video you started" in text)

with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "llm_provider": "builtin"}, f)
none = imagine.generate_image("a lighthouse")
check(f"with no key saved it says where to add one ({none.get('error', '')[:60]})",
      not none.get("ok") and "Settings" in none.get("error", "") and "/brain" not in none.get("error", ""))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all media route checks passed")
