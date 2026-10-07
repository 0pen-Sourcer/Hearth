"""Watch a team of agents build — spawn several Hearth subagents and watch them
work live, side by side, in terminal panes.

Opt-in: nothing here runs unless the model calls the `launch_team` tool (which
decomposes a task into members), or code calls launch_team() directly. The
agents run IN-PROCESS via subagents.spawn_subagent(mode='background'), each
writing a live JSONL transcript; each pane just live-TAILS one agent's
transcript and pretty-prints it. So you see every agent think, call tools, and
finish in real time — the Hearth-native version of a tmux multi-agent setup.

Panes: Windows Terminal split-panes (wt.exe) on Windows; tmux on Linux/macOS.
If neither is available, the agents still run — you just read their transcripts
instead of watching panes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Live panes per member, for a team started from the terminal. In the window
# the Team room shows them, so no terminal windows open over it.
PANES = False
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_C = {
    "dim": "\033[2m", "reset": "\033[0m", "brand": "\033[38;5;209m",
    "lav": "\033[38;5;223m", "ok": "\033[38;5;42m", "tool": "\033[38;5;180m",
    "warn": "\033[38;5;203m", "bold": "\033[1m",
}


# --------------------------------------------------------------- pane viewer
def _render_entry(e: Dict[str, Any]) -> Optional[str]:
    """Turn one transcript JSONL entry into a pretty colored line (or None)."""
    C = _C
    k = e.get("kind")
    if k == "start":
        # The task only: a team member's brief starts with who else is on the team.
        task = (e.get("prompt") or "").strip()
        if task.startswith("You are ") and "\n\n" in task:
            task = task.split("\n\n", 1)[1]
        who = e.get("name") or e.get("persona") or "agent"
        return (f"{C['brand']}{C['bold']}▶ {who}{C['reset']}  {C['dim']}{e.get('persona', '')}{C['reset']}\n"
                f"{C['dim']}{task[:240]}{C['reset']}")
    if k == "route":
        return f"{C['dim']}· on {e.get('model') or e.get('runtime') or 'its own model'}{C['reset']}" if e.get("model") or e.get("runtime") else None
    if k == "assistant_tool_call":
        from .subagents import _pointed_at
        lines: List[str] = []
        txt = (e.get("content") or "").strip()
        if txt:
            lines.append(f"{C['lav']}{txt}{C['reset']}")
        for c in (e.get("calls") or []):
            # What the call is pointed at, as the room says it, not its raw arguments.
            what = _pointed_at(c.get("args"))
            lines.append(f"{C['tool']}  › {c.get('name')}{C['reset']}" + (f"  {C['dim']}{what}{C['reset']}" if what else ""))
        return "\n".join(lines) if lines else None
    if k in ("tool_result", "result"):
        r = " ".join(str(e.get("preview") or e.get("result") or e.get("content") or "").split())
        return f"{C['dim']}    ↳ {r[:120]}{C['reset']}" if r else None
    if k == "sent":
        return f"{C['lav']}  → {e.get('to', '')}: {C['reset']}{str(e.get('text') or '')[:200]}"
    if k == "told":
        return f"{C['lav']}  ← {e.get('from', '')}: {C['reset']}{str(e.get('text') or '')[:200]}"
    if k in ("assistant_final", "assistant", "assistant_text", "final"):
        t = (e.get("text") or e.get("content") or "").strip()
        return f"\n{C['reset']}{t}\n" if t else None
    if k == "end":
        # The subagent loop ends with its result: ok, plus error when it failed.
        if e.get("ok") is False or e.get("error"):
            why = str(e.get("error") or "failed").replace("\n", " ")[:160]
            return f"{C['warn']}{C['bold']}■ failed{C['reset']} {C['dim']}{why}{C['reset']}"
        return f"{C['ok']}{C['bold']}■ done{C['reset']}"
    return None


def watch(path: str, title: str = "") -> int:
    """Live-tail a subagent transcript JSONL and pretty-print until it ends."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    hdr = title or os.path.basename(path)
    print(f"{_C['brand']}{_C['bold']}╭─ {hdr} ─╮{_C['reset']}\n", flush=True)
    pos = 0
    ended = False
    idle = 0.0
    while not ended:
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    idle = 0.0
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        out = _render_entry(e)
                        if out:
                            print(out, flush=True)
                        if e.get("kind") == "end":
                            ended = True
                else:
                    idle += 0.3
            else:
                idle += 0.3
                if idle > 30:
                    print(f"{_C['warn']}(waiting for {hdr} to start…){_C['reset']}", flush=True)
                    idle = 0.0
            time.sleep(0.3)
        except KeyboardInterrupt:
            break
    print(f"\n{_C['dim']}— {hdr} finished. Press Enter to close this pane.{_C['reset']}", flush=True)
    try:
        input()
    except Exception:
        pass
    return 0


