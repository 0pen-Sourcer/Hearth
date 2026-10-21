"""The terminal's first-run setup offers a model when there is none on disk.

It only said "run /models later", while the window's setup offers the model that
fits the card. Now the terminal offers the same one and downloads it on a yes.
Stand-ins for Hugging Face and the download.

    .\\.venv\\Scripts\\python.exe tests\\test_cli_onboard_model.py
"""
from __future__ import annotations

import asyncio
import builtins
import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-cli-onboard-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import hearth_cli  # noqa: E402
from hearth import llmserver  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


llmserver._is_lite_edition = lambda: False
llmserver.runtime_advice = lambda: {}
llmserver.scan_disk_for_models = lambda: []
llmserver.suggest_model = lambda: {"repo": "maker/Chat-9B-GGUF", "filename": "Chat-9B-Q4_K_M.gguf",
                                   "size_gb": 5.0, "params_b": 9.0, "downloads": 1000}
cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
ran = []


async def fake_models(raw):
    ran.append(raw)
cli._cmd_models = fake_models


def run(answer):
    ran.clear()
    real = builtins.input
    builtins.input = lambda *_a: answer
    try:
        asyncio.run(cli._onboard_pick_local_model())
    finally:
        builtins.input = real
    return list(ran)


check("with nothing on disk, a yes downloads the model that fits this card",
      run("") == ["/models get maker/Chat-9B-GGUF Chat-9B-Q4_K_M.gguf"])
check("a no leaves it for later", run("n") == [])
llmserver.suggest_model = lambda: None
check("with nothing to suggest (offline), it just points at /models", run("") == [])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all terminal setup model checks passed")
