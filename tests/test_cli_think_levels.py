"""/think in the terminal offers the current brain and model's own levels.

An agent model that takes no level says so instead of offering a lone
default; a cloud model's levels come from its provider's list and the model
catalog, without Off where it can't stop reasoning.

    .\\.venv\\Scripts\\python.exe tests\\test_cli_think_levels.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import asyncio
import contextlib
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
work = tempfile.mkdtemp(prefix="hearth-cli-think-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1",
                  HEARTH_NO_UPDATE_CHECK="1", LOCAL_API_BASE="http://127.0.0.1:9/v1")

import hearth_cli  # noqa: E402
from hearth import claude_brain as cb, headless  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
cli.think_on, cli.think_level, cli.current_model = True, "", "gpt-new"
cli._model_label = lambda: "gpt-new"


def say(cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        asyncio.run(cli.handle_command(cmd))
    return out.getvalue()


saved = {}
hearth_cli._save_setting = lambda k, v: saved.__setitem__(k, v)

cb.current = lambda: "cursor-agent"
cb.agent_models = lambda p: {"label": "Cursor", "model": "composer", "effort": "",
                             "models": [{"id": "composer", "efforts": []}]}
said = say("/think high")
check(f"an agent model with no levels says it decides on its own {said!r}",
      "no thinking level" in said and "think_level" not in saved)
cb.agent_models = lambda p: {"label": "Cursor", "model": "gpt-5.5", "effort": "",
                             "models": [{"id": "gpt-5.5", "efforts": ["low", "xhigh"]}]}
say("/think xhigh")
check(f"one of its own levels is saved {saved}", saved.get("think_level") == "xhigh")

cb.current = lambda: None
hearth_cli.LOCAL_API_BASE = headless.PROVIDERS["openai"][1]
headless.effort_facts = lambda base, model: {"reasoning": True, "efforts": ["low", "high"], "can_off": False}
said = say("/think off")
check(f"a cloud model that can't stop reasoning isn't offered Off {said!r}",
      "low, high" in said and "off" not in said.split("takes:")[-1] and cli.think_on)
say("/think high")
check("its own level is taken", cli.think_on and cli.think_level == "high")
headless.effort_facts = lambda base, model: {"reasoning": False}
said = say("/think high")
check(f"a cloud model that doesn't reason says so {said!r}", "doesn't reason" in said)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all terminal think checks passed")