# ------------------------------------------------------------------- launch
def _py_cmd() -> List[str]:
    """Command prefix to run `-m hearth.team` — handles the frozen bundle
    (sys.executable is Hearth.exe, which re-execs python via a sentinel)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--hearth-run-python"]
    return [sys.executable]


def _watch_cmd(member: Dict[str, Any]) -> List[str]:
    return _py_cmd() + ["-m", "hearth.team", "--watch", member["transcript"],
                        "--title", member["name"]]


def _find_wt() -> Optional[str]:
    """Locate wt.exe — PATH, the bundled copy (source + packaged), or the
    Store install. Hearth ships Windows Terminal in the dist, so a packaged
    user has it even without a system install."""
    c = shutil.which("wt.exe") or shutil.which("wt")
    if c:
        return c
    cands = [os.path.join(_REPO_ROOT, "Windows Terminal", "wt.exe"),
             os.path.join(_REPO_ROOT, "_internal", "Windows Terminal", "wt.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "wt.exe")]
    if getattr(sys, "frozen", False):
        cands.insert(0, os.path.join(os.path.dirname(sys.executable), "_internal", "Windows Terminal", "wt.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _open_wt(spawned: List[Dict[str, Any]]) -> bool:
    """Windows Terminal: one split-pane per agent in a fresh 'Team' tab."""
    wt = _find_wt()
    if not wt:
        return False
    args = [wt, "new-tab", "--title", "Hearth Team", "-d", _REPO_ROOT, "--"] + _watch_cmd(spawned[0])
    for m in spawned[1:]:
        args += [";", "split-pane", "-d", _REPO_ROOT, "--"] + _watch_cmd(m)
    # Even out the layout after the splits.
    args += [";", "move-focus", "first"]
    try:
        subprocess.Popen(args, creationflags=_NO_WINDOW)
        return True
    except Exception:
        return False


def _open_consoles(spawned: List[Dict[str, Any]]) -> bool:
    """Fallback when Windows Terminal isn't available: open each agent's watcher
    in its own separate console window (not as nice as panes, but still live)."""
    ok = False
    for m in spawned:
        try:
            subprocess.Popen(_watch_cmd(m), cwd=_REPO_ROOT,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            ok = True
        except Exception:
            pass
    return ok


def _open_tmux(spawned: List[Dict[str, Any]]) -> bool:
    """tmux: a detached session with one pane per agent, opened in a terminal."""
    tmux = shutil.which("tmux")
    if not tmux:
        return False
    sess = f"hearth_team_{int(time.time())}"
    try:
        def q(c: List[str]) -> str:
            return " ".join(f"'{x}'" if " " in x else x for x in c)
        subprocess.run([tmux, "new-session", "-d", "-s", sess, "-c", _REPO_ROOT,
                        q(_watch_cmd(spawned[0]))], check=True)
        for m in spawned[1:]:
            subprocess.run([tmux, "split-window", "-t", sess, "-c", _REPO_ROOT,
                            q(_watch_cmd(m))], check=True)
        subprocess.run([tmux, "select-layout", "-t", sess, "tiled"], check=False)
        # Open a terminal emulator attached to the session so the user sees it.
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                flag = "--" if term in ("gnome-terminal", "x-terminal-emulator") else "-e"
                subprocess.Popen([term, flag, tmux, "attach", "-t", sess])
                return True
        # No GUI terminal — session still exists; user can `tmux attach -t <sess>`.
        return True
    except Exception:
        return False


def launch_team(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Spawn a team of background subagents and open a live pane per agent.

    members: [{name, persona, prompt}]. persona defaults to 'coder'.
    """
    from . import subagents
    if not members:
        return {"ok": False, "error": "no team members given"}
    # Cap team size: each member is a full background LLM loop hammering the
    # model server + its own pane. A model that returns 20 members would self-DoS
    # an 8GB local box (depth guard only stops NESTED forks, not siblings). Cap is
    # endpoint-aware: a cloud endpoint can handle more parallelism (it costs $ but
    # doesn't serialize), a local single-GPU server can't.
    _base = os.environ.get("LOCAL_API_BASE", "").lower()
    _local = any(h in _base for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1",
                                      "192.168.", "10.", "host.docker.internal"))
    _MAX_TEAM = 4 if _local else 8
    # Every member that does not start is reported with the reason, so the
    # model knows which part of the task has nobody working on it.
    dropped: List[Dict[str, str]] = []
    spawned: List[Dict[str, Any]] = []
    # Each member hears who else is on the team, so they can reach each other.
    roster = [(str(m.get("name") or m.get("persona") or "coder").strip(), " ".join(str(m.get("prompt") or "").split())[:80])
              for m in members if str(m.get("prompt") or "").strip()]

    def with_team(name: str, prompt: str, can_message: bool) -> str:
        others = [f"{n} ({t})" for n, t in roster if n != name]
        if not others:
            return prompt
        how = ("Reach them with message_helper by name when your work touches theirs." if can_message
               else "You can't message them, so say in your result anything they need to know.")
        return f"You are {name}, working at the same time as: {'; '.join(others)}. {how}\n\n{prompt}"

    for m in members:
        persona = (m.get("persona") or "coder").strip()
        name = str(m.get("name") or persona).strip()
        prompt = (m.get("prompt") or "").strip()
        if not prompt:
            dropped.append({"name": name, "reason": "no prompt"})
            continue
        runtime = (m.get("agent") or "").strip().lower()
        if runtime and runtime != "hearth":
            # Another agent CLI, on the user's own install; it does not use the
            # local model, so it does not count against the local team limit.
            from . import external_agents
            r = external_agents.spawn(runtime, with_team(name, prompt, False), name=name,
                                      cwd=(m.get("folder") or "").strip(), task=prompt,
                                      model=str(m.get("model") or "").strip())
            if r.get("ok"):
                spawned.append({"name": name, "persona": r.get("label", runtime), "external": True,
                                "agent_id": r.get("agent_id"),
                                "transcript": str(subagents._transcript_path(r.get("agent_id")))})
            else:
                dropped.append({"name": name, "reason": str(r.get("error") or "did not start")})
            continue
        # One of Hearth's own: on the main brain, this PC, or a provider and model.
        route, err = subagents.route_for(str(m.get("runs_on") or ""), str(m.get("model") or ""))
        if err:
            dropped.append({"name": name, "reason": err})
            continue
        on_pc = route is None and _local or (route is not None and any(h in route[0] for h in ("localhost", "127.0.0.1")))
        if on_pc and sum(1 for x in spawned if x.get("on_pc")) >= 4:
            dropped.append({"name": name, "reason": "this PC's model already has 4 helpers at once"})
            continue
        if not on_pc and sum(1 for x in spawned if not x.get("external") and not x.get("on_pc")) >= _MAX_TEAM:
            dropped.append({"name": name, "reason": f"over the team limit of {_MAX_TEAM}"})
            continue
        r = subagents.spawn_subagent(persona=persona, prompt=with_team(name, prompt, True),
                                     mode="background", name=name, route=route)
        if r.get("ok"):
            spawned.append({"name": name, "persona": persona, "on_pc": on_pc,
                            "agent_id": r.get("agent_id"),
                            "transcript": r.get("transcript_path")})
        else:
            dropped.append({"name": name, "reason": str(r.get("error") or "did not start")})
    if not spawned:
        return {"ok": False, "error": "no agents started", "dropped": dropped}
    opened = False
    if PANES:
        opened = (_open_wt(spawned) or _open_consoles(spawned)) if sys.platform == "win32" else _open_tmux(spawned)
    note = (f"Started {len(spawned)} helpers and opened a live pane for each." if opened else
            f"Started {len(spawned)} helpers. They work in the background and can be watched in the Team "
            f"room; each one's report arrives when it finishes.")
    if dropped:
        note += (" Not started: " + "; ".join(f"{d['name']} ({d['reason']})" for d in dropped)
                 + ". Their part of the task is not being worked on.")
    return {
        "ok": True,
        "spawned": [{"name": s["name"], "persona": s["persona"], "agent_id": s["agent_id"]} for s in spawned],
        "dropped": dropped,
        "watching": opened,
        "note": note,
    }


