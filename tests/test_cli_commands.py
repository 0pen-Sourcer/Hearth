"""Typing commands in the terminal.

The completer offered nothing: it matched words, and "/" is not part of one,
so "/br" never found "/brain". A model name after "/brain <provider>" was saved
as that provider's API key, and every message after it failed to sign in. A
model that packed three calls into one was refused, and the refused call stayed
in the history, so the provider turned down every later message.

    .\\.venv\\Scripts\\python.exe tests\\test_cli_commands.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["JARVIS_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-commands-test-")
os.environ["LOCAL_API_BASE"] = "http://127.0.0.1:9/v1"
os.environ["HEARTH_NO_UPDATE_CHECK"] = "1"

from prompt_toolkit.application import create_app_session  # noqa: E402
from prompt_toolkit.document import Document  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

import hearth_cli  # noqa: E402
from hearth.tool_call_parser import split_calls  # noqa: E402


FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)

# Text copied from the terminal's own input line and pasted back came in
# broken at every row, mid-word ("s" / "pecifically").
row1 = "can you launch a team so I can inspect how the teams look like for testing purposes, I am using CLI for this reason s"
row2 = "pecifically so I can tick mark that teams work properly rather than having bugs and glitches before v0.7.6 launch of He"
copied = row1 + "\n" + row2 + "\narth."
check("text copied from the terminal's rows is one line again",
      hearth_cli.unwrap_terminal(copied, 120) == row1 + row2 + "arth.")
check("a row that ended on a space gets the space back",
      hearth_cli.unwrap_terminal("a" * 117 + "\n" + "c" * 119 + "\n" + "b" * 118 + "\nnext", 120)
      == "a" * 117 + "c" * 119 + "b" * 118 + " next")
poem = "roses are red\nviolets are blue"
check("a paste with its own line breaks is left alone", hearth_cli.unwrap_terminal(poem, 120) == poem)


# ------------------------------------------------------------- completion
with create_pipe_input() as inp:
    with create_app_session(input=inp, output=DummyOutput()):
        cli = hearth_cli.JarvisCLI.__new__(hearth_cli.JarvisCLI)
        cli.multiline_mode = False
        cli.pt_session = None
        cli._pt_init_attempted = False
        cli._footer_prompt = lambda: "> "
        cli.last_model_list = ["models/gemini-3.5-flash", "models/gemini-3.5-pro"]
        cli._ensure_pt_session()
        comp = cli.pt_session.completer

        def offered(text):
            return [(c.text, c.display_meta_text) for c in comp.get_completions(Document(text), None)]

        first = offered("/br")
        check(f"'/br' offers /brain, with what it does {first}",
              [c for c, _ in first] == ["/brain"] and "switch" in first[0][1])
        check("'/' alone offers every command", len(offered("/")) == len(hearth_cli.SLASH_COMMANDS))
        provs = [c for c, _ in offered("/brain ")]
        check(f"after /brain come the providers and the agents {provs}",
              {"local", "anthropic", "gemini", "openrouter", "claude-code", "codex", "cursor"} <= set(provs))
        check("a few letters narrow it", [c for c, _ in offered("/brain cu")] == ["custom", "cursor"])
        check("an agent offers its model list", [c for c, _ in offered("/brain codex ")] == ["models"])
        check("/mode offers its three modes", [c for c, _ in offered("/mode ")] == ["ask", "edits", "auto"])
        check("/model offers the last list shown", [c for c, _ in offered("/model models/gemini-3.5-p")]
              == ["models/gemini-3.5-pro"])
        check("prose is left alone", offered("tell me about /br") == [] and offered("hello") == [])

# ---------------------------------------------------- a key or a model name
keys = ["sk-or-v1-" + "a" * 64, "AIza" + "B" * 35, "xai-" + "c" * 80, "gsk_" + "d" * 52, "sk-ant-api03-" + "e" * 90,
        "AQ.Ab8RN6Kx" + "f" * 40]
names = ["qwen/qwen3.8-27b:free", "models/gemini-3.5-flash", "gemini-3.5-flash", "claude-sonnet-5-5",
         "use", "inclusionai/ling-3.1-flash", "Meta-Llama-3.1-70B-Instruct-Turbo",
         "claude-opus-4-5-20251101", "llama-3.3-70b-versatile"]
check("API keys are told from model names",
      all(hearth_cli._looks_like_key(k) for k in keys) and not any(hearth_cli._looks_like_key(n) for n in names))
check("a typo gets a suggestion", hearth_cli._did_you_mean("cusror", ["cursor", "codex", "custom"]) == "cursor"
      and hearth_cli._did_you_mean("/brian", hearth_cli.SLASH_COMMANDS) == "/brain")

# ------------------------------------------------------- packed tool calls
packed = ('{"persona":"researcher","prompt":"a","name":"A"}{"persona":"coder","prompt":"b","name":"B"}'
          '{"persona":"researcher","prompt":"c","name":"C"}')
calls = split_calls([{"id": "call_1", "type": "function", "function": {"name": "spawn_subagent", "arguments": packed}},
                     {"id": "call_2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "x"}'}},
                     {"id": "call_3", "type": "function", "function": {"name": "run_command", "arguments": '{"command": "dir'}}])
check("three calls packed into one run as three, each with its own id",
      [c["id"] for c in calls] == ["call_1", "call_1_1", "call_1_2", "call_2", "call_3"]
      and [json.loads(c["function"]["arguments"])["name"] for c in calls[:3]] == ["A", "B", "C"])
check("a call that reads fine is untouched", calls[3]["function"]["arguments"] == '{"path": "x"}')
check("an unreadable call goes back to the provider as {} and keeps its text for the error",
      calls[4]["function"]["arguments"] == "{}" and calls[4]["raw_arguments"] == '{"command": "dir')

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all command checks passed")
