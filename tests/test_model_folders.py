"""Model folders: one list for chat and image folders, the same in My Models and /models.

    .\\.venv\\Scripts\\python.exe tests\\test_model_folders.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-folders-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ.pop("HEARTH_MODELS_DIR", None)

from hearth import web, llmserver, sdserver   # noqa: E402
import hearth_cli                            # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


llmserver._MODELS_POINTER = type(llmserver._MODELS_POINTER)(os.path.join(HOME, ".hearth", "models_dir.txt"))
sdserver.forge_models = lambda: ""
f = web._model_folders()
check("the chat folder is listed", f["chat"] == llmserver.get_models_dir())
check("Inglen's own image folder is listed as its own",
      [d["own"] for d in f["images"]] == [True] and f["images"][0]["path"] == str(sdserver.models_dir()))

mine = os.path.join(HOME, "comfy", "models")
os.makedirs(mine)
cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
out = io.StringIO()
with redirect_stdout(out):
    cli._cmd_model_folders(f"add {mine}")
f = web._model_folders()
check(f"/models folders add reads one in place ({out.getvalue().strip()[:80]})",
      any(d["path"] == mine and not d["own"] for d in f["images"]))
out = io.StringIO()
with redirect_stdout(out):
    cli._cmd_model_folders("")
check("/models folders lists chat and image folders", "Chat models" in out.getvalue() and mine in out.getvalue())
with redirect_stdout(io.StringIO()):
    cli._cmd_model_folders("remove 1")
check("and remove takes it off", all(d["path"] != mine for d in web._model_folders()["images"]))
new = os.path.join(HOME, "D", "models")
with redirect_stdout(io.StringIO()):
    cli._cmd_model_folders(f"chat {new}")
check("chat sets where chat models download, the same setting the window uses",
      llmserver.get_models_dir() == new and open(llmserver._MODELS_POINTER, encoding="utf-8").read().strip() == new)

print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
