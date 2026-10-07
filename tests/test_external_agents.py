"""Other agent CLIs as Team members.

An external agent runs headless in the background and writes the same
transcript Hearth's own helpers write, so the Team view shows its steps and
its result reaches the main assistant. A stand-in script plays the agent here,
printing Claude Code's stream format, so no real account is needed.

    .\\.venv\\Scripts\\python.exe tests\\test_external_agents.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import subprocess
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import external_agents as xa, subagents, team        # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


REAL_AGENTS = list(xa.AGENTS)          # before the stand-ins below join them
work = tempfile.mkdtemp(prefix="hearth-xa-")
xa._REFUSED = os.path.join(work, "agents_refused.json")
subagents._TRANSCRIPT_DIR = __import__("pathlib").Path(work) / "subagents"
fake = os.path.join(work, "fake_claude.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write("""import sys, json, time
prompt = json.loads(sys.stdin.readline())["message"]["content"][0]["text"]     # stdin stays open
out = lambda o: print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "model": "stand-in"})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.txt"}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": "hello"}]}]}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "The file says hello."}]}})
if "slowly" in prompt:
    time.sleep(1.5)
heard = [json.loads(more)["message"]["content"][0]["text"] for more in ([sys.stdin.readline()] if "slowly" in prompt else [])]
out({"type": "result", "subtype": "success", "is_error": False,
     "result": "The file says hello. (" + prompt.strip() + ")" + "".join(" Heard: " + h for h in heard)})
