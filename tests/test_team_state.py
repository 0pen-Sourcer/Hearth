"""What the Team view reports about each helper agent.

The state comes from the agents' own transcripts: which tool one is running
right now, whether it finished or failed, and whether it wanted a permission
it could not ask for. An agent whose transcript never ended and that no thread
owns any more (the app closed mid-run) shows as stopped, not working forever.

    .\\.venv\\Scripts\\python.exe tests\\test_team_state.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import subagents                                         # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


d = Path(tempfile.mkdtemp(prefix="hearth-team-"))
subagents._TRANSCRIPT_DIR = d


def write(aid, *recs):
    with open(d / f"{aid}.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


start = lambda persona, name="": {"kind": "start", "persona": persona, "name": name,
                                  "prompt": "look into it", "ts": "2026-09-30T10:00:00"}
write("sub_1_busy", start("researcher", "Ada"),
      {"kind": "assistant_tool_call", "content": "", "calls": [{"name": "web_search", "args": '{"query": "local tts"}'},
                                                                 {"name": "web_fetch", "args": '{"url": "https://www.example.org/a/b"}'}]},
      {"kind": "tool_result", "name": "web_search", "chars": 10, "preview": "results"})
write("sub_2_think", start("coder"),
      {"kind": "assistant_tool_call", "content": "", "calls": [{"name": "read_file"}]},
      {"kind": "tool_result", "name": "read_file", "chars": 5, "preview": "text"})
write("sub_3_done", start("summarizer"), {"kind": "assistant_final", "text": "Here is the summary."},
      {"kind": "end", "ok": True})
write("sub_4_fail", start("librarian"), {"kind": "end", "ok": False, "error": "no model loaded"})
write("sub_5_gone", start("archivist"),
      {"kind": "assistant_tool_call", "content": "", "calls": [{"name": "find_file"}]})
write("sub_6_block", start("coder", "Bo"),
      {"kind": "assistant_tool_call", "content": "", "calls": [{"name": "run_command"}]},
      {"kind": "tool_result", "name": "run_command", "chars": 80,
       "preview": "(not run: run_command needs the user's permission and a sub-agent cannot ask.)"})

subagents._BG_RUNNING.clear()
subagents._BG_RUNNING.update({"sub_1_busy", "sub_2_think", "sub_6_block"})
st = {a["id"]: a for a in subagents.team_state()}

check("a running agent mid-tool shows the tool it is on",
      st["sub_1_busy"]["status"] == "working" and st["sub_1_busy"]["activity"] == "tool"
      and st["sub_1_busy"]["tool"] == "web_fetch")
check("and what that tool is pointed at", st["sub_1_busy"].get("what") == "example.org")
story = subagents.agent_story("sub_1_busy")
check("a helper's story lists each step with what it was pointed at and what came back",
      [(x["tool"], x["what"], x["result"]) for x in story["steps"]]
      == [("web_search", "local tts", "results"), ("web_fetch", "example.org", None)]
      and story["prompt"] == "look into it" and story["ended"] is None)
check("a name that isn't a helper's gets nothing", subagents.agent_story("../settings") == {}
      and subagents.agent_story("sub_9_nobody") == {})
check("a file is named without its folders, and a cut-off call still reads",
      subagents._pointed_at('{"path": "C:\\Users\\me\\proj\\SPEC.md", "content": "half a fi') == "SPEC.md"
      and subagents._pointed_at("{}") == "")
check("its name and role come through", st["sub_1_busy"]["name"] == "Ada" and st["sub_1_busy"]["persona"] == "researcher")
check("a running agent between tools is thinking",
      st["sub_2_think"]["status"] == "working" and st["sub_2_think"]["activity"] == "thinking")
check("a finished agent is done, with its answer",
      st["sub_3_done"]["status"] == "done" and st["sub_3_done"]["preview"] == "Here is the summary.")
check("a failed agent says why", st["sub_4_fail"]["status"] == "failed" and "no model" in st["sub_4_fail"]["preview"])
check("an agent the app lost mid-run is stopped, not working",
      st["sub_5_gone"]["status"] == "stopped")
check("an agent refused a permission it could not ask for says which tool",
      st["sub_6_block"]["blocked"] == "run_command")
order = [a["status"] for a in subagents.team_state()]
check("working agents come first", order[:3] == ["working"] * 3)

subagents._BG_RUNNING.clear()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all team state checks passed")
