"""Coding-agent sessions carried on from the phone, with stand-in agent programs.

Codex goes on over its app-server: the session keeps its own permissions (one an
older Inglen left read-only with approvals off is lifted to on-request in its
folder, never lowered), what it asks about reaches the phone, a message sent while
it works steers the turn, and its own commands (/compact, /review, /model) go to
its app-server as it takes them. Claude Code goes on over its stream-json protocol
with the permission mode said outright and its permission prompts coming to the
phone; a message sent while it works goes in as the next one. Codex's internal
approval reviewer is never a helper and its prompt is never shown. A new session
starts in the agent's own harness in a folder it has used. On a call, a step the
brain asks about aloud is answered only by a whole-sentence yes or no; anything
else said is a message of its own, never a no.

    .\\.venv\\Scripts\\python.exe tests\\test_phone_sessions.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-phone-sessions-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import agent_launch as L, agent_sessions as S, channel_core as CC  # noqa: E402
from hearth import claude_sessions, codex_sessions, relay_bridge as RB  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


PROJ = os.path.join(HOME, "code", "shop")
os.makedirs(PROJ)
BIN = os.path.join(HOME, "bin")
LOG = os.path.join(HOME, "rpc.jsonl")

# ------------------------------------------------------------ stand-in Codex app-server

write(os.path.join(BIN, "codex.py"), f'''
import json, os, sys
out = lambda o: print(json.dumps(o), flush=True)
log = lambda o: open({LOG!r}, "a").write(json.dumps(o) + "\\n")
policy, sandbox = os.environ.get("FAKE_POLICY", "on-request"), os.environ.get("FAKE_SANDBOX", "workspaceWrite")
for line in sys.stdin:
    m = json.loads(line)
    log(m)
    meth, p = m.get("method"), m.get("params") or {{}}
    if meth is None:                                    # an answer to what it asked
        ok = (m.get("result") or {{}}).get("decision") == "accept"
        out({{"method": "item/completed", "params": {{"item": {{"id": "r", "type": "agentMessage", "text": "Ran it." if ok else "Left it."}}}}}})
        out({{"method": "turn/completed", "params": {{"turn": {{"id": "t1", "status": "completed"}}}}}})
        continue
    if "id" not in m:
        continue
    r = {{}}
    if meth == "thread/resume":
        r = {{"thread": {{"id": p["threadId"]}}, "approvalPolicy": policy, "sandbox": {{"type": sandbox}}}}
    elif meth == "thread/start":
        r = {{"thread": {{"id": "fresh0001"}}, "approvalPolicy": "on-request", "sandbox": {{"type": "workspaceWrite"}}}}
    elif meth == "turn/start":
        r = {{"turn": {{"id": "t1"}}}}
    elif meth == "review/start":
        r = {{"turn": {{"id": "rv1"}}}}
    elif meth == "model/list":
        r = {{"data": [{{"id": "gpt-a"}}, {{"id": "gpt-b"}}]}}
    out({{"id": m["id"], "result": r}})
    if meth == "turn/start":
        out({{"method": "item/started", "params": {{"item": {{"id": "c", "type": "commandExecution", "command": "rm -rf build"}}}}}})
        out({{"id": 900, "method": "item/commandExecution/requestApproval", "params": {{"command": "rm -rf build"}}}})
    elif meth == "review/start":
        out({{"method": "item/completed", "params": {{"item": {{"id": "rv", "type": "agentMessage", "text": "Reviewed: no issues."}}}}}})
        out({{"method": "turn/completed", "params": {{"turn": {{"id": "rv1", "status": "completed"}}}}}})
    elif meth == "thread/compact/start":
        out({{"method": "thread/compacted", "params": {{"threadId": p["threadId"]}}}})
''')

# ------------------------------------------------------------ stand-in Claude Code

write(os.path.join(BIN, "claude.py"), f'''
import json, sys
out = lambda o: print(json.dumps(o), flush=True)
log = lambda o: open({LOG!r}, "a").write(json.dumps(dict(o, argv=sys.argv[1:])) + "\\n")
users = 0
for line in sys.stdin:
    m = json.loads(line)
    log(m)
    if m.get("type") == "control_request":
        out({{"type": "control_response", "response": {{"subtype": "success", "request_id": m["request_id"], "response": {{}}}}}})
    elif m.get("type") == "user":
        users += 1
        if users == 1:
            out({{"type": "system", "subtype": "init", "session_id": "cl-session-1"}})
            out({{"type": "control_request", "request_id": "p1", "request": {{"subtype": "can_use_tool", "tool_name": "Bash",
                 "input": {{"command": "rm -rf build"}}}}}})
        else:
            out({{"type": "result", "result": "Took the second one too.", "is_error": False}})
    elif m.get("type") == "control_response":
        ok = m["response"]["response"]["behavior"] == "allow"
        out({{"type": "result", "result": "Ran it." if ok else "Left it.", "is_error": False}})
''')

CODEX = [sys.executable, os.path.join(BIN, "codex.py")]
CLAUDE = [sys.executable, os.path.join(BIN, "claude.py")]


def rpc(method=None):
    try:
        rows = [json.loads(x) for x in open(LOG, encoding="utf-8")]
    except OSError:
        return []
    return [r for r in rows if method is None or r.get("method") == method]


def drive(coro_fn, allow=True, said=()):
    """Run one session turn; returns (rc, events, questions)."""
    events, asked, left = [], [], list(said)
    if os.path.exists(LOG):
        os.remove(LOG)

    def ask(q):
        asked.append(q)
        return allow

    def drain():
        return [left.pop(0)] if left else []
    rc = asyncio.run(asyncio.wait_for(coro_fn(lambda k, **f: events.append((k, f)), ask, drain), 30))
    return rc, events, asked


# ------------------------------------------------------------ Codex

rc, ev, asked = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "clean the build", e, a))
resume = (rpc("thread/resume") or [{}])[0].get("params") or {}
start = (rpc("turn/start") or [{}])[0].get("params") or {}
check(f"Codex resumes the session with no permission overrides ({sorted(resume)})",
      resume.get("threadId") == "th-1" and not {"approvalPolicy", "sandbox", "approvalsReviewer"} & set(resume))
check(f"and runs the turn with the session's own permissions ({sorted(start)})",
      not {"approvalPolicy", "sandboxPolicy"} & set(start))
check(f"what it asks about reaches the phone, said plainly ({asked})", asked and asked[0].startswith("Codex asks: may it")
      and "rm -rf build" in asked[0])
check("and the yes goes back to Codex", ev[-1] == ("text", {"text": "Ran it."}) and rc == 0)
rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "clean the build", e, a), allow=False)
check("a no goes back too, and Codex says what it did", ev[-1] == ("text", {"text": "Left it."}))

os.environ.update(FAKE_POLICY="never", FAKE_SANDBOX="readOnly")
rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "keep going", e, a))
start = (rpc("turn/start") or [{}])[0].get("params") or {}
check(f"a session left read-only with approvals off is lifted to on-request in its folder ({start.get('approvalPolicy')}, "
      f"{start.get('sandboxPolicy')})", start.get("approvalPolicy") == "on-request"
      and start.get("sandboxPolicy") == {"type": "workspaceWrite", "writableRoots": [PROJ], "networkAccess": False})
os.environ.update(FAKE_POLICY="on-request", FAKE_SANDBOX="readOnly")
rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "keep going", e, a))
start = (rpc("turn/start") or [{}])[0].get("params") or {}
check("one the person set read-only themselves (approvals on) stays as it is", "sandboxPolicy" not in start)
os.environ.pop("FAKE_POLICY"); os.environ.pop("FAKE_SANDBOX")

rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "clean the build", e, a, drain=d),
                  said=["only the build folder"])
steer = (rpc("turn/steer") or [{}])[0].get("params") or {}
check(f"a message sent while it works steers the turn it is on ({steer})",
      steer.get("expectedTurnId") == "t1" and steer.get("input") == [{"type": "text", "text": "only the build folder"}]
      and ("note", {"text": "Codex has it, in the turn it's on."}) in ev)

rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "/review", e, a))
review = (rpc("review/start") or [{}])[0].get("params") or {}
check(f"/review runs Codex's own review ({review})", review.get("target") == {"type": "uncommittedChanges"}
      and not rpc("turn/start") and ev[-1] == ("text", {"text": "Reviewed: no issues."}))
rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "/compact", e, a))
check(f"/compact compacts the session ({[k for k, _ in ev]})", rpc("thread/compact/start") and ("note", {"text": "Compacted."}) in ev)
rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "/model", e, a))
check(f"/model lists its models ({ev[-1]})", ev[-1] == ("text", {"text": "Models: gpt-a, gpt-b"}))
drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "/model gpt-b", e, a))
drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "go on", e, a))
check("and /model <id> uses that model from the next message", ((rpc("turn/start") or [{}])[0].get("params") or {}).get("model") == "gpt-b")
rc, ev, _ = drive(lambda e, a, d: codex_sessions.run(CODEX, "th-1", PROJ, "/fork", e, a))
check(f"a command only its app has is said plainly, not sent to the model ({ev[-1]})",
      not rpc("turn/start") and "works in the Codex app" in ev[-1][1].get("text", ""))

# ------------------------------------------------------------ Claude Code

rc, ev, asked = drive(lambda e, a, d: claude_sessions.run(CLAUDE, "cl-session-1", PROJ, "clean the build", e, a))
argv = (rpc() or [{}])[0].get("argv") or []
check(f"Claude Code resumes with the permission mode said outright and its prompts over stdio ({argv})",
      "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "default"
      and argv[argv.index("--permission-prompt-tool") + 1] == "stdio" and argv[argv.index("--resume") + 1] == "cl-session-1")
check(f"what it asks about reaches the phone ({asked})", asked and asked[0].startswith("Claude Code asks: may it")
      and "rm -rf build" in asked[0])
allowed = [r for r in rpc() if r.get("type") == "control_response"]
check("and the yes goes back as its permission answer", allowed and allowed[0]["response"]["response"]["behavior"] == "allow"
      and ev[-1] == ("text", {"text": "Ran it."}))
rc, ev, _ = drive(lambda e, a, d: claude_sessions.run(CLAUDE, "cl-session-1", PROJ, "clean the build", e, a), allow=False)
denied = [r for r in rpc() if r.get("type") == "control_response"]
check("a no goes back as a deny", denied and denied[0]["response"]["response"]["behavior"] == "deny" and ev[-1] == ("text", {"text": "Left it."}))
rc, ev, _ = drive(lambda e, a, d: claude_sessions.run(CLAUDE, "cl-session-1", PROJ, "clean the build", e, a, drain=d),
                  said=["then the cache"])
users = [r["message"]["content"] for r in rpc() if r.get("type") == "user"]
check(f"a message sent while it works goes in as the next one, and both are answered ({users})",
      users == ["clean the build", "then the cache"] and ("note", {"text": "Claude Code takes this next."}) in ev
      and ("text", {"text": "Took the second one too."}) in ev and rc == 0)

# ------------------------------------------------------------ Codex's own reviewer stays out of sight

guard = os.path.join(HOME, ".codex", "sessions", "2026", "10", "30", "rollout-2026-10-30T10-00-00-guard0001.jsonl")
parent = os.path.join(HOME, ".codex", "sessions", "2026", "10", "30", "rollout-2026-10-30T09-00-00-parent0001.jsonl")
write(parent, "\n".join(json.dumps(o) for o in [
    {"type": "session_meta", "payload": {"id": "parent0001", "cwd": PROJ, "source": "vscode"}},
    {"type": "event_msg", "payload": {"type": "user_message", "message": "fix the cart"}}]) + "\n")
write(guard, "\n".join(json.dumps(o) for o in [
    {"type": "session_meta", "payload": {"id": "guard0001", "cwd": PROJ, "parent_thread_id": "parent0001",
                                         "source": {"subagent": {"other": "guardian"}}, "thread_source": "guardian_review"}},
    {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": "You are judging one planned action."}},
    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "the reviewer's private prompt"}}]) + "\n")
S._CACHE.clear()
check(f"Codex's approval reviewer isn't listed as a helper of the session it reviewed ({S.subagents(S.chat_id('codex', 'parent0001'))})",
      S.subagents(S.chat_id("codex", "parent0001")) == [])
check("and it can't be opened, so its prompt is never shown",
      "private prompt" not in json.dumps(S.transcript(S.chat_id("codex", "guard0001"))))
check("a system or developer message is never read as said",
      all(S._said_in({"type": "response_item", "payload": {"type": "message", "role": r, "content": "the system prompt"}}) == ""
          for r in ("system", "developer")))

# ------------------------------------------------------------ a new session from the phone

S._exe = lambda a: CODEX if a == "codex" else None
got = RB.answer({"what": "agent_catalog"})
check(f"only the agents installed here are offered, with their marks ({got.get('agents')})",
      [(a["id"], a["label"], bool(a.get("brand"))) for a in got.get("agents") or []] == [("codex", S.label("codex"), True)])
check(f"with the folders they have worked in ({[r['path'] for r in got.get('recent') or []]})",
      any(os.path.samefile(r["path"], PROJ) for r in got.get("recent") or []))
os.makedirs(os.path.join(HOME, ".hearth"), exist_ok=True)
got = RB.answer({"what": "agent_folders", "path": HOME})
check(f"the PC's folders can be browsed, without hidden ones or Inglen's keys ({[f['name'] for f in got.get('folders') or []]})",
      "code" in [f["name"] for f in got.get("folders") or []] and ".hearth" not in [f["name"] for f in got.get("folders") or []])
check("and Inglen's key folder can't be opened", "error" in RB.answer({"what": "agent_folders", "path": os.path.join(HOME, ".hearth")}))
row = L.reserve("codex", PROJ, "set up the tests")
check(f"a new session gets a chat the phone opens at once ({row.get('chat')})", row.get("chat", "").startswith(S.PREFIX))
rc, ev, _ = drive(lambda e, a, d: S.say(row["chat"], "set up the tests", e, a))
check(f"its first message starts a fresh session in that folder, in the agent's own harness ({[k for k, _ in ev]})",
      rpc("thread/start") and os.path.samefile(rpc("thread/start")[0]["params"]["cwd"], PROJ)
      and ("session", {"agent": "codex", "id": "fresh0001"}) in ev)
check("an agent that isn't installed can't be started", "error" in L.reserve("claude-code", PROJ, "hi"))

# ------------------------------------------------------------ Memory on the phone

from hearth import memory, memory_cards  # noqa: E402

long = "They keep receipts in the blue folder. " * 120
write(os.path.join(HOME, "mem", "receipts.md"), "---\nname: Receipts\ndescription: Where receipts go\n---\n" + long)
memory.MEM_DIR = os.path.join(HOME, "mem")
memory_cards.everything = lambda *a, **k: []
fact = next(f for f in RB.memory_view({})["facts"] if f["name"] == "Receipts")
check(f"Memory sends the phone a fact's whole text to open, not a cut ({len(fact['more'])} of {len(long.strip())})",
      fact["text"] == "Where receipts go" and fact["more"].strip() == long.strip())

# ------------------------------------------------------------ "call me when you're done", from any agent

from hearth import call_me, live_bus, memory_mcp  # noqa: E402

rung = []
live_bus.publish = lambda ev: rung.append(ev)
call_me.quiet_now = lambda r, now=None: False
call_me.at_pc = lambda idle=None: False
call_me.reach = lambda: ""                          # a paired phone, its bridge up
said = memory_mcp.call_me("The tests pass and the branch is pushed.")
check(f"an agent's call_me rings the phone through Inglen, even with Inglen's own calls off ({said})",
      rung and rung[-1]["kind"] == "call_me" and "tests pass" in rung[-1]["say"] and said.startswith("Calling"))
call_me.at_pc = lambda idle=None: True
check("not while they're at the PC, and it says so", memory_mcp.call_me("done").startswith("Not calling") and len(rung) == 1)
args = claude_sessions.args_for(["claude"], "s1", "default")
check("a Claude Code session started from the phone gets Inglen's agent tools", "--mcp-config" in args
      and "hearth-memory" in args[args.index("--mcp-config") + 1])
check("so does a Codex one", any("mcp_servers.hearth-memory.command" in a for a in codex_sessions.inglen_tools()))
check("and an ACP one (OpenCode, Gemini CLI)", S.acp_tools()[0]["name"] == "hearth-memory")

# ------------------------------------------------------------ a goodbye, then the call ends

from hearth import headless  # noqa: E402


class Talky(CC.Adapter):
    name, label = "relay", "Phone"

    def __init__(self):
        self.rooms, self.sent, self.written = {}, [], []

    def send(self, chat, text, *a, **k):
        self.sent.append(text)

    def live(self, chat, text):
        self.written.append(text)


async def goodbye(prompt, *, emit, **k):
    emit("assistant_chunk", content="No worries, catch you later.")
    emit("tool_call", name="end_session", args={})
    emit("done", reason="end_session")
    return 0

headless.run_once = goodbye
ta = Talky()
th = CC.Hub(ta, configured_senders=["phone"])
th.receive("room", "phone", "that's all, thanks", cid="bye1")
for t in list(th.threads):
    t.join(30)
check(f"a goodbye said before end_session is the reply, not the couldn't-get-a-response line ({ta.sent})",
      ta.sent and ta.sent[-1].startswith("No worries, catch you later.") and "couldn't get a response" not in " ".join(ta.sent))

# ------------------------------------------------------------ approvals on a call


class Adapter(CC.Adapter):
    name, label = "relay", "Phone"

    def __init__(self):
        self.rooms = {}

    def send(self, chat, text, **k):
        pass


hub = CC.Hub(Adapter(), configured_senders=["phone"])
CC.is_allowed = lambda *a, **k: True
for said, want in (("Great. How fast will that be?", "aside"), ("Yes, but use the other folder.", "aside"),
                   ("No, use the other folder.", "aside"), ("yes", "answered")):
    run = {"question": threading.Event(), "tap": False, "steer": [], "answer": None}
    hub._busy["call1"] = run
    got = hub.receive("room", "phone", said, cid="call1", spoken=True)
    check(f"while a step waits, {said!r} is {want}" + (", not a no" if want == "aside" else ""),
          got == want and (isinstance(run["answer"], dict) and run["answer"]["decision"] == "aside" if want == "aside"
                           else run["answer"] is True))
    hub._busy.pop("call1", None)

ran = []
hub._turn = lambda chat, text, reply_to, run, cid="": ran.append(text)
hub._voice_approvals["call2"] = {"asks": {"perm_1": {"name": "run_command", "key": "k", "tap": False, "at": 0}}, "tapped": set()}
got = hub.receive("room", "phone", "what will it delete?", cid="call2", spoken=True)
check(f"after the brain asked aloud, anything else said is a message of its own, as said ({got}, {ran})",
      got == "aside" and ran == ["what will it delete?"])
hub._voice_approvals["call2"] = {"asks": {"perm_2": {"name": "run_command", "key": "k", "tap": True, "at": 0}}, "tapped": set()}
check("and a spoken yes can't allow a step only a tap may", hub.receive("room", "phone", "yes", cid="call2", spoken=True) == "needs_tap")


class Call:
    ended, cid = False, "call3"

    def __init__(self):
        self.did = []

    def ask(self, *a, **k):
        self.did.append("aloud")

    def ask_quietly(self, *a, **k):
        self.did.append("quiet")


relay = RB.Relay.__new__(RB.Relay)
relay.asks, relay.asked, relay.hub = {}, {}, None
relay._push = lambda *a, **k: "m1"
relay.notify = lambda *a, **k: None
c = Call()
relay._call_for = lambda chat: c
relay._needs_tap = lambda chat, cid: False
relay.ask_voice("room", "Run the command?", "perm_9", False)
relay.ask("room", "May it delete old.log?")
check(f"a step the brain asks about in its own words shows quietly; one asked from the card is read out ({c.did})",
      c.did == ["quiet", "aloud"])

# An agent's error reads as plain words from that agent, on the phone and in its notification.
said = [RB.stopped_note("Claude Code", "You've hit your 5-hour limit · resets 5:30pm"),
        RB.stopped_note("Codex", "You've hit your usage limit. Upgrade to Pro or try again at Nov 4th, 2026 2:59 PM."),
        RB.stopped_note("Claude Code", "You've hit your weekly limit · resets 1:30am (Asia/Kolkata)")]
check(f"a usage limit says which limit and when it resets ({said})",
      said == ["Claude Code stopped: its 5-hour limit resets at 5:30 pm",
               "Codex stopped: its usage limit resets on Nov 4th, 2026 2:59 pm",
               "Claude Code stopped: its weekly limit resets at 1:30 am"])

# A session open on the PC that stops on its own error (a usage limit) ends the phone's turn there and then.
tpath = os.path.join(tempfile.mkdtemp(), "s.jsonl")
open(tpath, "w").close()


def fake_deliver(inbox, words):
    with open(tpath, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "origin": {"kind": "peer"}, "isMeta": True, "message": {
            "role": "user", "content": f"Another Claude session sent a message:\n{S.PHONE_MARK} {words}"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "isApiErrorMessage": True, "message": {
            "role": "assistant", "model": "<synthetic>", "stop_reason": "stop_sequence",
            "content": [{"type": "text", "text": "You've hit your 5-hour limit · resets 5:30pm"}]}}) + "\n")
    return True


real_deliver, claude_sessions.deliver = claude_sessions.deliver, fake_deliver
got_ev: list = []
t0 = time.time()
try:
    rc = asyncio.run(asyncio.wait_for(claude_sessions.live({"pid": 1, "pipe": "x", "token": "t", "busy": False}, tpath,
                                                           "hi", lambda k, **f: got_ev.append((k, f))), 30))
except asyncio.TimeoutError:
    rc = None
claude_sessions.deliver = real_deliver
errs = [f.get("message") for k, f in got_ev if k == "error"]
check(f"a live session that stops on a usage limit ends the turn at once with that error ({time.time() - t0:.1f} s, {errs})",
      rc == 1 and bool(errs) and "5-hour limit" in errs[0] and time.time() - t0 < 10)

# What the phone sends into a live session reads as the person's own message, with its sender named.
sent = os.path.join(tempfile.mkdtemp(), "pipe.txt")
open(sent, "w").close()
claude_sessions.deliver({"pipe": sent, "token": "t"}, "Is the build green?")
lines = [json.loads(l) for l in open(sent, encoding="utf-8") if l.strip()]
body = lines[1]["message"]["content"]
check(f"it leads with where it came from and says whose words they are, with no 'Claude session' of its own ({body!r})",
      body.startswith("From your phone:") and "Is the build green?" in body and "their own message" in body
      and "Claude session" not in body and lines[1].get("from") == S.PHONE_FROM)
wrapped = {"type": "user", "origin": {"kind": "peer"}, "message": {"content": "Another Claude session sent a message:\n"
           + body + "\n\nThis came from another Claude session — not typed by your user"}}
old = {"type": "user", "origin": {"kind": "peer"}, "message": {"content": "Another Claude session sent a message:\n"
       "Sent by the user from their phone, through Inglen:\n\nHi there\n\nThis came from another Claude session"}}
check("and it reads back as just their words, as do ones sent before",
      S.from_phone(wrapped) == "Is the build green?" and S.from_phone(old) == "Hi there")

# Who sets how a session runs: open on the PC, its own program (read from the session); run here, the phone.
cfile = os.path.join(tempfile.mkdtemp(), "c.jsonl")
with open(cfile, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "user", "permissionMode": "acceptEdits", "message": {"content": "a"}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": []}}) + "\n")
    f.write(json.dumps({"type": "user", "permissionMode": "auto", "message": {"content": "b"}}) + "\n")
    f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 900000}]}}) + "\n")
xfile = os.path.join(tempfile.mkdtemp(), "x.jsonl")
with open(xfile, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "turn_context", "payload": {"approval_policy": "on-request", "model": "gpt-6.1-sol",
                        "effort": "high", "sandbox_policy": {"type": "danger-full-access"}}}) + "\n")
cs, xs = S.pc_settings("claude-code", cfile), S.pc_settings("codex", xfile)
check(f"a Claude Code session's own mode and model, the newest ones, past a long tool output ({cs})",
      cs.get("pc_mode") == "Auto" and cs.get("pc_model") == "claude-opus-5-5")
check(f"a Codex session's own sandbox, model and effort ({xs})",
      xs.get("pc_mode") == "Full access" and xs.get("pc_model") == "gpt-6.1-sol" and xs.get("pc_effort") == "high")
check("a session Inglen runs is set from the phone", S.control("claude-code", "no-such-session", cfile) == {"control": "here"})
check("the effort picked for a session Inglen runs is passed to Claude Code",
      claude_sessions.args_for(["claude"], "s1", "default", "", "high")[-4:-2] == ["--effort", "high"])

# Files a reply names go to the phone as cards, by the Files tab's rules: the session's own folder or the
# Files tab's places; never a file that holds keys; never one from elsewhere.
proj, elsewhere = tempfile.mkdtemp(prefix="proj-"), tempfile.mkdtemp(prefix="elsewhere-")
os.makedirs(os.path.join(proj, "src"))
for rel in ("src/app.py", "notes v2.md", ".env"):
    open(os.path.join(proj, rel), "w").write("x = 1\n")
open(os.path.join(elsewhere, "far.txt"), "w").write("far\n")
reply = ("Fixed it in [app.py](src/app.py#L12), see [the notes](<notes v2.md>) and [keys](.env); "
         f"the old copy is at {os.path.join(elsewhere, 'far.txt')}.")
cards = RB.session_files(reply, proj)
names = [c["name"] for c in cards]
check(f"a reply's links to its own files become cards, relative to its folder, line numbers dropped ({names})",
      names == ["app.py", "notes v2.md"])
check("a file that holds keys, or one outside the places the Files tab serves, is never carded",
      ".env" not in names and "far.txt" not in names and __import__("hearth.relay_media", fromlist=["card"]).card(os.path.join(proj, ".env")) is None)
ws_file = os.path.join(os.environ["HEARTH_WORKSPACE"], "report.txt")
open(ws_file, "w").write("done\n")
from hearth.bridge_status import files_in  # noqa: E402
check("Inglen's own reply naming a file in its workspace still sends it; one elsewhere doesn't",
      files_in(f"Saved to {ws_file} and {os.path.join(elsewhere, 'far.txt')}", 10 ** 8) == [ws_file])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all phone session checks passed")