""")

xa.AGENTS["stand-in"] = {"label": "Stand-in", "exe": ["stand-in"], "args": lambda allow: [fake],
                         "stream": "claude", "takes_messages": True}
xa.AGENTS["stand-in-text"] = {"label": "Text agent", "exe": ["stand-in"], "args": lambda allow: ["-c",
                              "import sys; print('line one'); print('done: ' + sys.stdin.read().strip())"],
                              "stream": "text"}
real_which = xa._which
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)

done = threading.Event()
got = {}
r = xa.spawn("stand-in", "read a.txt", name="Probe", cwd=work,
             on_done=lambda i, res: (got.update(res), done.set()))
check("an installed agent starts", r.get("ok") and r.get("agent_id"))
done.wait(30)
check("its result comes back", got.get("ok") and got.get("text", "").startswith("The file says hello."))
check("the prompt reached it on stdin", "(read a.txt)" in got.get("text", ""))
recs = [json.loads(l) for l in open(subagents._transcript_path(r["agent_id"]), encoding="utf-8")]
kinds = [x["kind"] for x in recs]
check("the transcript has the same shape as a Hearth helper's",
      kinds[:2] == ["start", "route"] and "assistant_tool_call" in kinds and "tool_result" in kinds and kinds[-1] == "end")
check("tool calls are named", any(x.get("calls") == [{"name": "Read", "args": '{"file_path": "a.txt"}'}] for x in recs))
st = {a["id"]: a for a in subagents.team_state()}
check("the Team view shows it done, under the agent's name",
      st[r["agent_id"]]["status"] == "done" and st[r["agent_id"]]["persona"] == "Stand-in" and st[r["agent_id"]]["tools_used"] == 1)

done.clear(); got.clear()
r2 = xa.spawn("stand-in-text", "hi there", cwd=work, on_done=lambda i, res: (got.update(res), done.set()))
done.wait(30)
check("a plain-text agent's output is its result", got.get("ok") and "done: hi there" in got.get("text", ""))

xa.AGENTS["absent"] = {"label": "Absent", "exe": ["hearth-no-such-agent"], "args": lambda allow: [], "stream": "text",
                       "also": [os.path.join(work, "nowhere", "*", "absent.exe")]}
check("an agent that is not installed is refused with a reason",
      "not installed" in xa.spawn("absent", "x", cwd=work).get("error", ""))
del xa.AGENTS["absent"]
fake_app = os.path.join(work, "app", "bin", "v2")
os.makedirs(fake_app, exist_ok=True)
open(os.path.join(fake_app, "app-agent.exe"), "wb").close()
xa.AGENTS["app-agent"] = {"label": "App agent", "exe": ["hearth-no-such-agent"], "args": lambda allow: [],
                          "stream": "text", "also": [os.path.join(work, "app", "bin", "*", "app-agent.exe")]}
check("an agent CLI kept inside its desktop app is found off PATH",
      xa.find("app-agent") == os.path.join(fake_app, "app-agent.exe"))
del xa.AGENTS["app-agent"]
check("an unknown agent is refused", not xa.spawn("nope", "x").get("ok"))

# launch_team routes a member with an agent to it, outside the local team limit
calls = []
xa_spawn = xa.spawn
xa.spawn = lambda *a, **k: (calls.append((a, k)), {"ok": True, "agent_id": "sub_1_x", "label": "Stand-in"})[1]
team._open_wt = team._open_consoles = team._open_tmux = lambda spawned: False
res = team.launch_team([{"name": "Ext", "agent": "stand-in", "prompt": "do it", "folder": work}])
check("launch_team hands an agent member to that agent", res.get("ok") and calls and calls[0][0][:2] == ("stand-in", "do it"))
xa.spawn = xa_spawn

# delegate: one task, the agent's answer relayed in the same turn
from hearth import tools                                            # noqa: E402
out = tools._delegate({"agent": "stand-in", "task": "read a.txt", "folder": work})
check("delegate waits and relays the agent's answer", "Stand-in answered" in out and "The file says hello." in out)
check("delegate without an agent says what is installed", tools._delegate({"task": "x"}).startswith("Error"))
check("the model is told about the agents it can hand work to",
      "delegate" in [t["name"] for t in tools.TOOL_DEFINITIONS])

# A slow agent: the turn moves on, and the answer still arrives later.
xa.AGENTS["stand-in-slow"] = {"label": "Slow agent", "exe": ["stand-in"], "args": lambda allow: ["-c",
                              "import time, sys; sys.stdin.read(); time.sleep(1.5); print('late answer')"],
                              "stream": "text"}
late = []
subagents._BG_COMPLETION_CB = lambda i, res: late.append(res)
tools._DELEGATE_WAIT_S = 0.3
out = tools._delegate({"agent": "stand-in-slow", "task": "x", "folder": work})
check("a slow agent does not hold the turn", "still working" in out)
t0 = time.time()
while not late and time.time() - t0 < 20:
    time.sleep(0.1)
check("its answer arrives afterwards like a background helper's", late and "late answer" in late[0].get("text", ""))

# A helper can be stopped part-way, along with whatever its program started.
xa.AGENTS["stand-in-stuck"] = {"label": "Stuck agent", "exe": ["stand-in"], "args": lambda allow: ["-c",
                               "import subprocess, sys, time; sys.stdin.read(); "
                               "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)']); "
                               "print(c.pid, flush=True); time.sleep(90)"],
                               "stream": "text"}
stuck_done, stuck = threading.Event(), {}
r = xa.spawn("stand-in-stuck", "x", cwd=work, on_done=lambda i, res: (stuck.update(res), stuck_done.set()))
time.sleep(1.5)
t0 = time.time()
said = subagents.stop_subagent(r["agent_id"])
stuck_done.wait(15)
check(f"a working helper can be stopped {said}", said.get("ok") and stuck_done.is_set() and time.time() - t0 < 10)
check(f"it ends as stopped, not failed {stuck.get('error')}", stuck.get("stopped") is True)
row = next((a for a in subagents.team_state() if a["id"] == r["agent_id"]), {})
check(f"the Team view shows it as stopped ({row.get('status')})", row.get("status") == "stopped")
pids = [ln for ln in str(stuck.get("text") or "").split() if ln.isdigit()]
if pids and os.name == "nt":
    alive = pids[0] in subprocess.run(["tasklist", "/FI", f"PID eq {pids[0]}"], capture_output=True, text=True).stdout
    check("what its program started is stopped too", not alive)
check("stopping one that already finished says so", not subagents.stop_subagent(r["agent_id"]).get("ok"))
with subagents._BG_RESULTS_LOCK:
    subagents._BG_RUNNING.add("sub_own_helper")
check("one of Hearth's own is stopped before its next step",
      subagents.stop_subagent("sub_own_helper").get("ok") and subagents._stop_asked("sub_own_helper"))

# npm installs CLIs as .cmd shims; the prompt must never go through cmd.exe.
shim_dir = tempfile.mkdtemp(prefix="hearth-shim-")
os.makedirs(os.path.join(shim_dir, "node_modules", "g", "dist"))
open(os.path.join(shim_dir, "node_modules", "g", "dist", "index.js"), "w").close()
shim = os.path.join(shim_dir, "g.cmd")
with open(shim, "w") as f:
    f.write('@IF EXIST "%~dp0\\node.exe" (\n  "%~dp0\\node.exe"  "%~dp0\\node_modules\\g\\dist\\index.js" %*\n)'
            ' ELSE (\n  node  "%~dp0\\node_modules\\g\\dist\\index.js" %*\n)\n')
argv = xa._direct_argv(shim)
if sys.platform == "win32":
    check("an npm shim runs its script directly, not through cmd.exe",
          argv[-1].endswith("index.js") and not argv[0].lower().endswith(".cmd"))
    xa.AGENTS["stand-in-shim"] = {"label": "Shim", "exe": ["stand-in-shim"], "args": lambda allow: ["-p"],
                                  "stream": "text", "prompt_arg": True}
    bad = os.path.join(shim_dir, "opaque.cmd")
    open(bad, "w").write("@echo off\r\nsomething %*\r\n")
    xa._which = lambda names: bad if names == ["stand-in-shim"] else (sys.executable if names == ["stand-in"] else real_which(names))
    r = xa.spawn("stand-in-shim", "hi & del x", cwd=work)
    check("a shim it can't see through is refused rather than given a prompt", not r.get("ok"))

# An agent that reads messages while it works can be told something part-way.
told_done, told_got = threading.Event(), {}
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)
r = xa.spawn("stand-in", "read it slowly", cwd=work, on_done=lambda i, res: (told_got.update(res), told_done.set()))
time.sleep(0.5)
check("an agent that reads messages while it works can be told something",
      subagents.can_tell(r["agent_id"]) and subagents.tell_subagent(r["agent_id"], "skip the footnotes").get("ok"))
told_done.wait(30)
check(f"and it acts on it before it finishes {told_got.get('text')!r}", "skip the footnotes" in told_got.get("text", ""))
check("once it has finished it can't be told any more", not subagents.can_tell(r["agent_id"]))
r2 = xa.spawn("stand-in-text", "hi", cwd=work)
check("an agent that takes one prompt and runs to the end can't be told",
      not subagents.can_tell(r2["agent_id"]))

# With an agent program as the brain and no local model to run helpers on,
# a helper runs on that agent, briefed with its role.
from hearth import claude_brain as cb                                # noqa: E402
from hearth import headless as hl                                    # noqa: E402

os.environ["LOCAL_API_BASE"] = "http://127.0.0.1:9/v1"
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)
real_current = cb.current
cb.current = lambda: "stand-in"
r = subagents.spawn_subagent("researcher", "find the tallest building", mode="sync")
check("a helper runs on the agent brain when no local model is serving",
      r.get("ok") and "tallest building" in r.get("text", "") and r.get("persona") == "researcher")
check("and is told its role", "helper" in r.get("text", "") and "researcher" in r.get("text", "").lower())
real_reach, real_has = hl.port_reachable, subagents._local_server_has_model
hl.port_reachable, subagents._local_server_has_model = (lambda *a, **k: True), (lambda base: True)
check("with a local model serving, helpers stay on it", subagents._agent_for_helpers() is None)
hl.port_reachable, subagents._local_server_has_model = real_reach, real_has
cb.current = lambda: None
check("with a model endpoint as the brain, nothing changes", subagents._agent_for_helpers() is None)
cb.current = real_current

# A newer version of an installed agent CLI is noticed, and only then.
xa._UPDATES_CACHE = os.path.join(work, "agent_updates.json")
real = (xa.find, xa._from_npm, xa.installed_version, xa.latest_version)
xa.find = lambda aid: "x" if aid in ("claude-code", "gemini-cli", "codex") else None
xa._from_npm = lambda aid: aid != "codex"                  # Codex here came with its app
xa.installed_version = lambda aid: {"claude-code": "2.1.232", "gemini-cli": "0.62.0", "codex": "0.1.0"}[aid]
xa.latest_version = lambda pkg: {"@anthropic-ai/claude-code": "2.1.286", "@google/gemini-cli": "0.62.0"}.get(pkg, "9.9.9")
ups = xa.updates()
check(f"an agent CLI with a newer version out is listed with both versions {ups}",
      ups == [{"id": "claude-code", "label": "Claude Code", "installed": "2.1.232", "latest": "2.1.286"}])
check("versions compare as numbers, not text", xa._version_tuple("2.1.100") > xa._version_tuple("v2.1.99 (Claude Code)"))
xa.find, xa._from_npm, xa.installed_version, xa.latest_version = real
check("an agent Hearth can't update says so", not xa.update("nope").get("ok"))

# An agent its own account turns away: the reason is said plainly, without the
# stack trace, and it is remembered so it isn't offered as if it worked.
xa.AGENTS["turned-away"] = {"label": "Turned away", "exe": ["stand-in"], "stream": "text", "args": lambda allow: [
    "-c", "import sys; print('{'); print('  tierId: 1'); print('}');"
          "print('An unexpected critical error occurred:IneligibleTierError: This client is no longer supported "
          "for individuals. Migrate to Antigravity.'); print('    at setup (file:///x.js:1:2)'); sys.exit(1)"]}
xa._which = lambda names: sys.executable if names == ["stand-in"] else real_which(names)
done, got = threading.Event(), {}
xa.spawn("turned-away", "hello", on_done=lambda i, res: (got.update(res), done.set()))
done.wait(30)
check(f"a refused agent says why in its own words, not a stack trace {got.get('error')!r}",
      got.get("error") == "This client is no longer supported for individuals. Migrate to Antigravity.")
check("and is marked as refused where agents are offered",
      next(a for a in xa.available() if a["id"] == "turned-away")["refused"].startswith("This client is no longer")
      and "not usable now" in xa.describe())
xa.AGENTS["turned-away"]["args"] = lambda allow: ["-c", "print('fine now')"]
done.clear()
xa.spawn("turned-away", "hello", on_done=lambda i, res: done.set())
done.wait(30)
check("a run that works clears the mark", "turned-away" not in xa.refused())
check("a plain failure isn't taken for an account refusing",
      xa.why_failed(["Traceback", "ValueError: bad path", "    at x (y.js:1:2)"]) == "bad path"
      and not any(w in "bad path" for w in xa._ACCOUNT_WORDS))
# Codex and Cursor say what they are doing as they go, so their steps show
# while they work, the way Claude Code's do.
codex_out = os.path.join(work, "fake_codex.py")
with open(codex_out, "w", encoding="utf-8") as f:
    f.write("""import json, sys
