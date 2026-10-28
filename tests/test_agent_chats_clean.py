"""Coding agents' sessions on the phone read as the person's own chats.

What the owner saw on the phone: Codex's auto-review checks listed as sessions,
titled by whatever their prompt quoted ("es GPT suck at game dev...") and full
of APPROVAL REQUEST text; titles that were an agent's own context blocks
("<ide_selection>The user selected...", "<recommended_plugins>"); every step of
a turn in one "61 steps" pill above the words; Codex code-mode steps said as
"Exec", a PowerShell command as the path to pwsh.exe; and a usage limit shown as
if the agent had said it.

    .\\.venv\\Scripts\\python.exe tests\\test_agent_chats_clean.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-agent-clean-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import agent_sessions as S                              # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def write(path, *objs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(o) for o in objs) + "\n")


GAME = os.path.join(HOME, "Game dev", "NIGHT_COURIER")
os.makedirs(GAME)
CX = os.path.join(HOME, ".codex", "sessions", "2026", "10", "28")
T = "2026-10-28T10:00:00Z"


def meta(sid, **extra):
    return {"timestamp": T, "type": "session_meta", "payload": dict({"id": sid, "cwd": GAME, "originator": "codex_vscode"}, **extra)}


def user(text):
    return {"timestamp": T, "type": "event_msg", "payload": {"type": "user_message", "message": text}}


def said(text):
    return {"timestamp": T, "type": "event_msg", "payload": {"type": "agent_message", "message": text}}


def call(name, **kw):
    return {"timestamp": T, "type": "response_item", "payload": dict({"type": "function_call", "name": name}, **kw)}


REVIEW_PROMPT = ("CONTEXT END\nThe Codex agent has requested the following next action:\n>> APPROVAL REQUEST START\n"
                 "Assess the exact planned action below.\nes GPT suck at game dev, god knows...")
write(os.path.join(CX, "rollout-a-11111111-1111-1111-1111-111111111111.jsonl"),
      meta("11111111-1111-1111-1111-111111111111", source={"subagent": "review"}), user(REVIEW_PROMPT))
write(os.path.join(CX, "rollout-b-22222222-2222-2222-2222-222222222222.jsonl"),
      meta("22222222-2222-2222-2222-222222222222", thread_source="guardian_review"), user("Untitled check"))
write(os.path.join(CX, "rollout-c-33333333-3333-3333-3333-333333333333.jsonl"),
      meta("33333333-3333-3333-3333-333333333333"), user(REVIEW_PROMPT))
write(os.path.join(CX, "rollout-d-44444444-4444-4444-4444-444444444444.jsonl"),
      meta("44444444-4444-4444-4444-444444444444", source={"subagent": {"thread_spawn": {"parent_thread_id": "x", "depth": 1}}}),
      user("look at the inventory code"))
script = 'text((await tools.exec_command({ cmd: "git status --short", login: false })).output);'
write(os.path.join(CX, "rollout-e-55555555-5555-5555-5555-555555555555.jsonl"),
      meta("55555555-5555-5555-5555-555555555555", source="vscode"),
      user("<recommended_plugins>\nHere is a list of plugins\n</recommended_plugins>\n"
           "<external_codex_apps_open_page>{\"page_id\":null}</external_codex_apps_open_page>\n"
           "Fix the menu screen, it flashes"),
      {"timestamp": T, "type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "input": script}},
      call("exec_command", call_id="c2", arguments=json.dumps({"cmd": ["C:\\Users\\x\\pwsh.exe", "-Command", "npm test"]})),
      {"timestamp": T, "type": "response_item", "payload": {"type": "function_call_output", "call_id": "c2",
                                                             "output": json.dumps({"output": "12 passing", "metadata": {}})}},
      call("update_plan", arguments=json.dumps({"plan": []})),
      call("write_stdin", arguments=json.dumps({"chars": "y"})),
      said("The menu reloads on every focus. Fixing that."),
      call("apply_patch", arguments=json.dumps({"path": "menu.gd"})),
      said("Done: it loads once."),
      {"timestamp": T, "type": "event_msg", "payload": {"type": "error", "message": "You've hit your usage limit."}})

CL = os.path.join(HOME, ".claude", "projects", "C--Hearth")
write(os.path.join(CL, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jsonl"),
      {"type": "user", "cwd": GAME, "timestamp": T, "message": {"role": "user", "content":
       "<ide_selection>The user selected the lines 3 to 9 from ui.html</ide_selection>\nMake Rose the default look"}},
      {"type": "assistant", "cwd": GAME, "timestamp": T, "message": {"role": "assistant", "model": "claude-x", "content": [
          {"type": "text", "text": "Writing down your asks first."},
          {"type": "tool_use", "name": "Bash", "id": "toolu_1", "input": {"command": "git status", "description": "Show working tree status"}},
          {"type": "tool_use", "name": "Grep", "input": {"pattern": "round("}, "id": "toolu_2"}]}},
      {"type": "user", "cwd": GAME, "timestamp": T, "message": {"role": "user", "content": [
          {"type": "tool_result", "tool_use_id": "toolu_1", "content": "On branch main\nAPI_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"}]}},
      {"type": "assistant", "cwd": GAME, "timestamp": T, "isApiErrorMessage": True,
       "message": {"role": "assistant", "model": "<synthetic>", "content": [
           {"type": "text", "text": "You've hit your 5-hour limit · resets 12:20am"}]}})

rows = S.listing()
titles = {r["id"]: r["title"] for r in rows}
check(f"Codex's auto-review checks and its sub-agents aren't listed {sorted(titles.values())}",
      not ({"11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444"} & set(titles)))
check(f"a title is the person's words, not the context blocks before them {titles.get('55555555-5555-5555-5555-555555555555')!r}",
      titles.get("55555555-5555-5555-5555-555555555555") == "Fix the menu screen, it flashes")
check(f"the same for Claude Code's IDE selection {titles.get('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')!r}",
      titles.get("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa") == "Make Rose the default look")
check("both are listed under their project folder",
      all(r["project"] == "NIGHT_COURIER" for r in rows))

cx = S.read("codex", "55555555-5555-5555-5555-555555555555")["messages"]
reply = next(m for m in cx if m["role"] == "assistant" and not m.get("error"))
order = [("steps", [s["w"] for s in p["steps"]]) if "steps" in p else ("text", p["text"]) for p in reply["parts"]]
check(f"the person's message is their words only {cx[0]['text']!r}", cx[0]["text"] == "Fix the menu screen, it flashes")
check(f"a reply keeps its steps and words in the order they came {order}",
      [k for k, _ in order] == ["steps", "text", "steps", "text"])
first = order[0][1]
check(f"a code-mode script says what it ran, a PowerShell command is the command, bookkeeping isn't a step {first}",
      first == ["Ran git status --short", "Ran npm test", "Updated its plan"])
check("the whole text and steps stay for copying and older phones",
      reply["text"].startswith("The menu reloads") and len(reply["steps"]) == 4)
err = [m for m in cx if m.get("error")]
check(f"Codex's error is a message of its own, marked as one {[m['text'] for m in err]}",
      len(err) == 1 and "usage limit" in err[0]["text"])

cl = S.read("claude-code", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")["messages"]
check(f"Claude Code's usage limit is marked as an error, not a reply {[(m['role'], m.get('error')) for m in cl]}",
      cl[-1].get("error") is True and "5-hour limit" in cl[-1]["text"] and not cl[1].get("error"))
check("Claude's IDE selection isn't shown as the person's words", cl[0]["text"] == "Make Rose the default look")
steps = [s["w"] for s in cl[1]["steps"]]
check(f"a step the agent described reads in its own words; one it didn't is said plainly, a pattern in quotes {steps}",
      steps == ["Show working tree status", "Searched for \u201cround(\u201d"])

st = cl[1]["steps"][0]
check(f"a step carries what it ran and what came back, secrets blanked ({st.get('cmd')!r}, {st.get('out', '')[:60]!r})",
      st.get("cmd") == "git status" and "On branch main" in st.get("out", "") and "abcdefghijklmnop" not in st.get("out", ""))
npm = next(s for s in reply["steps"] if s["w"] == "Ran npm test")
check(f"Codex's step output is matched to its call ({npm.get('out')!r})", npm.get("out") == "12 passing" and npm.get("cmd") == "npm test")
print("\n" + ("all agent chat cleaning checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
