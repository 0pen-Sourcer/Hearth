"""The persona the model reads on every turn.

It grew by accretion from the first versions onward: a rule for each thing that
went wrong once, a manual for tools whose own descriptions already said the
same, a list of all 106 tool names in the prompt, one owner's register baked
into a shipped product, and claims that had gone false (a turn cap of 8 when it
is 40, "you have no close-app tool" when manage_window closes apps). It reached
about twelve thousand tokens, which with the tool schemas used 64% of a 32K
window before anyone typed.

This holds the shipped part to a size and to the facts. The user's own layers
(profile, memories, house rules) are theirs and are not measured here.

    .\\.venv\\Scripts\\python.exe tests\\test_persona.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import persona as P                                    # noqa: E402
from hearth import tools as T                                      # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# Render the shipped part alone: no profile, memories, rules or skills.
for fn in ("read_rules", "read_profile", "frozen_index_for_prompt",
           "skills_for_prompt"):
    setattr(P, fn, lambda *a, **k: "")
os.environ.pop("HEARTH_PERSONA", None)
BASE = P.system_prompt()
VOICE = P.system_prompt(voice=True)

# ----------------------------------------------------- it knows what it runs on
# On a cloud model it told its user "I'm running locally on your machine, no
# cloud for me": the prompt said nothing was sent anywhere, whatever the brain.
from hearth import claude_brain                                    # noqa: E402
claude_brain.current = lambda: None                                 # no agent program is the brain here
os.environ["LOCAL_API_BASE"] = "https://generativelanguage.googleapis.com/v1beta/openai/"
os.environ["LOCAL_MODEL"] = "gemini-2.5-flash"
cloud = P.system_prompt()
check("on a cloud provider it is told which one, which model, and that the conversation goes there",
      "gemini-2.5-flash at Google Gemini" in cloud and "sent there" in cloud and "not a local model" in cloud
      and "nothing sent anywhere" not in cloud)
os.environ["LOCAL_API_BASE"] = "http://localhost:1234/v1"
os.environ["LOCAL_MODEL"] = r"F:\Models\Some-9B-Q4_K_M.gguf"
here = P.system_prompt()
check("on a local model it is told the model runs on this PC, by its file name",
      "a model running on this PC (Some-9B-Q4_K_M.gguf)" in here and "sent there" not in here)
os.environ.pop("LOCAL_MODEL")
claude_brain.current = lambda: "codex"
check("on an agent program it is told which, and whose plan",
      "runs through Codex on the user's ChatGPT plan" in P.system_prompt())
claude_brain.current = lambda: None
check("it is told its name is not the app's", "don't call yourself Inglen" in " ".join(here.split()))

# ------------------------------------------------------------------- size
tokens = len(BASE) // 4
check(f"the shipped persona stays under 4,000 tokens ({tokens})", tokens < 4000)
check("the voice addition is small and only present in voice mode",
      0 < len(VOICE) - len(BASE) < 2000 and "SPOKEN" not in BASE)

# -------------------------------------------------------- nothing it names is false
names = {t["name"] for t in T.TOOL_DEFINITIONS} | {"load_tools"}
mentioned = set(re.findall(r"\b([a-z]+(?:_[a-z]+)+)\b", BASE))
not_tools = {"include_hearth", "workout_schedule"}      # a parameter and an example
phantom = sorted(m for m in mentioned - not_tools if m not in names)
check("every tool it mentions exists" + (f": {phantom}" if phantom else ""), not phantom)

check("it does not repeat the whole tool list", "Your toolbelt" not in BASE
      and sum(1 for n in names if n in BASE) < 45)
check("it does not claim there is no way to close an app",
      "no close-app tool" not in BASE.lower())
check("it does not state a turn cap that is not the real one",
      not re.search(r"up to \d+ sequential tool turns", BASE, re.I))

# ------------------------------------------------------- a product, not a diary
for word in ("tictactoe", "514-page", "oppenheimer", "battlefield", "clippy"):
    check(f"no single-incident patch naming {word!r}", word not in BASE.lower())
check("no owner's register baked into the shipped default",
      not re.search(r"\bbro\b", BASE, re.I))

# ----------------------------------------------------- what it must still say
for must, why in (
        ("tool did it", "only a tool's result makes something done"),
        ("check it now", "state is checked, never assumed"),
        ("web_search first", "search before guessing"),
        ("Never ask them for a path", "never ask for a path"),
        ("load_tools", "how to reach a held-back tool"),
        ("desktop_snapshot", "how to drive a named control"),
        ("include_hearth", "how to look at its own window"),
        ("search_chats", "how to recall an earlier conversation"),
        ("end_session", "how to end on a goodbye"),
        ("masked", "never print a secret"),
        ("as an AI", "no AI-disclaimer register"),
):
    check(f"it still covers: {why}", " ".join(must.split()) in " ".join(BASE.split()))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all persona checks passed")
