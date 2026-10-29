"""Voice turns on every brain: what the brain is told, what is spoken and shown,
and how a step that needs an OK is asked about.

- A local or cloud brain is told each spoken turn is spoken (no markdown, tables,
  lists, code or emoji), on the turn's own message, so the system prompt is the
  same typed or spoken and a local model's cached prefix survives.
- What is spoken and the caption on screen are cleaned of tables, lists and emoji.
- A risky step in a voice turn isn't read out from a template: it goes back to the
  brain to say what it will do and ask; only a clear yes runs it (a tap for what
  only a tap may allow); anything else said is a message, never a no. Auto lets
  looking at things run without asking.
Stand-in model calls; the page's functions run in node.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_turns.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

WORK = tempfile.mkdtemp(prefix="hearth-voice-turns-")
os.environ.update(JARVIS_WORKSPACE=WORK, HEARTH_WORKSPACE=WORK, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import call, claude_brain, headless, memory_extract, tools, voice, web  # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
claude_brain.current = lambda: None
headless.LOCAL_API_BASE = "http://127.0.0.1:59999/v1"
headless.LOCAL_API_KEY = "stand-in"
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def set_mode(mode):
    p = os.path.join(WORK, "settings.json")
    s = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    s["approval_mode"] = mode
    json.dump(s, open(p, "w", encoding="utf-8"))


def chunk(content=None, finish=None, calls=None):
    return NS(choices=[NS(index=0, delta=NS(content=content, reasoning_content=None, model_extra={},
                                             tool_calls=calls), finish_reason=finish)], usage=None, model_extra={})


def tool_chunk(name, args):
    return chunk(calls=[NS(index=0, id="call_1", type="function",
                           function=NS(name=name, arguments=json.dumps(args)))], finish="tool_calls")


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()


SENT: list = []
SCRIPT: list = []


async def fake(client, **kw):
    SENT.append(kw.get("messages"))
    return Stream(SCRIPT.pop(0) if SCRIPT else [chunk(content="Okay."), chunk(finish="stop")])

headless.chat_create = fake


def turn(prompt, voice_on, history=None, check_fn=None):
    SENT.clear()
    ev = []
    asyncio.run(headless.run_once(prompt, emit=lambda k, **f: ev.append((k, f)), model="stand-in",
                                  history=history or [], voice=voice_on, permission_check=check_fn))
    return ev


# ------------------------------------------------------------ what the brain is told
turn("What's the tallest mountain?", True)
spoken = SENT[0]
turn("What's the tallest mountain?", False)
typed = SENT[0]
user_spoken = next(m["content"] for m in reversed(spoken) if m["role"] == "user")
check("a spoken turn on a local or cloud brain says it is spoken, on the turn's own message",
      "Voice mode" in user_spoken and "tables" in user_spoken and "emoji" in user_spoken)
check("and asks it to say what it will do and ask before a step that changes something",
      "ask if that's okay" in user_spoken)
check("the system prompt is the same typed or spoken (the cached prefix survives)",
      spoken[0]["content"] == typed[0]["content"])
check("a typed turn carries no voice note",
      "Voice mode" not in next(m["content"] for m in reversed(typed) if m["role"] == "user"))
check("an agent brain gets the same note", headless.VOICE_TURN_NOTE in user_spoken)

# ------------------------------------------------------------ what is spoken
reply = ("Here's the comparison 🔍\n\n| Approach | Latency |\n|---|---|\n| GPU | 0.3 s |\n| CPU | 1.2 s |\n\n"
         "- **Fast** path\n- Slow → fallback\n\nWant the details? 🙋")
said = voice._clean_for_tts(reply)
check(f"spoken text has no table syntax ({said!r})", "|" not in said and "---" not in said)
check("no emoji", not re.search("[\U0001F300-\U0001FAFF]", said))
check("the cells are read in order", "Approach, Latency" in said and "GPU, 0.3 s" in said)
check("a piece cut mid-row has no pipes", "|" not in voice._clean_for_tts("| Approach | Lat"))

# The caption on screen: the page's own function, run in node.
node = shutil.which("node")
html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
m = re.search(r"function setVoiceTranscript\(s\) \{[\s\S]*?\n\}", html)
sa = re.search(r"const _YES_W = [\s\S]*?\nfunction spokenAnswer\(text\) \{[\s\S]*?\n\}", html)
if node and m:
    js = ("const box = {textContent: ''}; const $ = () => box;\n" + m.group(0) + "\n"
          + (sa.group(0) if sa else "function spokenAnswer(){return 'missing'}") + "\n"
          + f"setVoiceTranscript({json.dumps(reply)});\n"
          + "const ans = ['Great.', 'No, you may not.', 'How do you say the performance?', 'Yeah, go ahead.', 'yes'].map(spokenAnswer);\n"
          + "console.log(JSON.stringify({cap: box.textContent, ans}));")
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8")
    try:
        r = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        r = {"cap": out.stderr[:200], "ans": []}
    check(f"the caption on screen shows no table syntax or emoji ({r['cap'][:70]!r})",
          "|" not in r["cap"] and "---" not in r["cap"] and not re.search("[\U0001F300-\U0001FAFF]", r["cap"]))
    check(f"the page reads a spoken answer as the PC does ({r['ans']})", r["ans"] == ["", "deny", "", "allow", "allow"])
else:
    check("node and the page's caption function are there", False)

# ------------------------------------------------------------ approvals in a voice turn
check("'Great.' is not an answer", call.spoken_answer("Great.") == "")
check("'No, you may not.' is a clear no", call.spoken_answer("No, you may not.") == "deny")
check("a question is not an answer", call.spoken_answer("How do you say the performance?") == "")
check("'Yeah, go ahead.' is a clear yes", call.spoken_answer("Yeah, go ahead.") == "allow")
check("'no, do it this way instead' is a message", call.spoken_answer("no, do it this way instead") == "")

EMITS: list = []
base = web._make_permission_check(lambda k, **f: EMITS.append((k, f)))
vcheck = getattr(web, "_voice_permission_check", None)
set_mode("ask")
if vcheck is None:
    check("voice turns have their own permission check", False)
else:
    c = vcheck(base, lambda k, **f: EMITS.append((k, f)), "How busy is my GPU?", [])
    EMITS.clear()
    got = c("run_command", {"command": "nvidia-smi"})
    check(f"Ask mode, a command in a voice turn goes back to be asked about in words ({str(got)[:60]})",
          isinstance(got, dict) and got.get("decision") == "ask_aloud" and "okay" in got.get("reason", ""))
    check("its card is shown, marked as asked aloud (Allow and Deny still work)",
          any(k == "permission_request" and f.get("aloud") for k, f in EMITS))
    rid = next(f["id"] for k, f in EMITS if k == "permission_request")
    asked = [{"role": "user", "content": "How busy is my GPU?"},
             {"role": "assistant", "content": "I'll check your CPU and GPU usage with a command, okay?"}]
    c2 = vcheck(base, lambda k, **f: None, "Yes.", asked)
    check("after it asked, a clear yes runs it", c2("run_command", {"command": "nvidia-smi"}) == "allow")
    c3 = vcheck(base, lambda k, **f: None, "Great.", asked)
    check("'Great.' doesn't run it, and isn't a no", isinstance(c3("run_command", {"command": "nvidia-smi"}), dict))
    c4 = vcheck(base, lambda k, **f: None, "Yes.", asked)
    got = c4("run_command", {"command": "rm -rf C:/Users/someone/Documents"})
    check("a spoken yes never allows what only a tap may", isinstance(got, dict) and "tap" in got.get("reason", ""))
    # A tap on Allow on the card lets that exact step run when the brain calls it again.
    EMITS.clear()
    c5 = vcheck(base, lambda k, **f: EMITS.append((k, f)), "Delete my old notes folder", [])
    c5("delete_path", {"path": "C:/x/notes"})
    rid = next(f["id"] for k, f in EMITS if k == "permission_request")
    web._answer_voice_ask(rid, "allow")
    c6 = vcheck(base, lambda k, **f: None, "Yes.", asked)
    check("a tap on Allow lets that exact step run", c6("delete_path", {"path": "C:/x/notes"}) == "allow")
    set_mode("auto")
    EMITS.clear()
    c7 = vcheck(base, lambda k, **f: EMITS.append((k, f)), "How busy is my GPU?", [])
    check("Auto: looking at things runs without asking", c7("run_command", {"command": "nvidia-smi"}) == "allow"
          and not EMITS)
    set_mode("ask")

    # Through the real turn: the brain calls a command; what it is told back.
    SCRIPT[:] = [[tool_chunk("run_command", {"command": "nvidia-smi"})],
                 [chunk(content="I'll check your GPU usage with a command, okay?"), chunk(finish="stop")]]
    ev = turn("How busy is my GPU?", True, check_fn=vcheck(base, lambda k, **f: None, "How busy is my GPU?", []))
    res = [f.get("content", "") for k, f in ev if k == "tool_result"]
    check(f"the brain is told to ask in its own words, not that they said no ({res[:1]})",
          bool(res) and "Not run yet" in res[0] and "said no" not in res[0])
    check("and its question is the reply", any(k == "assistant" and "okay?" in (f.get("content") or "") for k, f in ev))

# Something said while a card waits on an answer (an agent that only hears yes or no).
import queue  # noqa: E402
rid = "perm_1_run_command"
web._permission_queues[rid] = queue.Queue()
web._WIN_ASK.clear()
web._WIN_ASK.update(id=rid, tap=False)
got = web._window_said("Great.")
ans = web._permission_queues[rid].get_nowait() if not web._permission_queues[rid].empty() else None
check(f"said while a card waits, 'Great.' is not taken as a no ({got}, {ans})",
      got != "ran" and isinstance(ans, dict) and ans.get("decision") == "aside")
web._permission_queues[rid] = queue.Queue()
web._WIN_ASK.update(id=rid, tap=False)
web._window_said("No, you may not.")
check("a clear no still answers it", web._permission_queues[rid].get_nowait() == "deny")

# ------------------------------------------------------------ the timing log
# One line per spoken turn in logs/voice_turns.log, each stage after the speech ended.
import numpy as np  # noqa: E402
import time  # noqa: E402
voice._load_engine = lambda allow_download=False: ("kokoro", None)
voice._synth_chunk = lambda kind, obj, chunk, v, speed: (np.zeros(2400, dtype=np.int16), 24000)
sent: list = []
c = call.Call(send=sent.append, say=lambda t: "ran")
c.quick = True
c._heard("What's the tallest mountain?")
t = call.Turn(c)
t.event("assistant_chunk", {"content": "Mount Everest, "})
t.event("assistant_chunk", {"content": "at about 8,849 metres."})
t.event("assistant", {"content": "Mount Everest, at about 8,849 metres."})
t.end()
log = os.path.join(WORK, "logs", "voice_turns.log")
for _ in range(50):
    if os.path.exists(log):
        break
    time.sleep(0.1)
row = json.loads(open(log, encoding="utf-8").read().splitlines()[-1]) if os.path.exists(log) else {}
check(f"a spoken turn writes its timing line ({row})",
      all(k in row for k in ("final", "posted", "first_token", "first_audio", "done", "stt", "brain"))
      and row["final"] <= row["posted"] <= row["first_token"] <= row["first_audio"] <= row["done"])

# How soon a reply starts: its first piece is short, even when a long opening sentence
# arrives at once or breaks only with a dash (the voice takes about a second per 50 letters).
pieces: list = []
c2 = call.Call(send=lambda m: None, say=lambda t: "ran")
c2.quick = True
c2._speak = lambda text: pieces.append(text)
c2.begin()
c2.live("Mount Everest is the tallest mountain above sea level — its summit sits at about 8,849 metres "
        "(29,032 feet), a figure confirmed by China and Nepal in 2020.\n\nIf you mean")
check(f"a long opening sentence starts with its first part ({pieces[:1]})",
      bool(pieces) and len(pieces[0]) <= 70)
pieces.clear()
c2.begin()
c2.live("Sure thing. ")
check(f"a short one goes whole ({pieces})", pieces == ["Sure thing."])

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall voice turn checks passed")