def _main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hearth.team")
    p.add_argument("--watch", metavar="TRANSCRIPT", help="Live-tail a subagent transcript (pane mode).")
    p.add_argument("--title", default="", help="Pane title.")
    args = p.parse_args(argv)
    if args.watch:
        return watch(args.watch, args.title)
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())


def race(task: str, agents: List[str], folder: str = "", check: str = "") -> Dict[str, Any]:
    """Give one task to several agent programs, each on its own copy of a git
    repository. When all have finished, run `check` in each copy and report
    which passed and how much each changed; the winner is brought in with
    helper_changes and the rest discarded."""
    import threading
    from . import external_agents, helper_changes, subagents, tools
    task = (task or "").strip()
    picked = [str(a).strip().lower() for a in (agents or []) if str(a).strip()]
    if not task or len(picked) < 2:
        return {"ok": False, "error": "give a task and at least two agents to race"}
    folder = os.path.abspath(os.path.expanduser(folder or tools.WORKSPACE))
    inside = shutil.which("git") and subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder,
                                                     capture_output=True, text=True, creationflags=_NO_WINDOW).returncode == 0
    if not inside:
        return {"ok": False, "error": "a race needs a folder inside a git repository, so each agent works on its own copy"}
    if check and tools._is_destructive(check):
        return {"ok": False, "error": f"the check command looks destructive: {tools._is_destructive(check)}"}
    left = {"n": 0}
    lock = threading.Lock()
    runs: List[Dict[str, Any]] = []
    brief = (f"{task}\n\nOther agents are doing this same task separately, each on their own copy; "
             "yours will be compared with theirs" + (f" by running: {check}" if check else "") + ".")

    def finished(agent_id: str, result: Dict[str, Any]) -> None:
        with lock:
            for r in runs:
                if r["agent_id"] == agent_id:
                    r["result"] = result
            left["n"] -= 1
            last = left["n"] == 0
        if last:
            threading.Thread(target=judge, daemon=True, name="race-judge").start()

    def judge() -> None:
        lines = []
        for r in runs:
            res, wt = r.get("result") or {}, helper_changes._load(r["agent_id"]).get("worktree") or {}
            head = f"{r['name']} ({r['agent_id']})"
            if not res.get("ok"):
                lines.append(f"{head}: didn't finish: {str(res.get('error') or '')[:200]}")
                continue
            if wt.get("state") != "waiting":
                lines.append(f"{head}: changed nothing")
                continue
            verdict = ""
            if check:
                try:
                    c = subprocess.run(["powershell", "-NoProfile", "-Command", check] if sys.platform == "win32"
                                       else ["bash", "-lc", check], cwd=wt.get("work") or wt["path"],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                                       creationflags=_NO_WINDOW)
                    tail = " ".join((c.stdout + c.stderr).split())[-300:]
                    verdict = ("check passed" if c.returncode == 0 else f"check failed (exit {c.returncode})") + (f": {tail}" if tail else "")
                except subprocess.TimeoutExpired:
                    verdict = "check ran past 15 minutes"
            lines.append(f"{head}: {wt.get('stat') or str(len(wt.get('files', []))) + ' files'}"
                         + (f"; {verdict}" if verdict else ""))
        text = ("Race finished: " + task[:200] + "\n" + "\n".join("- " + l for l in lines)
                + "\nBring in the one to keep with helper_changes (action bring_in) and discard the others.")
        subagents.enqueue_notification(source="race", name=task[:60], status="completed", result_text=text,
                                       summary=f"Race finished: {len(lines)} agents")

    counts: Dict[str, int] = {}
    for a in picked:
        spec = external_agents.AGENTS.get(a)
        label = spec["label"] if spec else a
        counts[label] = counts.get(label, 0) + 1
        name = label if picked.count(a) == 1 else f"{label} {counts[label]}"
        with lock:
            left["n"] += 1
        r = external_agents.spawn(a, brief, name=name, cwd=folder, task=task, on_done=finished)
        if r.get("ok"):
            with lock:
                runs.append({"agent_id": r["agent_id"], "name": name})
        else:
            with lock:
                left["n"] -= 1
            runs.append({"agent_id": "", "name": name, "error": r.get("error")})
    started = [r for r in runs if r["agent_id"]]
    if len(started) < 2:
        return {"ok": False, "error": "fewer than two agents started: "
                + "; ".join(f"{r['name']}: {r.get('error')}" for r in runs if not r["agent_id"])}
    return {"ok": True, "racing": [{"name": r["name"], "agent_id": r["agent_id"]} for r in started],
            "not_started": [{"name": r["name"], "reason": r.get("error")} for r in runs if not r["agent_id"]],
            "note": "Each works on its own copy. When all are done you get a comparison"
                    + (" with the check's result for each" if check else "") + "."}
