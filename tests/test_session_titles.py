"""Coding-agent sessions on the phone are named as their own app names them.

The owner's Codex session showed on the phone as "# Files pasted by the user:
## "/loo..." while Codex called it by its name: the title was the first
message, with what Codex adds before the person's words. Now: Codex's thread
names (~/.codex/session_index.jsonl, the newest entry wins), the words after
the last "## My request for Codex:" as Codex shows them, and the name given to
a Claude Code session (/rename).

    .\\.venv\\Scripts\\python.exe tests\\test_session_titles.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-session-titles-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import agent_sessions as S  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def write(path, *objs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(o) for o in objs) + "\n")


T = "2026-10-29T08:00:00Z"
GAME = os.path.join(HOME, "t")
os.makedirs(GAME)
CX = os.path.join(HOME, ".codex", "sessions", "2026", "10", "29")
PASTED = ('# Files pasted by the user:\n\n## "/loop/inventory.gd"\nextends Node\n\n'
          '## My request for Codex:\nFix the van losing its reflections')


def codex(sid, first):
    write(os.path.join(CX, f"rollout-x-{sid}.jsonl"),
          {"timestamp": T, "type": "session_meta", "payload": {"id": sid, "cwd": GAME}},
          {"timestamp": T, "type": "event_msg", "payload": {"type": "user_message", "message": first}},
          {"timestamp": T, "type": "event_msg", "payload": {"type": "agent_message", "message": "On it."}})


A, B = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
codex(A, PASTED)
codex(B, PASTED)
write(os.path.join(HOME, ".codex", "session_index.jsonl"),
      {"id": B, "thread_name": "Old name", "updated_at": T},
      {"id": B, "thread_name": "NIGHT COURIER goal", "updated_at": T})
CL = "33333333-3333-3333-3333-333333333333"
write(os.path.join(HOME, ".claude", "projects", "-t", CL + ".jsonl"),
      {"type": "user", "cwd": GAME, "timestamp": T, "message": {"role": "user", "content": "check the van"}},
      {"type": "assistant", "cwd": GAME, "timestamp": T, "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
      {"type": "custom-title", "customTitle": "Van reflections", "sessionId": CL})

titles = {r["id"]: r["title"] for r in S.listing(20)}
check(f"a Codex message's pasted files aren't its title: the person's words are ({titles.get(A)!r})",
      titles.get(A) == "Fix the van losing its reflections")
check(f"a thread Codex has named is called by that name, the newest one ({titles.get(B)!r})",
      titles.get(B) == "NIGHT COURIER goal")
check(f"a Claude Code session given a name is called by it ({titles.get(CL)!r})", titles.get(CL) == "Van reflections")
msgs = S.transcript(S.chat_id("codex", A), 10)["messages"]
check(f"and the message itself shows only what they asked ({msgs[0]['text']!r})",
      msgs[0]["text"] == "Fix the van losing its reflections")

rows = S.listing(20)
hits = S.search("REFLECTIONS", rows)
check(f"search finds the sessions that mention it, any case, with the words around it ({hits})",
      set(hits) >= {S.chat_id("codex", A), S.chat_id("codex", B)} and "reflections" in hits[S.chat_id("codex", A)].lower())
check("and nothing for words no session has", S.search("spreadsheet budget", rows) == {})
from hearth import relay_bridge as RB  # noqa: E402
got = RB.answer({"what": "agent_sessions", "query": "van"})
check(f"the phone asks it through the relay ({sorted(got.get('hits') or {})})",
      S.chat_id("claude-code", CL) in (got.get("hits") or {}))
w = S.transcript(S.chat_id("codex", A), 10)
check(f"a session written just now reads as at work ({w.get('working')})", w.get("working") is True)
old = os.path.join(CX, f"rollout-x-{A}.jsonl")
os.utime(old, (1, 1))
check("and one quiet for long doesn't", S.transcript(S.chat_id("codex", A), 10).get("working") is False)
# A Codex session's helpers, as its Subagents panel lists them.
SUB1, SUB2 = "44444444-4444-4444-4444-444444444444", "55555555-5555-5555-5555-555555555555"
for sub, nick in ((SUB1, "Nell"), (SUB2, "Ivo")):
    write(os.path.join(CX, f"rollout-y-{sub}.jsonl"),
          {"timestamp": T, "type": "session_meta", "payload": {"id": sub, "cwd": GAME, "source": {"subagent": {
              "thread_spawn": {"parent_thread_id": B, "depth": 1, "agent_nickname": nick, "agent_role": "explorer"}}}}},
          {"timestamp": T, "type": "event_msg", "payload": {"type": "user_message", "message": "audit continuity"}},
          {"timestamp": T, "type": "event_msg", "payload": {"type": "agent_message", "message": "Two gaps found."}})
with open(os.path.join(HOME, ".codex", "session_index.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"id": SUB1, "thread_name": "Nell continuity audit", "updated_at": T}) + "\n")
os.utime(os.path.join(CX, f"rollout-y-{SUB2}.jsonl"), (1, 1))
subs = RB.answer({"what": "agent_subagents", "chat": S.chat_id("codex", B)})["subagents"]
check(f"a session lists the helpers it started, newest first, by their names ({[(x['title'], x['working']) for x in subs]})",
      [x["title"] for x in subs] == ["Nell continuity audit", "Ivo explorer"] and subs[0]["working"] and not subs[1]["working"])
check("they aren't listed as sessions of their own", not ({SUB1, SUB2} & {r["id"] for r in S.listing(30)}))
sub = S.transcript(subs[0]["chat"], 10)
check(f"and one opens as a chat ({[m['text'] for m in sub.get('messages', [])]})",
      [m["text"] for m in sub.get("messages", [])] == ["audit continuity", "Two gaps found."])
check("another session has none", RB.answer({"what": "agent_subagents", "chat": S.chat_id("codex", A)})["subagents"] == [])
print("\n" + ("all session title checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