sys.stdin.read()
out = lambda o: print(json.dumps(o), flush=True)
print("2026-10-02 INFO starting", flush=True)
out({"type": "thread.started", "thread_id": "th-1"})
out({"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "git status"}})
out({"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "git status", "aggregated_output": "clean", "exit_code": 0}})
out({"type": "item.completed", "item": {"id": "i2", "type": "file_change", "changes": [{"path": "notes.txt", "kind": "update"}]}})
out({"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "The tree is clean."}})
out({"type": "turn.completed", "usage": {}})
""")
cursor_out = os.path.join(work, "fake_cursor.py")
with open(cursor_out, "w", encoding="utf-8") as f:
    f.write("""import json, sys
out = lambda o: print(json.dumps(o), flush=True)
if "limit" in sys.argv[-1]:
    print("ActionRequiredError: You've hit your usage limit Get Cursor Pro for more Agent usage.", flush=True)
    sys.exit(1)
out({"type": "system", "subtype": "init", "session_id": "s"})
out({"type": "tool_call", "subtype": "started", "call_id": "c1", "tool_call": {"readToolCall": {"args": {"path": "docs/SPEC.md"}}}})
out({"type": "tool_call", "subtype": "completed", "call_id": "c1", "tool_call": {"readToolCall": {"args": {"path": "docs/SPEC.md"}, "result": {"success": {"content": "the spec"}}}}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "The spec is short."})
""")
check("a Codex helper runs commands of its own only on Auto",
      "features.shell_tool=false" in xa._codex_cmd("edits") and "features.shell_tool=false" not in xa._codex_cmd("auto"))
xa.AGENTS["stand-in-codex"] = {"label": "Codex stand-in", "exe": ["stand-in"], "stream": "codex",
                               "args": lambda allow: [codex_out]}
xa.AGENTS["stand-in-cursor"] = {"label": "Cursor stand-in", "exe": ["stand-in"], "stream": "cursor",
                                "args": lambda allow: [cursor_out], "prompt_arg": True}


def run_agent(aid, prompt):
    done, got = threading.Event(), {}
    r = xa.spawn(aid, prompt, on_done=lambda i, res: (got.update(res), done.set()))
    done.wait(30)
    rows = [json.loads(l) for l in open(got["transcript_path"], encoding="utf-8")]
    return r, got, rows


r, got, rows = run_agent("stand-in-codex", "check the tree")
calls = [(c["name"], c["args"]) for row in rows if row["kind"] == "assistant_tool_call" for c in row["calls"]]
check(f"Codex's steps are written down as it works {calls}",
      calls == [("run_command", '{"command": "git status"}'), ("edit_file", '{"path": "notes.txt"}')]
      and [row["preview"] for row in rows if row["kind"] == "tool_result"] == ["clean", "update: notes.txt"])
check("and its last message is the result, not its log lines", got["ok"] and got["text"] == "The tree is clean." and got["turns"] == 2)
state = {a["id"]: a for a in subagents.team_state()}[r["agent_id"]]
check("the Team view counts its steps", state["tools_used"] == 2 and state["status"] == "done")

r, got, rows = run_agent("stand-in-cursor", "read the spec")
calls = [(c["name"], c["args"]) for row in rows if row["kind"] == "assistant_tool_call" for c in row["calls"]]
check(f"Cursor's steps are written down the same way {calls}",
      calls == [("read_file", '{"path": "docs/SPEC.md"}')] and got["ok"] and got["text"] == "The spec is short.")
r, got, rows = run_agent("stand-in-cursor", "over the limit")
check(f"a Cursor run that never starts says why {got.get('error')!r}",
      not got["ok"] and got["error"].startswith("You've hit your usage limit") and "stand-in-cursor" in xa.refused())
check("the real Codex and Cursor are run in their step-by-step modes",
      "--json" in xa.AGENTS["codex"]["args"]("edits") and "--full-auto" not in xa.AGENTS["codex"]["args"]("auto")
      and xa.AGENTS["codex"]["stream"] == "codex"
      and "stream-json" in xa.AGENTS["cursor-agent"]["args"]("edits") and xa.AGENTS["cursor-agent"]["stream"] == "cursor")

# Setting an agent up: whether it is signed in is asked of the agent itself.
says = os.path.join(work, "signed.txt")
open(says, "w").write("Not logged in")
xa.SETUP["stand-in"] = {"login": ["login"], "status": ["-c", f"print(open({says!r}).read())"],
                        "signed": lambda out: "logged in as" in out}
xa.SETUP["absent"] = {"needs": "Get the desktop app first.", "login": ["login"], "status": ["status"], "signed": lambda out: False}
xa.AGENTS["absent"] = {"label": "Absent", "exe": ["hearth-no-such-agent"], "args": lambda allow: [], "stream": "text"}
check("an installed agent that is signed out says so", xa.signed_in("stand-in") is False)
open(says, "w").write("Logged in as someone@example.com")
check("the answer is kept for a while, and asked again when it matters",
      xa.signed_in("stand-in") is False and xa.signed_in("stand-in", fresh=True) is True)
check("one that isn't installed has no answer", xa.signed_in("absent") is None)
state = {a["id"]: a for a in xa.setup_state()}
check("the setup list says which are here and which are signed in",
      state["stand-in"]["installed"] and state["stand-in"]["signed_in"] is True and not state["absent"]["installed"])
check("installing one that is already here does nothing", xa.install("stand-in") == {"ok": True, "already": True})
check("one with no installer for this system says what to get instead",
      xa.install("absent") == {"ok": False, "error": "Get the desktop app first."})
check("sign-in needs it installed first", not xa.sign_in("absent")["ok"])
check("the real three use their makers' installers and their own status commands",
      "claude.ai/install" in xa.SETUP["claude-code"]["win"] and "cursor.com/install" in xa.SETUP["cursor-agent"]["win"]
      and xa.SETUP["codex"]["status"] == ["login", "status"]
      and xa.SETUP["claude-code"]["signed"]('{\n  "loggedin": true,\n') and not xa.SETUP["claude-code"]["signed"]('{"loggedin": false}')
      and xa.SETUP["codex"]["signed"]("logged in using chatgpt") and not xa.SETUP["codex"]["signed"]("not logged in"))

# Terminal colour codes in an agent's output don't reach its result.
xa.AGENTS["turned-away"]["args"] = lambda allow: ["-c", "import sys; print(chr(27) + '[91m' + chr(27) + '[1mError: ' + chr(27) + '[0mNo endpoints found that support tool use.'); sys.exit(1)"]
done.clear(); got.clear()
xa.spawn("turned-away", "hello", on_done=lambda i, res: (got.update(res), done.set()))
done.wait(30)
check(f"an error printed in colour reads as plain words {got.get('error')!r}",
      got.get("error") == "No endpoints found that support tool use.")

# Every agent Hearth can run that isn't here comes with a way to get it.
check("every agent has a page to get it from", not [aid for aid in REAL_AGENTS if aid not in xa.SITES])
check("an agent whose desktop app is here but whose command line isn't is told apart",
      "app" in xa.AGENTS["opencode"] and all("has_app" in a for a in xa.missing()))
check("the three with a full setup say so, and the rest point at an installer or their page",
      xa.how_to_get("claude-code") == "setup" and xa.how_to_get("aider") == "site"
      and xa.how_to_get("opencode") in ("install", "site"))
real_avail = xa.available
xa.available = lambda: [{"id": "claude-code"}]
gone = {a["id"]: a for a in xa.missing()}
check("what isn't on the PC is listed with its page", "claude-code" not in gone and gone["goose"]["site"] == "https://goose-docs.ai"
      and gone["codex"]["get"] == "setup")
xa.available = real_avail

gem = xa.AGENTS["gemini-cli"]["args"]
check("Gemini CLI is run trusting its folder, with the approval level the switch allows",
      gem("auto") == ["--skip-trust", "--approval-mode=yolo", "-p"]
      and gem("edits") == ["--skip-trust", "--approval-mode=auto_edit", "-p"])

# The sign-in window Hearth opens is watched, so a closed one is noticed.
_find, _argv, _which = xa.find, xa._direct_argv, xa.shutil.which
xa.find = lambda aid: "stand-in"
xa._direct_argv = lambda exe: [sys.executable, "-c", "import time; time.sleep(1.5)"]
xa.shutil.which = lambda name: None if name.startswith("wt") else _which(name)
_r = xa.sign_in("claude-code")
_open_now = xa.signing("claude-code")
time.sleep(2.5)
check(f"the sign-in window is known to be open, then known to be closed {_r}",
      _r.get("ok") and _r.get("paste") == "right-click" and _open_now is True and xa.signing("claude-code") is False)
xa.find, xa._direct_argv, xa.shutil.which = _find, _argv, _which

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all external agent checks passed")
