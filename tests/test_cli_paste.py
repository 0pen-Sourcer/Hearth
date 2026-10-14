"""A long paste in the terminal's input line.

Pasting forty lines used to fill the screen with them before anything was
sent. Now the line shows a short marker, the whole text goes out on Enter, and
pasting the same text a second time swaps the marker for the text.

This feeds the terminal's own input session the byte sequence a terminal sends
for a paste, so the key binding, the marker and what the turn receives are the
real ones.

    .\\.venv\\Scripts\\python.exe tests\\test_cli_paste.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["JARVIS_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-paste-test-")
os.environ["LOCAL_API_BASE"] = "http://127.0.0.1:9/v1"
os.environ["HEARTH_NO_UPDATE_CHECK"] = "1"

from prompt_toolkit.application import create_app_session  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402
from prompt_toolkit.formatted_text import to_formatted_text  # noqa: E402

import hearth_cli  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


LONG = "\n".join(f"line {i}" for i in range(1, 8))
SHORT = "two\nlines"


def paste(text):
    return "\x1b[200~" + text.replace("\n", "\r") + "\x1b[201~"


def read(keys):
    """What the turn receives, the line as it stood at Enter, and whether the
    hint was showing then."""
    seen = {}
    with create_pipe_input() as inp:
        with create_app_session(input=inp, output=DummyOutput()):
            cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
            cli.multiline_mode = False
            cli.pt_session = None
            cli._pt_init_attempted = False
            cli._turn = None
            cli._footer_prompt = lambda: "> "
            cli._model_label = lambda: "model"
            cli.think_on, cli.voice_on, cli.context_tokens, cli._session_use = False, False, 32768, {}
            cli._ctx_used = lambda: 0
            cli._ensure_pt_session()
            buf = cli.pt_session.default_buffer

            def note(b):
                seen["line"] = b.text
                # The footer under the input turns into the hint while a marker is in the line.
                text = "".join(t for _, t in to_formatted_text(cli._footer()))
                seen["hint"] = text if "paste again" in text else None

            buf.on_text_changed += note
            inp.send_text(keys + "\r")
            got = asyncio.run(cli._read_input())
    return got, seen.get("line", ""), seen.get("hint")


got, line, hint = read("look at this " + paste(LONG))
check("a long paste shows as a marker with its line count",
      line == "look at this [Pasted text #1 +7 lines]")
check("the hint says how to see it", bool(hint) and "paste again to expand" in hint)
check("the turn receives the whole text", got == "look at this " + LONG)

got, line, hint = read(paste(LONG) + paste(LONG))
check("pasting the same text again puts the text in the line", line == LONG)
check("and the hint goes away", hint is None)
check("sent once, not twice", got == LONG)

got, line, hint = read(paste(SHORT))
check("a short paste stays as typed text", line == SHORT and got == SHORT and hint is None)

got, line, hint = read(paste(LONG) + " and " + paste(LONG + "\nline 8"))
check("two different pastes get their own markers",
      "[Pasted text #" in line and line.count("[Pasted text #") == 2)
check("both are sent whole and in order", got == LONG + " and " + LONG + "\nline 8")

got, line, hint = read("[Pasted text #99 +3 lines]")
check("a marker nobody pasted is left alone", got == "[Pasted text #99 +3 lines]" and hint is None)

hist = os.path.join(os.environ["JARVIS_WORKSPACE"], "logs", "input_history.txt")
saved = open(hist, encoding="utf-8").read() if os.path.isfile(hist) else ""
check("history on disk keeps the text, since a marker means nothing next session",
      "line 7" in saved and "[Pasted text #1 " not in saved)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all paste checks passed")
