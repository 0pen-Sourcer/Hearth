"""Chats get a real title whatever the brain, in the window and the terminal.

With Anthropic as the brain the title call went to its OpenAI-compatible
endpoint with `temperature` and a local-only field; Anthropic's newer models
refuse those, so every chat kept its first words as a title. The terminal
never asked for one at all. A stand-in for Anthropic's SDK answers here.

    .\\.venv\\Scripts\\python.exe tests\\test_chat_titles.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-titles-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import anthropic_chat, chat_sync, headless              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


asked = []


class FakeChat:
    def __init__(self, api_key=None, **_):
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kw):
        asked.append(kw)
        return NS(choices=[NS(message=NS(content='"Planning a Goa Trip"'))])


anthropic_chat.AsyncAnthropicChat = FakeChat
title = chat_sync.title_for("help me plan a trip to Goa in December",
                            lambda p: headless.one_shot(p, "claude-x", base="https://api.anthropic.com/v1/", key="k"))
check(f"Anthropic as the brain names the chat through its own API {title!r}",
      title == "Planning a Goa Trip" and asked and asked[0].get("hearth_think") is False)
check("nothing answering keeps the opening's first words",
      chat_sync.title_for("help me plan a trip to Goa in December please", lambda p: "")
      == "help me plan a trip to…")

# A bare "Yo!" was named "Greeting only, no task yet".
asked_for = []
title = chat_sync.title_for("Yo!\n\nAssistant: Yo! What do you need?", lambda p: asked_for.append(p) or "NONE")
check(f"a chat that is only a greeting gets no title yet, so the next message names it {title!r}",
      title == "" and "NONE" in asked_for[0] and "Ignore greetings" not in asked_for[0])

chat_sync.post("c1", "help me plan a trip", "terminal", [{"role": "user", "content": "help me plan a trip"}])
d = chat_sync.load("c1")
d["title"] = "help me plan a trip"
chat_sync.atomic_write_json(chat_sync._path("c1"), d)
chat_sync.retitle("c1", "Trip Planning")
check("a chat still under its first words gets the real title", chat_sync.load("c1")["title"] == "Trip Planning")
chat_sync.retitle("c1", "Something Else")
check("one renamed since keeps its name", chat_sync.load("c1")["title"] == "Trip Planning")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat title checks passed")
